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

It also fails the build when /opt/venv is not the Python pin for this line.
The cu130 line is CPython 3.14.7. torchaudio is pinned to 2.11.0+cu130
because the cu130 index has no torchaudio 2.13 wheel. The cu124 line is
Debian bookworm CPython 3.11 with torchaudio 2.6.0+cu124. On both lines the
trio names still have to be present for Manager's pip-list check.

``COMFYFLEET_CUDA_TAG`` selects the line (``cu130`` or ``cu124``). Unset
means cu130, which is what the module-level pin constants describe.
"""

import ctypes
import glob
import importlib.util
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

# cu124 is the peer line (Dockerfile.cu124). main() swaps the globals above
# when COMFYFLEET_CUDA_TAG=cu124. Import-time constants stay on cu130.
PIN_PROFILES = {
    "cu130": {
        "numpy": "2.3.2",
        "python": (3, 14, 7),
        "torch": (
            ("torch", "2.13.0+cu130"),
            ("torchvision", "0.28.0+cu130"),
            ("torchaudio", "2.11.0+cu130"),
        ),
    },
    "cu124": {
        "numpy": "2.2.6",
        "python": (3, 11),
        "torch": (
            ("torch", "2.6.0+cu124"),
            ("torchvision", "0.21.0+cu124"),
            ("torchaudio", "2.6.0+cu124"),
        ),
    },
}

# Exact logging.error text in PIPFixer.fix_broken when any of the three
# names is missing from the pre-install pip list snapshot.
MANAGER_PYTORCH_MISSING_LOG = "[ComfyUI-Manager] PyTorch is not installed"

# JoyCaption imports llama_cpp. Both CUDA lines bake this abetlen wheel.
# pip list normalizes dashes to underscores.
LLAMA_PIP_PIN = ("llama_cpp_python", "0.3.36")

# RES4LYF images.py and Impact modules/impact/utils.py both import cv2.
# The wheel must be opencv-python. The published opencv-python-headless wheel
# replaces that module. The image installs only the placeholder version from
# docker/opencv_headless_shim.py, which has no cv2 files.
CV2_SOURCES = (
    "RES4LYF/images.py",
    "ComfyUI-Impact-Pack/modules/impact/utils.py",
)
BAKED_ROOT = "/opt/comfyfleet/baked_custom_nodes"


def _headless_shim_version() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencv_headless_shim.py")
    spec = importlib.util.spec_from_file_location("_comfyfleet_opencv_headless_shim", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"opencv headless shim missing: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VERSION


HEADLESS_SHIM_VERSION = _headless_shim_version()


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


def configure_pins(cuda_tag: str | None = None) -> str:
    """Point the module pin globals at one CUDA line. Returns the tag."""

    global NUMPY_PIN, PYTHON_PIN, TORCH_PIP_PINS
    tag = cuda_tag
    if tag is None:
        tag = os.environ.get("COMFYFLEET_CUDA_TAG", "cu130")
    tag = (tag or "cu130").strip()
    profile = PIN_PROFILES.get(tag)
    if profile is None:
        sys.exit(f"comfyfleet: CUDA line {tag!r} is not cu130 or cu124")
    NUMPY_PIN = profile["numpy"]
    PYTHON_PIN = profile["python"]
    TORCH_PIP_PINS = profile["torch"]
    return tag


def manager_torch_pin_errors(versions: Mapping[str, str]) -> list[str]:
    """Errors when the snapshot would trip Manager, or a pin does not match."""
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


def llama_pin_errors(versions: Mapping[str, str]) -> list[str]:
    """Fail when the baked llama-cpp-python wheel is missing or drifted."""

    name, pin = LLAMA_PIP_PIN
    got = versions.get(name)
    if got != pin:
        return [f"llama-cpp-python {got!r} != {pin!r}"]
    return []


def assert_llama_import() -> None:
    """Run the class import. The smoke script deletes any driver stub it builds."""

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llama_import_smoke.py")
    if not os.path.isfile(script):
        sys.exit(f"comfyfleet: llama import smoke missing: {script}")
    completed = subprocess.run([sys.executable, script], check=False)
    if completed.returncode != 0:
        sys.exit('comfyfleet: from llama_cpp import Llama failed')


def opencv_distribution_errors(versions: Mapping[str, str]) -> list[str]:
    """Fail unless cv2 stays on opencv-python and headless is the placeholder.

    ``pip list`` names are already normalized (dashes become underscores).
    The placeholder version satisfies ReActor's albumentations dependency.
    Any other headless version is the published wheel and replaces ``cv2``.
    """

    errors: list[str] = []
    headless = versions.get("opencv_python_headless")
    if headless is None:
        errors.append(
            "opencv-python-headless placeholder is not installed. "
            f"Only version {HEADLESS_SHIM_VERSION} is allowed, and it must be present "
            "so a later requirements install can satisfy opencv-python-headless>=4.9.0.80."
        )
    elif headless != HEADLESS_SHIM_VERSION:
        errors.append(
            f"opencv-python-headless {headless!r} is installed. RES4LYF and Impact "
            "import cv2 from opencv-python. Only the placeholder "
            f"{HEADLESS_SHIM_VERSION} is allowed; the published headless wheel replaces cv2."
        )
    if "opencv_python" not in versions:
        errors.append("opencv-python is not installed")
    return errors


def missing_cv2_imports(baked_root: str) -> list[str]:
    """Paths under the bake root that do not import cv2."""

    missing: list[str] = []
    for rel in CV2_SOURCES:
        path = os.path.join(baked_root, rel)
        if not os.path.isfile(path):
            missing.append(f"{path} missing")
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if "import cv2" not in text:
            missing.append(f"{path} does not import cv2")
    return missing


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
    configure_pins()
    got = sys.version_info[: len(PYTHON_PIN)]
    if got != PYTHON_PIN:
        sys.exit(f"python {got} != {PYTHON_PIN}")

    import numpy

    if numpy.__version__ != NUMPY_PIN:
        sys.exit(f"numpy {numpy.__version__} != {NUMPY_PIN}")

    versions = parse_manager_pip_list(pip_list_text())
    pin_errors = manager_torch_pin_errors(versions)
    pin_errors.extend(opencv_distribution_errors(versions))
    pin_errors.extend(llama_pin_errors(versions))
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
    import_errors = missing_cv2_imports(BAKED_ROOT)
    if import_errors:
        sys.exit("comfyfleet: " + "; ".join(import_errors))
    assert_llama_import()
    print(
        "comfyfleet: "
        f"numpy {numpy.__version__} cv2 {cv2.__version__} libxcb ok "
        f"torch {versions['torch']} torchvision {versions['torchvision']} "
        f"torchaudio {versions['torchaudio']} "
        f"llama-cpp-python {versions['llama_cpp_python']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
