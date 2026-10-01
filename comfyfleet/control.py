"""Create, start, stop, and list workflow instances.

The control HTTP API calls these functions and does not reimplement mounts,
naming, the operator workflow copy, or port assignment. ``authorize`` is the
fail-closed check on that HTTP path. A local CLI call is not an HTTP request.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from comfyfleet.auth import AuthError, http_auth_state
from comfyfleet.docker import DockerCLI, build_create_args
from comfyfleet.errors import FleetError
from comfyfleet.gpu import Gpu, select_gpus
from comfyfleet.launch import LaunchConfig, launch_from_json, parse_launch
from comfyfleet.naming import instance_name_from_workflow, is_instance_name
from comfyfleet.paths import DEFAULT_IMAGE, MODEL_SUBDIRS, FleetLayout
from comfyfleet.ports import choose_port, make_port_in_use
from comfyfleet.workflow import load_operator_workflow

METADATA_SCHEMA = 1


@dataclass
class Instance:
    name: str
    port: int
    gpus: list[int]
    image: str
    workflow_host_path: str
    workflow_source: str
    created_at: str
    launch: LaunchConfig = field(default_factory=LaunchConfig)

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["schema"] = METADATA_SCHEMA
        payload["launch"] = self.launch.to_json()
        return payload

    @classmethod
    def from_json(cls, payload: dict, path: Path) -> "Instance":
        try:
            return cls(
                name=str(payload["name"]),
                port=int(payload["port"]),
                gpus=[int(item) for item in payload["gpus"]],
                image=str(payload["image"]),
                workflow_host_path=str(payload["workflow_host_path"]),
                workflow_source=str(payload.get("workflow_source", "")),
                created_at=str(payload.get("created_at", "")),
                launch=launch_from_json(payload.get("launch"), path=str(path)),
            )
        except FleetError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise FleetError(f"instance metadata is invalid: {path}") from exc


@dataclass
class ActionResult:
    instance: Instance
    started: bool
    warning: str | None = None


def authorize(action: str) -> None:
    """Allow a control action, or fail closed on an unauthenticated HTTP request.

    Outside an HTTP request (the host CLI, or ``docker exec`` inside the
    manager) this only checks that ``action`` is known. That process is
    local: it does not read the session cookie. The HTTP server grants
    this check only after a valid session cookie or Bearer token, and
    refuses to start when ``COMFYFLEET_PASSWORD`` is missing.
    """

    if action not in {"create", "start", "stop", "force-stop", "delete", "terminal", "restart", "list"}:
        raise FleetError(f"unknown control action {action!r}")
    if http_auth_state() is False:
        raise AuthError("unauthorized")


def create_instance(
    workflow: Path,
    *,
    layout: FleetLayout,
    docker: DockerCLI,
    gpus: list[Gpu],
    gpu: str | None = None,
    gpus_spec: str | None = None,
    interactive: bool = False,
    prompt=None,
    image: str = DEFAULT_IMAGE,
    start: bool = False,
    force: bool = False,
    port_in_use=None,
    max_concurrent: int | None = None,
    use_env_limit: bool = False,
    launch: LaunchConfig | None = None,
) -> ActionResult:
    authorize("create")
    launch = _canonicalize_launch(launch)
    image = resolve_instance_image(image)
    source = Path(workflow)
    load_operator_workflow(source)
    name = instance_name_from_workflow(source)
    _require_name(name)
    previous = _load_if_present(layout, name)
    container_status = docker.status(name)
    if previous is not None or container_status is not None:
        if not force:
            raise FleetError(
                f"instance {name!r} already exists (status: {container_status or 'metadata only'}). "
                "Refusing to overwrite. Stop it and re-run with --force to replace a stopped instance. "
                "Changing the GPU set requires recreate (--force), not an in-place edit."
            )
        if container_status == "running":
            raise FleetError(
                f"instance {name!r} is running. Stop it before --force replace. "
                "A running container is never overwritten."
            )
    selected = select_gpus(
        gpus,
        gpu=gpu,
        gpus_spec=gpus_spec,
        interactive=interactive,
        prompt=prompt,
    )
    if force and container_status is not None:
        docker.remove(name)
    reserved = _reserved_ports(layout, exclude=name)
    in_use = _port_in_use(port_in_use, docker)
    port = choose_port(reserved, in_use=in_use)
    _prepare_dirs(layout, name)
    dest = layout.workflow_file(name)
    _copy_workflow(source, dest)
    instance = Instance(
        name=name,
        port=port,
        gpus=selected,
        image=image,
        workflow_host_path=str(dest),
        workflow_source=str(source.resolve()),
        created_at=_now(),
        launch=launch,
    )
    _write_metadata(layout, instance)
    try:
        docker.create(_create_args(layout, instance))
    except Exception:
        if previous is not None:
            _write_metadata(layout, previous)
        else:
            _remove_metadata(layout, name)
        raise
    warning = None
    started = False
    if start:
        started_result = start_instance(
            name,
            layout=layout,
            docker=docker,
            gpus=gpus,
            port_in_use=port_in_use,
            max_concurrent=max_concurrent,
            use_env_limit=use_env_limit,
        )
        warning = started_result.warning
        started = True
        instance = started_result.instance
    return ActionResult(instance=instance, started=started, warning=warning)


def start_instance(
    name: str,
    *,
    layout: FleetLayout,
    docker: DockerCLI,
    gpus: list[Gpu],
    port_in_use=None,
    max_concurrent: int | None = None,
    use_env_limit: bool = False,
) -> ActionResult:
    authorize("start")
    _require_name(name)
    if not gpus:
        raise FleetError(
            "nvidia-smi reported no usable GPU. Phase 1 will not start this instance."
        )
    instance = _require_instance(layout, name)
    known = {gpu.index for gpu in gpus}
    missing = [index for index in instance.gpus if index not in known]
    if missing:
        raise FleetError(
            f"GPU(s) {missing} were assigned at create but nvidia-smi no longer lists them. "
            "Changing GPUs requires recreate: stop the instance and run "
            f"comfyfleet create --workflow {instance.workflow_host_path} --force --gpus ..."
        )
    status = docker.status(name)
    if status == "running":
        return ActionResult(instance=instance, started=True, warning=None)
    in_use = _port_in_use(port_in_use, docker)
    reserved = _reserved_ports(layout, exclude=name)
    port = choose_port(reserved, preferred=instance.port, in_use=in_use)
    if status is None or port != instance.port:
        if status is not None:
            docker.remove(name)
        instance.port = port
        _write_metadata(layout, instance)
        docker.create(_create_args(layout, instance))
    limit = _resolve_limit(max_concurrent, use_env=use_env_limit)
    running = [item for item in docker.running_names() if item != name]
    running_after = len(running) + 1
    warning = _concurrency_warning(running_after, gpu_count=len(gpus), limit=limit)
    docker.update_restart(name, "unless-stopped")
    docker.start(name)
    return ActionResult(instance=instance, started=True, warning=warning)


def stop_instance(name: str, *, layout: FleetLayout, docker: DockerCLI) -> Instance:
    authorize("stop")
    _require_name(name)
    instance = _require_instance(layout, name)
    status = docker.status(name)
    if status is None:
        raise FleetError(
            f"instance {name!r} has metadata but no container. Nothing to stop."
        )
    if status != "running":
        return instance
    docker.stop(name)
    return instance


def force_stop_instance(name: str, *, layout: FleetLayout, docker: DockerCLI) -> Instance:
    """SIGKILL the instance container. This is not ``docker stop``."""

    authorize("force-stop")
    _require_name(name)
    instance = _require_instance(layout, name)
    status = docker.status(name)
    if status is None:
        raise FleetError(
            f"instance {name!r} has metadata but no container. Nothing to force-stop."
        )
    if status != "running":
        return instance
    docker.kill(name)
    return instance


def delete_instance(name: str, *, layout: FleetLayout, docker: DockerCLI) -> Instance:
    """Force-stop and remove this instance container, then drop its fleet record.

    Host files (workflow, input, output, custom nodes) are left in place.
    Only the named instance is removed. Other containers are not touched.
    """

    authorize("delete")
    _require_name(name)
    instance = _require_instance(layout, name)
    status = docker.status(name)
    if status == "running":
        docker.kill(name)
    if docker.status(name) is not None:
        docker.remove(name)
    _remove_metadata(layout, name)
    return instance


def terminal_argv(
    name: str,
    *,
    layout: FleetLayout,
    docker: DockerCLI,
    argv_for=None,
) -> list[str]:
    """Argv for a shell in one running instance. The browser does not supply it."""

    from comfyfleet.terminal import terminal_exec_argv

    authorize("terminal")
    _require_name(name)
    instance = _require_instance(layout, name)
    if docker.status(instance.name) != "running":
        raise FleetError(
            f"instance {instance.name!r} is not running. Start it before opening a shell."
        )
    build = argv_for or terminal_exec_argv
    return list(build(instance.name))


def restart_instance(
    name: str,
    *,
    layout: FleetLayout,
    docker: DockerCLI,
    gpus: list[Gpu],
    port_in_use=None,
    max_concurrent: int | None = None,
    use_env_limit: bool = False,
) -> ActionResult:
    authorize("restart")
    status = docker.status(name)
    if status == "running":
        stop_instance(name, layout=layout, docker=docker)
    return start_instance(
        name,
        layout=layout,
        docker=docker,
        gpus=gpus,
        port_in_use=port_in_use,
        max_concurrent=max_concurrent,
        use_env_limit=use_env_limit,
    )


def list_instances(layout: FleetLayout, docker: DockerCLI) -> list[tuple[Instance, str]]:
    authorize("list")
    rows: list[tuple[Instance, str]] = []
    files_root = layout.files
    if not files_root.is_dir():
        return rows
    for child in sorted(path for path in files_root.iterdir() if path.is_dir()):
        meta = child / "comfyfleet.json"
        if not meta.is_file():
            continue
        instance = _read_metadata(meta)
        status = docker.status(instance.name) or "missing"
        rows.append((instance, status))
    return rows


def format_list(rows: list[tuple[Instance, str]]) -> str:
    header = ("NAME", "STATUS", "PORT", "URL", "GPUS", "WORKFLOW")
    host = list_url_host()
    body = []
    for instance, status in rows:
        body.append(
            (
                instance.name,
                status,
                str(instance.port),
                f"http://{host}:{instance.port}",
                ",".join(str(index) for index in instance.gpus),
                instance.workflow_host_path,
            )
        )
    widths = [len(column) for column in header]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(cell.ljust(widths[index]) for index, cell in enumerate(header))]
    if not body:
        lines.append("(no instances)")
        return "\n".join(lines)
    for row in body:
        lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(lines)


def _canonicalize_launch(launch: LaunchConfig | None) -> LaunchConfig:
    if launch is None:
        return LaunchConfig()
    return parse_launch(
        vram=launch.vram,
        attention=launch.attention,
        flags=launch.flags,
        reserve_vram=launch.reserve_vram,
        vram_headroom=launch.vram_headroom,
        preview_method=launch.preview_method,
        preview_size=launch.preview_size,
        extra_args=launch.extra_args,
    )


def resolve_instance_image(image: str) -> str:
    """``COMFYFLEET_INSTANCE_IMAGE`` overrides the default instance tag.

    An explicit non-default ``--image`` is kept. The tag must already exist
    on the host engine; the manager does not build it during create.
    """

    if image != DEFAULT_IMAGE:
        return image
    override = os.environ.get("COMFYFLEET_INSTANCE_IMAGE", "").strip()
    return override or DEFAULT_IMAGE


def list_url_host() -> str:
    raw = os.environ.get("COMFYFLEET_PUBLIC_HOST", "").strip()
    if not raw:
        return "0.0.0.0"
    from comfyfleet.public_host import open_host

    return open_host(None, "0.0.0.0", public_host=raw)


def _create_args(layout: FleetLayout, instance: Instance) -> list[str]:
    return build_create_args(
        name=instance.name,
        image=instance.image,
        port=instance.port,
        gpus=instance.gpus,
        models=str(layout.models),
        custom_nodes=str(layout.custom_nodes(instance.name)),
        input_dir=str(layout.input_dir(instance.name)),
        output_dir=str(layout.output_dir(instance.name)),
        temp_dir=str(layout.temp_dir(instance.name)),
        instance_dir=str(layout.instance_dir(instance.name)),
        comfy_args=instance.launch.argv(),
    )


def _prepare_dirs(layout: FleetLayout, name: str) -> None:
    paths = [
        layout.models,
        *[layout.models / sub for sub in MODEL_SUBDIRS],
        layout.custom_nodes(name),
        layout.input_dir(name),
        layout.output_dir(name),
        layout.temp_dir(name),
    ]
    for path in paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FleetError(
                f"cannot create {path}: {exc}. Required host paths: "
                f"{layout.models}, {layout.custom_nodes(name)}, "
                f"{layout.input_dir(name)}, {layout.output_dir(name)}, {layout.temp_dir(name)}."
            ) from exc


def _copy_workflow(source: Path, dest: Path) -> None:
    payload = source.read_bytes()
    temporary = dest.with_suffix(".json.tmp")
    temporary.write_bytes(payload)
    temporary.replace(dest)


def _write_metadata(layout: FleetLayout, instance: Instance) -> None:
    path = layout.metadata_file(instance.name)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(instance.to_json(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _remove_metadata(layout: FleetLayout, name: str) -> None:
    path = layout.metadata_file(name)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _read_metadata(path: Path) -> Instance:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetError(f"cannot read instance metadata {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FleetError(f"instance metadata is invalid: {path}")
    return Instance.from_json(payload, path)


def _load_if_present(layout: FleetLayout, name: str) -> Instance | None:
    path = layout.metadata_file(name)
    if not path.is_file():
        return None
    return _read_metadata(path)


def _require_instance(layout: FleetLayout, name: str) -> Instance:
    instance = _load_if_present(layout, name)
    if instance is None:
        raise FleetError(
            f"no instance named {name!r}. Create one with "
            "comfyfleet create --workflow /path/to/flow.json."
        )
    return instance


def _reserved_ports(layout: FleetLayout, *, exclude: str) -> set[int]:
    reserved: set[int] = set()
    for instance, _status in list_instances(layout, _StatusFreeDocker()):
        if instance.name == exclude:
            continue
        reserved.add(instance.port)
    return reserved


def _require_name(name: str) -> None:
    if not is_instance_name(name):
        raise FleetError(
            f"invalid instance name {name!r}. Names are lowercase [a-z0-9_-], "
            "start with a letter or digit, and are at most 63 characters."
        )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_limit(explicit: int | None, *, use_env: bool) -> int | None:
    if explicit is not None:
        return explicit
    if not use_env:
        return None
    raw = os.environ.get("COMFYFLEET_MAX_CONCURRENT")
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise FleetError("COMFYFLEET_MAX_CONCURRENT must be an integer") from exc
    if value < 1:
        raise FleetError("COMFYFLEET_MAX_CONCURRENT must be >= 1")
    return value


def _concurrency_warning(running_after: int, *, gpu_count: int, limit: int | None) -> str | None:
    if limit is not None and running_after > limit:
        raise FleetError(
            f"refusing to start: {running_after} running instance(s) would exceed "
            f"COMFYFLEET_MAX_CONCURRENT={limit}."
        )
    if running_after > gpu_count:
        return (
            f"starting this instance would run {running_after} ComfyFleet container(s) "
            f"on {gpu_count} GPU(s). Create-many/run-few: stop another instance if the "
            "GPUs are overloaded. This is a warning, not a block."
        )
    return None


def _port_in_use(explicit, docker):
    """Use the caller's probe, or snapshot host listeners and published ports.

    Host CLI and the manager HTTP API both allocate here. A missing callback
    must still skip ports taken outside fleet metadata.
    """

    if explicit is not None:
        return explicit
    return make_port_in_use(docker)


class _StatusFreeDocker:
    """Used only while reading metadata so port reservation does not need Docker."""

    def status(self, _name: str) -> str | None:
        return None
