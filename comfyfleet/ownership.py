"""Allowlisted host ownership for ComfyFleet.

``comfyfleet fix-owner`` and ``POST /api/host/fix-owner`` both call
:func:`fix_owner`. The allowlist is fixed: there is no path argument.
Recursive ``chown`` runs only on those trees, and a path that escapes them
is refused before it is chowned.

Instance create uses :func:`ensure_wildcards_dir`, which creates
``/home/wildcards`` only when it is missing and chowns that new directory
once. It does not walk an existing tree.
"""

from __future__ import annotations

import os
import pwd
import grp
import re
from dataclasses import dataclass
from pathlib import Path

from comfyfleet.errors import FleetError
from comfyfleet.paths import FleetLayout

OWNER_NAME = "comfyui"
GROUP_NAME = "comfyui"
_CUSTOM_NODES_PREFIX = "custom_nodes_"
_NODE_SUFFIX = re.compile(r"[A-Za-z0-9._-]+\Z")


@dataclass(frozen=True)
class FixOwnerResult:
    uid: int
    gid: int
    user: str
    group: str
    paths: tuple[str, ...]


def resolve_comfyui_ids() -> tuple[int, int]:
    """Uid and gid of the host ``comfyui:comfyui`` names."""

    try:
        user = pwd.getpwnam(OWNER_NAME)
    except KeyError as exc:
        raise FleetError(
            "cannot resolve host user comfyui by name. "
            "Create the comfyui user before running fix-owner."
        ) from exc
    try:
        group = grp.getgrnam(GROUP_NAME)
    except KeyError as exc:
        raise FleetError(
            "cannot resolve host group comfyui by name. "
            "Create the comfyui group before running fix-owner."
        ) from exc
    return user.pw_uid, group.gr_gid


def ensure_wildcards_dir(layout: FleetLayout) -> bool:
    """Create the shared wildcards directory only when it is missing.

    Returns True when this call created it and chowned that directory
    (not its children). An existing directory, including one that already
    has files, is left alone. A second create does not ``chown -R``.
    """

    path = layout.wildcards
    if path.is_symlink() or path.exists():
        return False
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return False
    chown_new_directory(path)
    return True


def chown_new_directory(path: Path) -> None:
    """``chown comfyui:comfyui`` on one directory. Not recursive.

    The manager entrypoint exports ``COMFYFLEET_MANAGER=1`` and runs as root.
    In that process a missing ``comfyui`` user or a failed chown is an error.
    A unit test or a host checkout without that variable cannot chown to a
    user that is not there; those calls leave the new directory as-is.
    ``fix_owner`` does not use this skip.
    """

    strict = os.environ.get("COMFYFLEET_MANAGER") == "1"
    try:
        uid, gid = resolve_comfyui_ids()
    except FleetError:
        if strict:
            raise
        return
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except OSError as exc:
        if not strict:
            return
        raise FleetError(
            f"cannot chown {path} to {OWNER_NAME}:{GROUP_NAME} ({uid}:{gid}): {exc}. "
            "The manager image runs as root so it can set the owner of a directory it just created."
        ) from exc


def fix_owner(
    layout: FleetLayout,
    *,
    resolve=None,
    chown=None,
) -> FixOwnerResult:
    """Recursively chown the fixed allowlist to ``comfyui:comfyui``.

    No caller-supplied path. Roots outside ``layout.root`` are refused.
    """

    from comfyfleet.control import authorize

    authorize("fix-owner")
    uid, gid = (resolve or resolve_comfyui_ids)()
    actor = chown or chown_inode
    changed: list[str] = []
    for path in allowlisted_roots(layout):
        assert_allowlisted(path, layout)
        chown_tree(path, uid, gid, layout, actor)
        changed.append(str(_abs(path)))
    return FixOwnerResult(
        uid=uid,
        gid=gid,
        user=OWNER_NAME,
        group=GROUP_NAME,
        paths=tuple(changed),
    )


