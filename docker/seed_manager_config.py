#!/usr/bin/env python3
"""Seed ComfyUI-Manager config.ini for a ComfyFleet instance.

Pinned ComfyUI (v0.37.4 on the cu130 line, v0.38.0 on the cu124 line)
has ``folder_paths.get_system_user_directory``,
so pinned Manager 14b5aaab resolves its files through
``get_manager_path(user_dir)`` to ``<user directory>/__manager``. The default
user directory is ``<ComfyUI>/user``. The instance entrypoint does not pass
``--user-directory``, so the file Manager reads is:

    /opt/ComfyUI/user/__manager/config.ini

``read_config`` treats a missing ``allow_git_url_install`` or
``allow_pip_install`` as false (``get_bool`` is true only for the string
``true``, any case). This script creates that file, or sets these keys in
the ``[default]`` section when the file is already there:

    allow_git_url_install = true
    allow_pip_install = true
    security_level = normal

Other keys, other sections, and comments stay. A second run leaves a file
that already has those values untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_PATH = Path("/opt/ComfyUI/user/__manager/config.ini")

# Manager's get_bool() accepts only "true" after casefold. security_level is
# lowercased on read. These are the strings the denial messages document.
REQUIRED = (
    ("allow_git_url_install", "true"),
    ("allow_pip_install", "true"),
    ("security_level", "normal"),
)


def _newline_of(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _body(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _section_name(line: str) -> str | None:
    body = _body(line).strip()
    if len(body) < 3 or body[0] != "[" or "]" not in body:
        return None
    # A section header is the whole line. Inline comments after it are not
    # how Manager writes config.ini; ignore anything that is not just [name].
    end = body.find("]")
    if body[end + 1 :].strip():
        return None
    return body[1:end].strip()


def _split_key(line: str) -> tuple[str, str] | None:
    body = _body(line)
    stripped = body.lstrip()
    if not stripped or stripped[0] in "#;" or stripped[0] == "[":
        return None
    if "=" not in body:
        return None
    key, value = body.split("=", 1)
    if not key.strip():
        return None
    return key.strip(), value


def _semantic(value: str) -> str:
    cut = value
    for marker in (" ;", "\t;", " #", "\t#"):
        index = cut.find(marker)
        if index != -1:
            cut = cut[:index]
    return cut.strip()


def _acceptable(key: str, value: str) -> bool:
    semantic = _semantic(value).lower()
    if key == "security_level":
        return semantic == "normal"
    return semantic == "true"


def _default_spans(lines: list[str]) -> list[tuple[int, int]]:
    headers: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        name = _section_name(line)
        if name is not None:
            headers.append((index, name))
    spans: list[tuple[int, int]] = []
    for pos, (index, name) in enumerate(headers):
        if name.lower() != "default":
            continue
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        spans.append((index + 1, end))
    return spans


def _insert_missing(
    lines: list[str],
    spans: list[tuple[int, int]],
    missing: list[tuple[str, str]],
    newline: str,
) -> list[str]:
    block = [f"{key} = {wanted}{newline}" for key, wanted in missing]
    if not spans:
        if lines and not _newline_of(lines[-1]):
            lines = lines[:-1] + [lines[-1] + newline]
        addition: list[str] = []
        if lines and _body(lines[-1]).strip():
            addition.append(newline)
        addition.append(f"[default]{newline}")
        addition.extend(block)
        return lines + addition

    insert_at = spans[0][1]
    if insert_at > 0 and not _newline_of(lines[insert_at - 1]):
        lines = lines[: insert_at - 1] + [lines[insert_at - 1] + newline] + lines[insert_at:]
    return lines[:insert_at] + block + lines[insert_at:]


def ensure_manager_config(text: str) -> str:
    """Return ``text`` with the ComfyFleet Manager keys set in ``[default]``."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True) if text else []
    spans = _default_spans(lines)
    missing: list[tuple[str, str]] = []
    for key, wanted in REQUIRED:
        found = False
        for start, end in spans:
            for index in range(start, end):
                parsed = _split_key(lines[index])
                if parsed is None or parsed[0].lower() != key:
                    continue
                found = True
                if not _acceptable(key, parsed[1]):
                    ending = _newline_of(lines[index]) or newline
                    lines[index] = f"{key} = {wanted}{ending}"
        if not found:
            missing.append((key, wanted))
    if missing:
        lines = _insert_missing(lines, spans, missing, newline)
    result = "".join(lines)
    if result and not result.endswith(("\n", "\r\n")):
        result += newline
    return result


def seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = ensure_manager_config(current)
    if updated != current:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(path)
    print(
        "comfyfleet: Manager config "
        f"{path} allow_git_url_install=true allow_pip_install=true security_level=normal"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else DEFAULT_PATH
    seed(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
