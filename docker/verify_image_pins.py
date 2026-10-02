#!/usr/bin/env python3
"""Fail the instance image build when numpy drifted or OpenCV cannot load.

RES4LYF's images.py imports cv2 while the custom node loads. The
opencv-python wheel (the name in that repo's requirements.txt) ships a Qt
xcb platform plugin, cv2/qt/plugins/platforms/libqxcb.so, whose DT_NEEDED
entry is libxcb.so.1. bookworm-slim does not ship it. Importing cv2 also
loads libGL.so.1 and libglib-2.0.so.0 through the bundled Qt libraries.

It also fails the build when ComfyUI-Manager's pip-list check would log
"PyTorch is not installed". Pinned Manager 14b5aaab (glob/manager_util.py,
PIPFixer.fix_broken) does not import torch. After `python -m pip install`
it looks at the `pip list` snapshot taken before that command and logs the
error when torch, torchvision, or torchaudio is absent. This script parses
`python -m pip list` the same way.

This runs after requirements are installed. It does not need a GPU or a
display: dlopen resolves NEEDED libraries and returns before any X server
is contacted, and `pip list` does not import torch.

It also fails the build when /opt/venv is not CPython 3.14.7. torchaudio
is pinned to 2.11.0+cu130 because the cu130 index has no torchaudio 2.13
wheel. The name still has to be present for Manager's pip-list check.
"""

import ctypes
import glob
import os
import subprocess
import sys
from collections.abc import Mapping

NUMPY_PIN = "2.3.2"
PYTHON_PIN = (3, 14, 7)

# cu130 pins. torchvision 0.28.0 pairs with torch 2.13.0. torchaudio stays
# at 2.11.0+cu130 until a 2.13 cu130 audio wheel exists.
TORCH_PIP_PINS = (
    ("torch", "2.13.0+cu130"),
    ("torchvision", "0.28.0+cu130"),
    ("torchaudio", "2.11.0+cu130"),
)

# Exact logging.error text in PIPFixer.fix_broken when any of the three
# names is missing from the pre-install pip list snapshot.
MANAGER_PYTORCH_MISSING_LOG = "[ComfyUI-Manager] PyTorch is not installed"


def parse_manager_pip_list(text: str) -> dict[str, str]:
    """Parse `pip list` the way Manager 14b5aaab get_installed_packages does.

    Header rows are skipped. Names are lowercased and dashes become
    underscores. The second column is the version, including a local
    version such as ``2.13.0+cu130``.
    """
    pip_map: dict[str, str] = {}
    for line in text.split("\n"):
        if line.strip():
            columns = line.split()
            if columns[0] == "Package" or columns[0].startswith("-"):
                continue
            normalized_name = columns[0].lower().replace("-", "_")
            pip_map[normalized_name] = columns[1]
    return pip_map


def stock_manager_pytorch_log(versions: Mapping[str, str]) -> str | None:
    """Return Manager's missing-PyTorch log line, or None when the trio is listed.

    This is the ``if`` in ``PIPFixer.fix_broken``, not the version-change
    ``elif`` (that path logs a restore message and calls ``torch_rollback``).
    """
    if (
        "torch" not in versions
        or "torchvision" not in versions
        or "torchaudio" not in versions
    ):
        return MANAGER_PYTORCH_MISSING_LOG
    return None


def manager_torch_pin_errors(versions: Mapping[str, str]) -> list[str]:
    """Errors when the snapshot would trip Manager, or a pin is not cu130."""
    errors: list[str] = []
    log_line = stock_manager_pytorch_log(versions)
    if log_line is not None:
        missing = [name for name, _pin in TORCH_PIP_PINS if name not in versions]
        errors.append(
            f"{log_line} (pip list is missing {', '.join(missing) or 'a torch package'})"
        )
    for name, pin in TORCH_PIP_PINS:
        got = versions.get(name)
        if got != pin:
            errors.append(f"{name} {got!r} != {pin!r}")
    return errors


def pip_list_text(executable: str | None = None) -> str:
    """Run `<python> -m pip list`, the command Manager's get_pip_cmd builds."""
    python = executable or sys.executable
    completed = subprocess.run(
        [python, "-m", "pip", "list"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        sys.exit(
            "pip list failed. Manager's get_installed_packages then returns "
            "an empty map and logs that PyTorch is not installed.\n"
            f"{completed.stderr}"
        )
    return completed.stdout


def main() -> None:
    got = sys.version_info[:3]
    if got != PYTHON_PIN:
        sys.exit(f"python {got} != {PYTHON_PIN}")

    import numpy

    if numpy.__version__ != NUMPY_PIN:
        sys.exit(f"numpy {numpy.__version__} != {NUMPY_PIN}")

    versions = parse_manager_pip_list(pip_list_text())
    pin_errors = manager_torch_pin_errors(versions)
    if pin_errors:
        sys.exit("comfyfleet: " + "; ".join(pin_errors))

    # The historical crash, before any Qt plugin work. Apt package: libxcb1.
    ctypes.CDLL("libxcb.so.1")

    import cv2

    plugins = glob.glob(
        os.path.join(
            os.path.dirname(cv2.__file__),
            "qt",
            "plugins",
            "platforms",
            "libqxcb.so",
        )
    )
    if not plugins:
        sys.exit(
            "opencv-python Qt xcb plugin missing. RES4LYF requires "
            "opencv-python, and that plugin is what needs libxcb.so.1."
        )
    # Also pulls libX11.so.6, libXext.so.6, libSM.so.6, libICE.so.6, and
    # libGL.so.1. A missing soname raises OSError here.
    ctypes.CDLL(plugins[0], mode=ctypes.RTLD_GLOBAL)
    print(
        "comfyfleet: "
        f"numpy {numpy.__version__} cv2 {cv2.__version__} libxcb ok "
        f"torch {versions['torch']} torchvision {versions['torchvision']} "
        f"torchaudio {versions['torchaudio']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
