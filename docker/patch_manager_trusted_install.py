#!/usr/bin/env python3
"""Let ComfyFleet skip Manager's loopback listen check for dedicated installs.

Pinned ComfyUI-Manager 14b5aaab gates ``POST /customnode/install/git_url``,
``POST /customnode/install/pip``, and the unknown-git-URL arm of
``POST /manager/queue/install`` with ``is_dedicated_install_allowed``:

    return bool(flag_value) and is_loopback(listen_address)

The flag comes from config.ini. The listen address is ComfyUI's ``--listen``.
ComfyFleet SPEC-locks ``--listen 0.0.0.0`` so Docker can publish the instance
port onto the docker.sock LAN. ``0.0.0.0`` is not a loopback address, so stock
Manager denies git-URL install even when ``allow_git_url_install`` is true.

This rewrite keeps the flag check. When the entrypoint has exported
``COMFYFLEET_TRUSTED_INSTALL=1`` and the flag is true, the loopback term is
skipped. Unset the variable and the stock predicate returns. The listen
address is not changed.

``0.0.0.0`` is required for Docker publish. A Manager bound on the public
internet, with git-URL install open and no operator gate, is a different
threat model. The env var is the explicit ComfyFleet gate.

The patch is source-only and idempotent. A pin bump that changes the helper
fails the image build instead of silently leaving the stock gate in place.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_PATH = Path(
    "/opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager/glob/manager_server.py"
)

STOCK_RETURN = "return bool(flag_value) and is_loopback(listen_address)"
NETWORK_MODE_LOG = (
    'logging.info("[ComfyUI-Manager] network_mode: " + core.get_config()[\'network_mode\'])\n'
)
# This string is absent from pinned manager_server.py. "dedicated install
# flags" is already in the helper docstring, so it cannot mark our log line.
FLAG_LOG_MARKER = "[ComfyUI-Manager] ComfyFleet dedicated install flags:"
FLAG_LOG = (
    'logging.info("[ComfyUI-Manager] ComfyFleet dedicated install flags: '
    'allow_git_url_install=%s allow_pip_install=%s security_level=%s '
    'COMFYFLEET_TRUSTED_INSTALL=%s" % ('
    "core.get_config()['allow_git_url_install'], "
    "core.get_config()['allow_pip_install'], "
    "core.get_config()['security_level'], "
    'os.environ.get("COMFYFLEET_TRUSTED_INSTALL", "")))\n'
)


class ManagerPatchError(Exception):
    """The baked Manager source is not the pinned helper this patch knows."""


def _span(source: str, node: ast.AST) -> tuple[int, int]:
    lines = source.splitlines(keepends=True)
    if node.end_lineno is None or node.end_col_offset is None or node.lineno is None:
        raise ManagerPatchError("AST node is missing source positions")
    start = sum(len(lines[i]) for i in range(node.lineno - 1)) + node.col_offset
    end = sum(len(lines[i]) for i in range(node.end_lineno - 1)) + node.end_col_offset
    return start, end


def _function(source: str) -> ast.FunctionDef | None:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "is_dedicated_install_allowed":
            return node
    return None


def _replacement(indent: str) -> str:
    # The first line is written at the return statement's column. Later lines
    # repeat that indent. Inner returns add four spaces of their own.
    lines = [
        "# ComfyFleet: the config flag stays required. COMFYFLEET_TRUSTED_INSTALL=1",
        "# (set by the instance entrypoint) skips the loopback term. --listen",
        "# stays 0.0.0.0 so Docker can publish the port onto the docker.sock LAN.",
        "if not bool(flag_value):",
        "    return False",
        'if os.environ.get("COMFYFLEET_TRUSTED_INSTALL") == "1":',
        "    return True",
        "return is_loopback(listen_address)",
    ]
    parts = [lines[0]]
    parts.extend(indent + line for line in lines[1:])
    return "\n".join(parts)


def _patch_predicate(source: str) -> str:
    func = _function(source)
    if func is None:
        raise ManagerPatchError(
            "is_dedicated_install_allowed is missing from manager_server.py. "
            "The Manager pin changed; update docker/patch_manager_trusted_install.py."
        )
    segment = ast.get_source_segment(source, func) or ""
    if "COMFYFLEET_TRUSTED_INSTALL" in segment:
        return source

    statements = [
        node
        for node in func.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    if len(statements) != 1 or not isinstance(statements[0], ast.Return):
        raise ManagerPatchError(
            "is_dedicated_install_allowed is not the pinned single-return helper. "
            "Refusing to patch an unrecognized gate."
        )
    statement = ast.get_source_segment(source, statements[0])
    if statement != STOCK_RETURN:
        raise ManagerPatchError(
            "is_dedicated_install_allowed returned "
            f"{statement!r}, not the pinned {STOCK_RETURN!r}."
        )
    start, end = _span(source, statements[0])
    line_start = source.rfind("\n", 0, start) + 1
    indent = source[line_start:start]
    if indent.strip():
        raise ManagerPatchError("the dedicated-install return is not at a clean indent")
    return source[:start] + _replacement(indent) + source[end:]


def _patch_flag_log(source: str) -> str:
    if FLAG_LOG_MARKER in source:
        return source
    if NETWORK_MODE_LOG not in source:
        raise ManagerPatchError(
            "manager_server.py has no network_mode startup log to anchor the "
            "dedicated-install flag line."
        )
    return source.replace(NETWORK_MODE_LOG, NETWORK_MODE_LOG + FLAG_LOG, 1)


def patch_source(source: str) -> str:
    """Return Manager ``manager_server.py`` with the ComfyFleet gate applied."""
    if "import os\n" not in source and "import os\r\n" not in source:
        raise ManagerPatchError("manager_server.py does not import os; the trusted-install gate needs it")
    updated = _patch_flag_log(_patch_predicate(source))
    ast.parse(updated)
    return updated


def patch_file(path: Path) -> bool:
    """Patch ``path``. Return True when the file was rewritten."""
    if not path.is_file():
        raise ManagerPatchError(f"Manager source is missing: {path}")
    source = path.read_text(encoding="utf-8")
    updated = patch_source(source)
    if updated == source:
        print(f"comfyfleet: Manager trusted-install patch already applied: {path}")
        return False
    path.write_text(updated, encoding="utf-8")
    print(f"comfyfleet: patched Manager dedicated-install gate: {path}")
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else DEFAULT_PATH
    try:
        patch_file(path)
    except ManagerPatchError as exc:
        print(f"comfyfleet: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