def allowlisted_roots(layout: FleetLayout) -> list[Path]:
    """Existing allowlisted directories under the fleet root.

    ``/home/wildcards``, ``/home/models``, ``/home/files``, and every
    ``/home/custom_nodes_*`` directory. Missing entries are skipped.
    """

    found: list[Path] = []
    for path in (layout.wildcards, layout.models, layout.files):
        if path.is_symlink() or path.exists():
            found.append(path)
    root = _abs(layout.root)
    if root.is_dir():
        try:
            children = list(root.iterdir())
        except OSError as exc:
            raise FleetError(f"cannot list {root}: {exc}") from exc
        for child in sorted(children, key=lambda item: item.name):
            if _is_custom_nodes_name(child.name) and (child.is_symlink() or child.is_dir()):
                found.append(child)
    return found


def assert_allowlisted(path: Path, layout: FleetLayout) -> Path:
    """Raise ``FleetError`` unless ``path`` stays inside an allowlisted root.

    ``..``, absolute paths outside the fleet root, and symlinks whose
    target leaves the allowlist are refused. The returned path is
    normalized without following symlinks.
    """

    root = _abs(layout.root)
    normalized = _abs(path)
    if normalized != root and not _is_under(normalized, root):
        raise FleetError(f"refusing path outside the /home allowlist: {path}")
    if not _is_allowlisted_lexical(normalized, layout):
        raise FleetError(f"refusing path outside the fix-owner allowlist: {path}")
    if path.is_symlink() or path.exists():
        real = Path(os.path.realpath(path))
        real_norm = _abs(real)
        if not _is_under(real_norm, root) or not _is_allowlisted_lexical(real_norm, layout):
            raise FleetError(
                f"refusing symlink that leaves the fix-owner allowlist: {path} -> {real}"
            )
    return normalized


def chown_tree(root: Path, uid: int, gid: int, layout: FleetLayout, chown) -> None:
    """``chown`` ``root`` and its descendants. Symlinks are not followed."""

    assert_allowlisted(root, layout)
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink() or not root.is_dir():
        chown(root, uid, gid)
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        assert_allowlisted(current, layout)
        chown(current, uid, gid)
        for name in filenames:
            path = current / name
            assert_allowlisted(path, layout)
            chown(path, uid, gid)
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            assert_allowlisted(path, layout)
            if path.is_symlink():
                chown(path, uid, gid)
                continue
            kept.append(name)
        dirnames[:] = kept


def chown_inode(path: Path, uid: int, gid: int) -> None:
    """Chown one inode. Does not follow a symlink."""

    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except OSError as exc:
        raise FleetError(f"cannot chown {path} to {uid}:{gid}: {exc}") from exc


def _is_custom_nodes_name(name: str) -> bool:
    if not name.startswith(_CUSTOM_NODES_PREFIX):
        return False
    suffix = name[len(_CUSTOM_NODES_PREFIX) :]
    if not suffix or suffix in {".", ".."} or ".." in suffix:
        return False
    return _NODE_SUFFIX.fullmatch(suffix) is not None


def _abs(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _named_roots(layout: FleetLayout) -> tuple[Path, ...]:
    root = _abs(layout.root)
    return (
        _abs(layout.wildcards),
        _abs(layout.models),
        _abs(layout.files),
    )


def _is_allowlisted_lexical(path: Path, layout: FleetLayout) -> bool:
    """True when ``path`` is an allowlisted root or a descendant of one.

    ``path`` must already be absolute and normalized without symlink resolution.
    """

    for candidate in _named_roots(layout):
        if path == candidate or _is_under(path, candidate):
            return True
    parent = path if path.parent == path else path
    # A descendant of custom_nodes_* matches on the first path component
    # under the fleet root. The root directory itself is not allowlisted.
    root = _abs(layout.root)
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    parts = relative.parts
    if not parts:
        return False
    if not _is_custom_nodes_name(parts[0]):
        return False
    return True
