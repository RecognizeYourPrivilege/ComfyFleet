"""Host port allocation starting at 8188."""

from __future__ import annotations

import socket

from comfyfleet.errors import FleetError

PORT_START = 8188
PORT_SCAN = 1000


def tcp_port_in_use(port: int) -> bool:
    for host in ("0.0.0.0", "127.0.0.1"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                return True
    return False


def choose_port(
    reserved: set[int],
    *,
    preferred: int | None = None,
    in_use=tcp_port_in_use,
    start: int = PORT_START,
    scan: int = PORT_SCAN,
) -> int:
    """Prefer ``preferred`` when it is free and not reserved by another instance.

    Stopped instances keep their recorded port in ``reserved`` so two created
    containers do not both claim 8188. A recorded port that is busy at start
    time is skipped and the next free port from ``start`` is used.
    """

    if preferred is not None and preferred not in reserved and not in_use(preferred):
        return preferred
    for port in range(start, start + scan):
        if port in reserved or in_use(port):
            continue
        return port
    raise FleetError(
        f"no free host port in {start}..{start + scan - 1}. "
        "Stop another listener or instance and retry."
    )
