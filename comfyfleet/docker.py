"""Docker CLI wrapper. Phase 1 uses ``docker create`` / ``start`` / ``stop``."""

from __future__ import annotations

import subprocess

from comfyfleet.errors import FleetError
from comfyfleet.paths import CONTAINER_PORT, WORKFLOW_CONTAINER_PATH


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
) -> list[str]:
    """Arguments after ``docker``. Restart policy is ``no`` until ``start``."""

    gpu_list = ",".join(str(index) for index in gpus)
    return [
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


class DockerCLI:
    def __init__(self, run=None):
        self._run = run or default_run

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
        completed = self._run(["docker", "inspect", "-f", "{{.State.Status}}", name])
        if completed.returncode != 0:
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

    def _check(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = self._run(["docker", *args])
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            shown = " ".join(args[:4])
            raise FleetError(f"docker {shown} failed: {detail}".rstrip())
        return completed


def default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FleetError(
            "docker was not found on PATH. Install Docker and the NVIDIA Container Toolkit."
        ) from exc
