#!/usr/bin/env python3
"""Seed ComfyUI-Impact-Pack ``impact-pack.ini`` for shared host wildcards.

Pinned Impact Pack reads ``impact-pack.ini`` two directories above
``modules/impact/config.py`` (the pack root). The entrypoint links the baked
pack into ``custom_nodes`` and then runs this script, so the setting is
written again on every start and survives recreate.

The value is written without quotes::

    custom_wildcards = /home/wildcards

Impact's ``read_config`` strips quotes if they are present, but this file
does not write them. Other keys and sections stay. A second run that already
has this exact line does not rewrite the file.
"""

from __future__ import annotations

import sys
from pathlib import Path

WILDCARDS = "/home/wildcards"
KEY = "custom_wildcards"
DEFAULT_PATHS = (
    Path("/opt/ComfyUI/custom_nodes/ComfyUI-Impact-Pack/impact-pack.ini"),
    Path("/opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Pack/impact-pack.ini"),
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


def _acceptable(value: str) -> bool:
    semantic = _semantic(value)
    if semantic != WILDCARDS:
        return False
    if '"' in semantic or "'" in semantic:
        return False
    return True


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


def _insert_line(
    lines: list[str],
    spans: list[tuple[int, int]],
    newline: str,
) -> list[str]:
    block = [f"{KEY} = {WILDCARDS}{newline}"]
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


def ensure_impact_config(text: str) -> str:
    """Return ``text`` with ``custom_wildcards = /home/wildcards`` and no quotes."""

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True) if text else []
    spans = _default_spans(lines)
    found = False
    for start, end in spans:
        for index in range(start, end):
            parsed = _split_key(lines[index])
            if parsed is None or parsed[0].lower() != KEY:
                continue
            found = True
            if not _acceptable(parsed[1]):
                ending = _newline_of(lines[index]) or newline
                lines[index] = f"{KEY} = {WILDCARDS}{ending}"
    if not found:
        lines = _insert_line(lines, spans, newline)
    result = "".join(lines)
    if result and not result.endswith(("\n", "\r\n")):
        result += newline
    return result


def seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = ensure_impact_config(current)
    if updated != current:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(path)
    print(f"comfyfleet: impact-pack.ini {path} custom_wildcards={WILDCARDS}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        seed(Path(args[0]))
        return 0
    seeded = False
    for path in DEFAULT_PATHS:
        # is_dir follows a custom_nodes symlink onto the baked pack.
        # Seeding both paths is idempotent when they are the same file.
        if path.parent.is_dir():
            seed(path)
            seeded = True
    if not seeded:
        print(
            "comfyfleet: Impact Pack directory missing; cannot seed impact-pack.ini",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
