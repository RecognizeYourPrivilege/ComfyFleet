#!/usr/bin/env python3
"""Placeholder distribution for ``opencv-python-headless``.

ComfyFleet installs custom nodes in two places, and both of them pip-install
that pack's requirements inside the instance:

* A git URL posted to Manager's ``POST /customnode/install/git_url`` (the
  create-time trusted install, and an install from the running instance).
  Pinned Manager ``14b5aaab`` clones the repo and runs
  ``python -m pip install`` once per ``requirements.txt`` line. It does not
  pass ``-c``. ``PIP_CONSTRAINT`` is inherited.
* A zip extracted onto the instance ``custom_nodes`` volume. Manager installs
  those requirements the same way when the pack is installed or when a
  reserved install script runs. ``comfyui-reactor-node`` is also the registry
  id, so a registry zip takes this path.

``comfyui-reactor-node`` does not pin ``opencv-python-headless``. Its
``requirements.txt`` asks for ``albumentations>=1.4.16``. Every release from
1.4.16 through 2.0.8 depends on ``opencv-python-headless>=4.9.0.80``
(``albucore`` repeats that). The published headless wheels are ``cp37-abi3``
and install on both image Pythons (3.14.7 on cu130, 3.11 on cu124).

The images already import ``cv2`` from ``opencv-python`` (RES4LYF). The real
headless wheel writes another ``cv2`` and replaces that module. The old
constraint ``opencv-python-headless<0`` was there so pip would refuse the
real wheel. It matches no distribution, so the resolver stops with
"no matching distributions available for your environment" and names
``opencv-python-headless``. That is the same failure on both CUDA lines.

This wheel is version 99.0.0. That satisfies ``>=4.9.0.80``, is not on PyPI,
and contains no ``cv2`` files. The constraint file pins that version.
``PIP_FIND_LINKS`` points at the wheel directory, so Manager's pip command
selects the placeholder. A request for a published headless build does not
match the pin. Torch, numpy, and llama-cpp-python pins are unchanged.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import zipfile
from pathlib import Path

PACKAGE = "opencv-python-headless"
VERSION = "99.0.0"
CONSTRAINT = f"{PACKAGE}=={VERSION}"
# pip list normalizes dashes to underscores.
PIP_LIST_NAME = "opencv_python_headless"
WHEEL_FILENAME = f"{PIP_LIST_NAME}-{VERSION}-py3-none-any.whl"
_DIST_INFO = f"{PIP_LIST_NAME}-{VERSION}.dist-info"


def build_wheel(directory: Path) -> Path:
    """Write the placeholder wheel into ``directory`` and return its path."""

    directory.mkdir(parents=True, exist_ok=True)
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {PACKAGE}\n"
        f"Version: {VERSION}\n"
        "Summary: ComfyFleet placeholder. opencv-python already provides cv2.\n"
        "Requires-Python: >=3.9\n"
    ).encode()
    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: comfyfleet-opencv-headless-shim\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode()
    marker = (
        "ComfyFleet placeholder for opencv-python-headless.\n"
        "This distribution does not ship cv2.\n"
        f"Version {VERSION} satisfies opencv-python-headless>=4.9.0.80.\n"
    ).encode()
    files = {
        f"{_DIST_INFO}/METADATA": metadata,
        f"{_DIST_INFO}/WHEEL": wheel_meta,
        f"{_DIST_INFO}/comfyfleet-shim": marker,
    }
    record = "".join(_record_line(name, data) for name, data in files.items())
    record += f"{_DIST_INFO}/RECORD,,\n"
    destination = directory / WHEEL_FILENAME
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(f"{_DIST_INFO}/RECORD", record.encode())
    return destination


def wheel_ships_cv2(path: Path) -> bool:
    """True when the wheel contains a ``cv2`` module or package."""

    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name == "cv2.py" or name.startswith("cv2/"):
                return True
    return False


def _record_line(name: str, data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{name},sha256={encoded},{len(data)}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    path = build_wheel(args.wheel_dir)
    if wheel_ships_cv2(path):
        print(f"comfyfleet: {path} contains cv2", file=sys.stderr)
        return 1
    print(f"comfyfleet: wrote {path} ({CONSTRAINT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
