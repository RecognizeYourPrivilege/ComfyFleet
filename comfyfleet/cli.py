"""Command-line entrypoint.

``comfyfleet.control`` is the lifecycle API. ``comfyfleet ui`` (alias
``serve``) exposes it over HTTP and requires ``COMFYFLEET_PASSWORD``.
The host CLI does not use the HTTP session.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from comfyfleet import __version__
from comfyfleet.control import (
    create_instance,
    delete_instance,
    force_stop_instance,
    format_list,
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from comfyfleet.docker import DockerCLI
from comfyfleet.errors import FleetError
from comfyfleet.gpu import detect_gpus
from comfyfleet.http_api import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, serve
from comfyfleet.launch import combine_extra_args, main_argv, parse_launch
from comfyfleet.paths import DEFAULT_CUDA_TAG, DEFAULT_IMAGE, FleetLayout
from comfyfleet.public_host import PUBLIC_HOST_ENV, open_host


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FleetError as exc:
        print(f"comfyfleet: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("comfyfleet: cancelled", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comfyfleet",
        description=(
            "Create many ComfyUI workflow containers and start a few of them. "
            "Create requires an operator workflow JSON. The image has no stock default."
        ),
    )
    parser.add_argument("--version", action="version", version=f"comfyfleet {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a stopped instance from a workflow JSON file")
    create.add_argument(
        "--workflow",
        required=True,
        help="Path to the operator workflow JSON. Required. There is no baked default.",
    )
    create.add_argument("--gpu", help="One GPU index (non-interactive), for example 0")
    create.add_argument("--gpus", help="GPU indices, for example 0,1 or all")
    create.add_argument("--image", default=DEFAULT_IMAGE, help=f"Image ref override (default {DEFAULT_IMAGE})")
    create.add_argument(
        "--cuda-tag",
        choices=["cu130", "cu124"],
        default=None,
        help=(
            "CUDA line for this instance. cu130 needs a host driver that supports "
            "CUDA 13.0. cu124 needs CUDA 12.4. "
            f"Default is COMFYFLEET_CUDA_TAG, or {DEFAULT_CUDA_TAG} when that is unset. "
            "Changing the line on an existing instance requires --force recreate. "
            "start, restart, and launch Apply keep the line from create."
        ),
    )
    create.add_argument(
        "--start",
        action="store_true",
        help="Start this one instance after create. Default is to leave it stopped.",
    )
    create.add_argument(
        "--force",
        action="store_true",
        help="Replace a stopped instance with the same name. Refuses while it is running.",
    )
    create.add_argument(
        "--vram",
        default=None,
        help="VRAM mode: lowvram, novram, or highvram. Omit for stock ComfyUI.",
    )
    create.add_argument(
        "--attention",
        default=None,
        help=(
            "One attention backend: use-pytorch-cross-attention, use-sage-attention, "
            "use-flash-attention, use-split-cross-attention, use-quad-cross-attention, "
            "or use-ck-attention."
        ),
    )
    create.add_argument(
        "--flag",
        action="append",
        default=None,
        help="Repeatable ComfyUI toggle, for example --flag disable-smart-memory.",
    )
    create.add_argument("--reserve-vram", default=None, help="GB reserved for the OS (--reserve-vram).")
    create.add_argument("--vram-headroom", default=None, help="Extra dynamic-VRAM headroom in GB.")
    create.add_argument(
        "--preview-method",
        default=None,
        help="Sampler preview method: auto, latent2rgb, taesd, or none.",
    )
    create.add_argument("--preview-size", default=None, help="Maximum sampler preview size.")
    create.add_argument(
        "--extra-args",
        default="",
        help="Extra main.py arguments, appended last. --listen and --port are removed.",
    )
    create.add_argument(
        "--comfy-extra-args",
        default=None,
        help=(
            "Extra main.py arguments. Appended after --extra-args. "
            "--listen and --port are removed. "
            "If the value starts with -, use --comfy-extra-args=--flag."
        ),
    )
    create.add_argument(
        "--custom-node-git-url",
        action="append",
        default=None,
        dest="custom_node_git_urls",
        help=(
            "HTTPS or SSH git URL to clone into this instance's custom_nodes. "
            "Repeatable. Blank values are ignored."
        ),
    )
    create.add_argument(
        "--custom-nodes-zip",
        default=None,
        help="Zip of custom node packs to extract into this instance's custom_nodes.",
    )
    create.add_argument(
        "--install-missing-from-workflow",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Install custom nodes referenced by this workflow that are not already "
            "present (default: true). Uses Manager's git-URL install for those nodes only."
        ),
    )
    create.set_defaults(func=_cmd_create)

    start = sub.add_parser("start", help="Start one existing instance without rebuilding the image")
    start.add_argument("name", help="Instance name")
    start.set_defaults(func=_cmd_start)

    stop = sub.add_parser("stop", help="Stop one instance. Mounts and the workflow file are kept.")
    stop.add_argument("name", help="Instance name")
    stop.set_defaults(func=_cmd_stop)

    kill = sub.add_parser("kill", help="Force-stop one instance with docker kill (SIGKILL)")
    kill.add_argument("name", help="Instance name")
    kill.set_defaults(func=_cmd_kill)

    delete = sub.add_parser(
        "delete",
        help="Force-stop and remove one instance container, and drop its fleet record",
    )
    delete.add_argument("name", help="Instance name")
    delete.set_defaults(func=_cmd_delete)

    restart = sub.add_parser("restart", help="Stop then start one instance without rebuilding the image")
    restart.add_argument("name", help="Instance name")
    restart.set_defaults(func=_cmd_restart)

    listing = sub.add_parser("list", help="Show instances, status, and host ports")
    listing.set_defaults(func=_cmd_list)

    bind = argparse.ArgumentParser(add_help=False)
    bind.add_argument(
        "--host",
        default=DEFAULT_BIND_HOST,
        help=(
            f"Bind address (default {DEFAULT_BIND_HOST}). "
            "Use 127.0.0.1 to keep the control server on this machine."
        ),
    )
    bind.add_argument(
        "--port",
        type=int,
        default=DEFAULT_BIND_PORT,
        help=f"Control HTTP port (default {DEFAULT_BIND_PORT})",
    )
    bind.add_argument(
        "--ui-dir",
        default=None,
        help="Directory of static control-UI files to serve at /. Defaults to ./ui when that directory exists.",
    )
    ui_help = (
        "Serve the control HTTP API for the web UI. "
        f"Default is http://{DEFAULT_BIND_HOST}:{DEFAULT_BIND_PORT}/ . "
        "Requires COMFYFLEET_PASSWORD. Trusted LAN is still recommended."
    )
    ui = sub.add_parser("ui", parents=[bind], help="Serve the control HTTP API", description=ui_help)
    ui.set_defaults(func=_cmd_ui)
    serve_cmd = sub.add_parser(
        "serve",
        parents=[bind],
        help="Alias of ui",
        description=ui_help,
    )
    serve_cmd.set_defaults(func=_cmd_ui)
    return parser


def _cmd_create(args: argparse.Namespace) -> int:
    gpus = detect_gpus()
    zip_bytes = _read_zip(args.custom_nodes_zip)
    result = create_instance(
        Path(args.workflow),
        layout=FleetLayout(),
        docker=DockerCLI(),
        gpus=gpus,
        gpu=args.gpu,
        gpus_spec=args.gpus,
        interactive=sys.stdin.isatty(),
        prompt=input,
        image=args.image,
        cuda_tag=args.cuda_tag,
        start=args.start,
        force=args.force,
        use_env_limit=True,
        launch=parse_launch(
            vram=args.vram,
            attention=args.attention,
            flags=args.flag,
            reserve_vram=args.reserve_vram,
            vram_headroom=args.vram_headroom,
            preview_method=args.preview_method,
            preview_size=args.preview_size,
            extra_args=combine_extra_args(args.extra_args, args.comfy_extra_args),
        ),
        custom_node_git_urls=args.custom_node_git_urls,
        custom_nodes_zip=zip_bytes,
        install_missing_from_workflow=args.install_missing_from_workflow,
    )
    _print_warning(result.warning)
    for item in result.warnings:
        print(f"comfyfleet: warning: {item}", file=sys.stderr)
    instance = result.instance
    state = "started" if result.started else "created (not started)"
    print(f"{state}: {instance.name}")
    print(f"  workflow: {instance.workflow_host_path}")
    print(f"  port:     {instance.port}")
    print(f"  url:      {_open_url(instance.port)}")
    print(f"  gpus:     {','.join(str(index) for index in instance.gpus)}")
    print(f"  image:    {instance.image}")
    print(f"  cuda:     {instance.cuda_tag or '(custom ref)'}")
    print(f"  comfy:    {' '.join(main_argv(instance.launch))}")
    if not result.started:
        print(f"Start it with: comfyfleet start {instance.name}")
    return 0


def _read_zip(path: str | None) -> bytes | None:
    if path is None or not str(path).strip():
        return None
    zip_path = Path(path)
    try:
        return zip_path.read_bytes()
    except OSError as exc:
        raise FleetError(f"cannot read custom nodes zip {zip_path}: {exc}") from exc


def _cmd_start(args: argparse.Namespace) -> int:
    gpus = detect_gpus()
    result = start_instance(
        args.name,
        layout=FleetLayout(),
        docker=DockerCLI(),
        gpus=gpus,
        use_env_limit=True,
    )
    _print_warning(result.warning)
    instance = result.instance
    print(f"started: {instance.name}")
    print(f"  port:  {instance.port}")
    print(f"  url:   {_open_url(instance.port)}")
    print(f"  gpus:  {','.join(str(index) for index in instance.gpus)}")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    instance = stop_instance(args.name, layout=FleetLayout(), docker=DockerCLI())
    print(f"stopped: {instance.name}")
    return 0


def _cmd_kill(args: argparse.Namespace) -> int:
    instance = force_stop_instance(args.name, layout=FleetLayout(), docker=DockerCLI())
    print(f"killed: {instance.name}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    instance = delete_instance(args.name, layout=FleetLayout(), docker=DockerCLI())
    print(f"deleted: {instance.name}")
    return 0


def _cmd_restart(args: argparse.Namespace) -> int:
    gpus = detect_gpus()
    result = restart_instance(
        args.name,
        layout=FleetLayout(),
        docker=DockerCLI(),
        gpus=gpus,
        use_env_limit=True,
    )
    _print_warning(result.warning)
    print(f"restarted: {result.instance.name} on port {result.instance.port}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    rows = list_instances(FleetLayout(), DockerCLI())
    print(format_list(rows))
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    serve(host=args.host, port=args.port, ui_dir=args.ui_dir)
    return 0


def _open_url(port: int) -> str:
    raw = os.environ.get(PUBLIC_HOST_ENV, "").strip()
    if not raw:
        return f"http://0.0.0.0:{port}"
    return f"http://{open_host(None, '0.0.0.0', public_host=raw)}:{port}"


def _print_warning(warning: str | None) -> None:
    if warning:
        print(f"comfyfleet: warning: {warning}", file=sys.stderr)
