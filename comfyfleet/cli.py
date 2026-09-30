"""Command-line entrypoint. Phase 2 Auth can wrap ``comfyfleet.control``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from comfyfleet import __version__
from comfyfleet.control import (
    create_instance,
    format_list,
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from comfyfleet.docker import DockerCLI
from comfyfleet.errors import FleetError
from comfyfleet.gpu import detect_gpus
from comfyfleet.paths import DEFAULT_IMAGE, FleetLayout
from comfyfleet.ports import tcp_port_in_use


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
    create.add_argument("--image", default=DEFAULT_IMAGE, help=f"Image tag (default {DEFAULT_IMAGE})")
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
    create.set_defaults(func=_cmd_create)

    start = sub.add_parser("start", help="Start one existing instance without rebuilding the image")
    start.add_argument("name", help="Instance name")
    start.set_defaults(func=_cmd_start)

    stop = sub.add_parser("stop", help="Stop one instance. Mounts and the workflow file are kept.")
    stop.add_argument("name", help="Instance name")
    stop.set_defaults(func=_cmd_stop)

    restart = sub.add_parser("restart", help="Stop then start one instance without rebuilding the image")
    restart.add_argument("name", help="Instance name")
    restart.set_defaults(func=_cmd_restart)

    listing = sub.add_parser("list", help="Show instances, status, and host ports")
    listing.set_defaults(func=_cmd_list)
    return parser


def _cmd_create(args: argparse.Namespace) -> int:
    gpus = detect_gpus()
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
        start=args.start,
        force=args.force,
        port_in_use=tcp_port_in_use,
        use_env_limit=True,
    )
    _print_warning(result.warning)
    instance = result.instance
    state = "started" if result.started else "created (not started)"
    print(f"{state}: {instance.name}")
    print(f"  workflow: {instance.workflow_host_path}")
    print(f"  port:     {instance.port}")
    print(f"  url:      http://0.0.0.0:{instance.port}")
    print(f"  gpus:     {','.join(str(index) for index in instance.gpus)}")
    print(f"  image:    {instance.image}")
    if not result.started:
        print(f"Start it with: comfyfleet start {instance.name}")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    gpus = detect_gpus()
    result = start_instance(
        args.name,
        layout=FleetLayout(),
        docker=DockerCLI(),
        gpus=gpus,
        port_in_use=tcp_port_in_use,
        use_env_limit=True,
    )
    _print_warning(result.warning)
    instance = result.instance
    print(f"started: {instance.name}")
    print(f"  port:  {instance.port}")
    print(f"  url:   http://0.0.0.0:{instance.port}")
    print(f"  gpus:  {','.join(str(index) for index in instance.gpus)}")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    instance = stop_instance(args.name, layout=FleetLayout(), docker=DockerCLI())
    print(f"stopped: {instance.name}")
    return 0


def _cmd_restart(args: argparse.Namespace) -> int:
    gpus = detect_gpus()
    result = restart_instance(
        args.name,
        layout=FleetLayout(),
        docker=DockerCLI(),
        gpus=gpus,
        port_in_use=tcp_port_in_use,
        use_env_limit=True,
    )
    _print_warning(result.warning)
    print(f"restarted: {result.instance.name} on port {result.instance.port}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    rows = list_instances(FleetLayout(), DockerCLI())
    print(format_list(rows))
    return 0


def _print_warning(warning: str | None) -> None:
    if warning:
        print(f"comfyfleet: warning: {warning}", file=sys.stderr)
