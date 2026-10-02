#!/usr/bin/env python3
"""Filter Impact Pack requirements and refuse baked weight files.

ComfyUI-Impact-Pack and ComfyUI-Impact-Subpack list ``opencv-python-headless``.
RES4LYF already installed ``opencv-python``. The headless wheel replaces
``cv2``. This filter drops both OpenCV project names so the existing
``opencv-python`` wheel stays, then the image build installs the rest.

SAM and other weight files are not baked. ``install.py`` is run with Impact's
``skip_download_model`` sentinel, and this script can check that no weight
file landed under the model or baked-node trees.
"""

from __future__ import annotations

import sys
from pathlib import Path

BANNED = {"opencv-python", "opencv-python-headless"}
WEIGHT_GLOBS = ("*.pt", "*.pth", "*.safetensors", "*.onnx", "*.ckpt")
DEFAULT_SOURCES = (
    Path("/opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Pack/requirements.txt"),
    Path("/opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Subpack/requirements.txt"),
)
DEFAULT_REQUIREMENTS = Path("/tmp/impact-requirements.txt")
DEFAULT_WEIGHT_ROOTS = (
    Path("/opt/ComfyUI/models"),
    Path("/opt/comfyfleet/baked_custom_nodes"),
)


def project_name(line: str) -> str:
    text = line.strip()
    if not text or text.startswith("#"):
        return ""
    payload = text.split("#", 1)[0].strip()
    if payload.startswith("git+") or "git+" in payload.split(";", 1)[0]:
        return ""
    name = payload.split(";", 1)[0].strip()
    name = name.split("[", 1)[0]
    for sep in ("===", "==", "!=", "<=", ">=", "~=", "<", ">", "="):
        if sep in name:
            name = name.split(sep, 1)[0]
            break
    return name.strip().lower().replace("_", "-")


def filter_requirements(text: str) -> str:
    """Drop OpenCV lines. Keep every other requirement, including VCS URLs."""

    kept: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = project_name(stripped)
        if name in BANNED or "opencv-python-headless" in stripped.lower():
            continue
        kept.append(stripped)
    body = "\n".join(kept)
    if body:
        body += "\n"
    if "opencv-python-headless" in body.lower():
        raise SystemExit("refusing to install opencv-python-headless")
    return body


def merged_requirements(texts: list[str]) -> str:
    return filter_requirements("\n".join(texts))


def weight_files(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in WEIGHT_GLOBS:
            found.extend(path for path in root.rglob(pattern) if path.is_file())
    return found


def assert_no_weight_files(roots: list[Path]) -> None:
    found = weight_files(roots)
    if found:
        shown = ", ".join(str(path) for path in found[:30])
        raise SystemExit(f"weight files baked into the image: {shown}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--check-weights"]:
        roots = [Path(item) for item in args[1:]] or list(DEFAULT_WEIGHT_ROOTS)
        assert_no_weight_files(roots)
        print("comfyfleet: no SAM or other weight files baked")
        return 0
    texts = []
    for path in DEFAULT_SOURCES:
        texts.append(path.read_text(encoding="utf-8"))
    body = merged_requirements(texts)
    DEFAULT_REQUIREMENTS.write_text(body, encoding="utf-8")
    print(f"comfyfleet: wrote {DEFAULT_REQUIREMENTS} without opencv-python-headless")
    return 0


if __name__ == "__main__":
    sys.exit(main())
