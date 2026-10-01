"""Docker CLI wrapper. Lifecycle uses ``docker create`` / ``start`` / ``stop``.

The manager container talks to the **host** engine through the mounted socket
(``/var/run/docker.sock`` by default). Instance containers are siblings on that
engine. This module does not start a daemon inside the manager.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from comfyfleet.errors import FleetError
from comfyfleet.paths import CONTAINER_PORT, WORKFLOW_CONTAINER_PATH

DEFAULT_SOCKET = "/var/run/docker.sock"
_PUBLISHED_PORT = re.compile(r":(\d+)->")
# Host network namespace, not the manager's. Prints LISTEN tables only.
_HOST_LISTENER_SCRIPT = (
    "import pathlib,sys\n"
    "n=0\n"
    "for name in ('tcp','tcp6'):\n"
    " p=pathlib.Path('/proc/net')/name\n"
    " if p.is_file():\n"
    "  sys.stdout.write(p.read_text())\n"
    "  n+=1\n"
    "sys.exit(0 if n else 1)\n"
)


def build_create_args(
    *,
    name: str,
    image: str,
    port: int,
    gpus: list[int],
    models: str,
    custom_nodes: str,
    input_dir: str,
    output_dir: str,
    temp_dir: str,
    instance_dir: str,
    comfy_args: list[str] | None = None,
) -> list[str]:
    """Arguments after ``docker``. Restart policy is ``no`` until ``start``.

    ``comfy_args`` are appended after the image name so they become
    entrypoint arguments. The entrypoint puts them after
    ``--listen 0.0.0.0`` and ``--port`` (the container port).
    """

    gpu_list = ",".join(str(index) for index in gpus)
    args = [
        "create",
        "--name",
        name,
        "--restart",
        "no",
        "--gpus",
        f"device={gpu_list}",
        "-p",
        f"{port}:{CONTAINER_PORT}",
        "-v",
        f"{models}:/opt/ComfyUI/models",
        "-v",
        f"{custom_nodes}:/opt/ComfyUI/custom_nodes",
        "-v",
        f"{input_dir}:/opt/ComfyUI/input",
        "-v",
        f"{output_dir}:/opt/ComfyUI/output",
        "-v",
        f"{temp_dir}:/opt/ComfyUI/temp",
        "-v",
        f"{instance_dir}:/opt/comfyfleet/instance",
        "-e",
        f"NVIDIA_VISIBLE_DEVICES={gpu_list}",
        "-e",
        f"COMFYFLEET_WORKFLOW_PATH={WORKFLOW_CONTAINER_PATH}",
        "--label",
        "comfyfleet.managed=true",
        "--label",
        f"comfyfleet.name={name}",
        "--label",
        f"comfyfleet.port={port}",
        "--label",
        f"comfyfleet.gpus={gpu_list}",
        image,
    ]
    if comfy_args:
        args.extend(comfy_args)
    return args


class DockerCLI:
    def __init__(self, run=None):
        self._run = run or default_run
        # Injected runners are tests and local fakes. The real client checks
        # the host socket before it asks the engine to do anything.
        self._check_engine = run is None

    def create(self, args: list[str]) -> None:
        self._check(args)

    def start(self, name: str) -> None:
        self._check(["start", name])

    def stop(self, name: str) -> None:
        self._check(["stop", name])

    def remove(self, name: str) -> None:
        self._check(["rm", "-f", name])

    def update_restart(self, name: str, policy: str) -> None:
        self._check(["update", "--restart", policy, name])

    def status(self, name: str) -> str | None:
        self._raise_if_engine_unreachable()
        completed = self._run(["docker", "inspect", "-f", "{{.State.Status}}", name])
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            if container_absent(detail):
                return None
            message = explain_docker_failure(detail)
            if message != detail:
                raise FleetError(message)
            return None
        return (completed.stdout or "").strip() or None

    def running_names(self) -> list[str]:
        completed = self._check(
            [
                "ps",
                "-a",
                "--filter",
                "label=comfyfleet.managed=true",
                "--format",
                "{{.Names}}\t{{.Status}}",
            ]
        )
        names: list[str] = []
        for line in (completed.stdout or "").splitlines():
            if not line.strip():
                continue
            name, _, status = line.partition("\t")
            if status.startswith("Up"):
                names.append(name.strip())
        return names

    def published_host_ports(self) -> set[int]:
        """Host ports published by running (or paused) containers on this engine.

        Stopped containers do not hold the host port. Fleet metadata reserves
        those separately, including ports recorded for stopped instances.
        """

        completed = self._check(
            [
                "ps",
                "--filter",
                "status=running",
                "--filter",
                "status=paused",
                "--filter",
                "status=restarting",
                "--format",
                "{{.Ports}}",
            ]
        )
        return parse_published_ports(completed.stdout or "")

    def host_tcp_tables(self) -> str:
        """``/proc/net/tcp`` and ``tcp6`` from the host network namespace.

        The manager container has its own netns, so a local ``bind()`` or
        ``/proc/net/tcp`` does not see host listeners. A sibling with
        ``--network host`` does: its ``/proc/net/tcp`` is the host stack.
        The entrypoint is replaced so this does not start another UI.
        """

        image = self._manager_image()
        completed = self._check(
            [
                "run",
                "--rm",
                "--network",
                "host",
                "--label",
                "comfyfleet.probe=listeners",
                "--entrypoint",
                "/usr/bin/python3",
                image,
                "-c",
                _HOST_LISTENER_SCRIPT,
            ]
        )
        return completed.stdout or ""

    def _manager_image(self) -> str:
        configured = os.environ.get("COMFYFLEET_MANAGER_IMAGE", "").strip()
        if configured:
            return configured
        try:
            hostname = Path("/etc/hostname").read_text(encoding="utf-8").strip()
        except OSError:
            hostname = ""
        if hostname:
            completed = self._run(["docker", "inspect", "-f", "{{.Config.Image}}", hostname])
            if getattr(completed, "returncode", 1) == 0:
                image = (completed.stdout or "").strip()
                if image and image != "<no value>":
                    return image
        return "comfyfleet-manager:latest"

    def _raise_if_engine_unreachable(self) -> None:
        if not self._check_engine:
            return
        problem = engine_problem()
        if problem:
            raise FleetError(problem)

    def _check(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self._raise_if_engine_unreachable()
        try:
            completed = self._run(["docker", *args])
        except FileNotFoundError as exc:
            raise FleetError(_missing_docker_message()) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            message = explain_docker_failure(detail)
            if message == detail:
                shown = " ".join(args[:4])
                raise FleetError(f"docker {shown} failed: {detail}".rstrip())
            raise FleetError(message)
        return completed


def parse_published_ports(text: str) -> set[int]:
    """Host ports from ``docker ps`` (``0.0.0.0:8189->8188/tcp`` → ``8189``).

    The number before ``->`` is the host port. The number after it is the
    port inside the container and is not a host allocation.
    """

    return {int(match) for match in _PUBLISHED_PORT.findall(text)}


def engine_problem(environ=None, exists=None, access=None) -> str | None:
    """A clear error when the host socket is missing or not usable.

    ``DOCKER_HOST`` of ``tcp://`` or ``ssh://`` skips the unix-socket check.
    Returns None when the default socket exists and is readable and writable.
    """

    env = os.environ if environ is None else environ
    exists = os.path.exists if exists is None else exists
    access = os.access if access is None else access
    configured = env.get("DOCKER_HOST", "").strip()
    if configured.startswith(("tcp://", "ssh://", "http://", "https://")):
        return None
    path = DEFAULT_SOCKET
    if configured.startswith("unix://"):
        path = configured[len("unix://") :] or DEFAULT_SOCKET
    if not exists(path):
        return (
            f"Docker socket {path} is missing. Mount the host engine into the manager "
            "with -v /var/run/docker.sock:/var/run/docker.sock and confirm the host "
            "Docker daemon is running. ComfyFleet creates sibling instance containers "
            "on that engine. It does not start a Docker daemon inside the manager."
        )
    if not access(path, os.R_OK) or not access(path, os.W_OK):
        return (
            f"permission denied on Docker socket {path}. The manager process cannot "
            "use the host engine. Run the manager as root, or as a uid in the host's "
            "docker group (the socket is usually mode 660, group docker). Access to "
            "this socket is root-equivalent on the host. Use it only on a trusted host."
        )
    return None


def explain_docker_failure(detail: str) -> str:
    """Turn common engine errors into operator-facing text. Unknown text is unchanged."""

    text = (detail or "").strip()
    low = text.lower()
    if "permission denied" in low and (
        "docker.sock" in low or "docker.socket" in low or "dial unix" in low
    ):
        return (
            "permission denied on the Docker socket (/var/run/docker.sock). "
            "The manager cannot create, start, or stop sibling containers. "
            "Mount -v /var/run/docker.sock:/var/run/docker.sock and run the manager "
            "as root or as a member of the host docker group. "
            "That socket is root-equivalent on the host. "
            f"Docker said: {text}"
        ).rstrip()
    if (
        "cannot connect to the docker daemon" in low
        or "is the docker daemon running" in low
        or ("no such file or directory" in low and "docker.sock" in low)
        or ("dial unix" in low and "docker.sock" in low)
    ):
        return (
            "cannot reach the Docker engine at /var/run/docker.sock. "
            "Mount the host socket (-v /var/run/docker.sock:/var/run/docker.sock) "
            "and confirm the host daemon is running. The manager does not start its "
            f"own Docker daemon. Docker said: {text}"
        ).rstrip()
    if "nvidia" in low and (
        "could not select device driver" in low
        or "unknown runtime" in low
        or "could not select device" in low
        or "unsatisfiable" in low
        or "nvidia-container" in low
    ):
        return (
            "NVIDIA Container Toolkit is missing or the host engine rejected the GPU "
            "request. Install the toolkit and confirm `docker run --rm --gpus all` "
            f"works on the host. Docker said: {text}"
        ).rstrip()
    return text


def container_absent(detail: str) -> bool:
    low = (detail or "").lower()
    return "no such object" in low or "no such container" in low


def _missing_docker_message() -> str:
    return (
        "docker was not found on PATH. The manager image includes the Docker client. "
        "On a host checkout, install Docker. ComfyFleet talks to the host engine "
        "through /var/run/docker.sock; it does not run a daemon inside the manager. "
        "GPU attach also needs the NVIDIA Container Toolkit on the host."
    )


def default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FleetError(_missing_docker_message()) from exc
