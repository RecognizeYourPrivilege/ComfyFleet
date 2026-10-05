"""comfyui-reactor-node requirements install on both instance lines.

Git URL installs and zip installs both end in Manager's pip command.
Pinned Manager 14b5aaab (``execute_install_script`` / ``execute_lazy_install_script``)
runs ``python -m pip install <line>`` for each requirements.txt line. It does
not pass ``-c``. The instance image exports ``PIP_CONSTRAINT`` and
``PIP_FIND_LINKS``, and that environment is what the command inherits.

``comfyui-reactor-node`` requirements.txt (pack version 0.7.1-b3) is:

    albumentations>=1.4.16
    onnx>=1.14.0
    opencv-python>=4.7.0.72
    numpy
    segment_anything
    ultralytics

It does not name opencv-python-headless. ``albumentations>=1.4.16`` does:
every release from 1.4.16 through 2.0.8 depends on
``opencv-python-headless>=4.9.0.80``. The published wheels are cp37-abi3, so
they exist for the cu130 image (CPython 3.14) and the cu124 image (CPython
3.11). The old constraint ``opencv-python-headless<0`` matches none of them.
pip then reports that opencv-python-headless has no matching distribution
and suggests loosening the pin.

The images already provide ``cv2`` from opencv-python. The placeholder wheel
satisfies the range and does not ship ``cv2``. Torch, numpy, and
llama-cpp-python pins stay as written in the Dockerfiles.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Manager installs these lines one at a time. They are the requirements.txt
# of the usual comfyui-reactor-node pack, not a loosened copy.
REACTOR_REQUIREMENTS = (
    "albumentations>=1.4.16",
    "onnx>=1.14.0",
    "opencv-python>=4.7.0.72",
    "numpy",
    "segment_anything",
    "ultralytics",
)

# The image's opencv-python build. A newer PyPI release does not replace an
# already-satisfied install: Manager does not pass -U for requirements lines.
OPENCV_PYTHON_VERSION = "5.0.0.93"
CV2_SENTINEL = "SENTINEL = 'opencv-python'\n"

KEPT_PINS = {
    "Dockerfile": (
        "torch==2.13.0+cu130",
        "torchvision==0.28.0+cu130",
        "torchaudio==2.11.0+cu130",
        "numpy==2.3.2",
        "llama-cpp-python==0.3.36",
    ),
    "Dockerfile.cu124": (
        "torch==2.6.0+cu124",
        "torchvision==0.21.0+cu124",
        "torchaudio==2.6.0+cu124",
        "numpy==2.2.6",
        "llama-cpp-python==0.3.36",
    ),
}


def _load_shim():
    path = ROOT / "docker" / "opencv_headless_shim.py"
    spec = importlib.util.spec_from_file_location("opencv_headless_shim", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_pins(dockerfile: str) -> list[str]:
    pins: list[str] = []
    for line in dockerfile.splitlines():
        if "torch-constraints.txt" not in line or "printf" not in line:
            continue
        pins.extend(re.findall(r"'([^']+)'", line))
    return [pin for pin in pins if pin != "%s\\n"]


def _empty_wheel(directory: Path, dist_name: str, version: str, modules: dict[str, bytes]) -> Path:
    normalized = dist_name.replace("-", "_")
    folder = f"{normalized}-{version}"
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {dist_name}\n"
        f"Version: {version}\n"
        "Summary: test double\n"
        "Requires-Python: >=3.9\n"
    ).encode()
    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: comfyfleet-test\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode()
    files = {
        f"{folder}.dist-info/METADATA": metadata,
        f"{folder}.dist-info/WHEEL": wheel_meta,
        **modules,
    }

    def record(name: str, data: bytes) -> str:
        digest = hashlib.sha256(data).digest()
        encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return f"{name},sha256={encoded},{len(data)}\n"

    body = "".join(record(name, data) for name, data in files.items())
    body += f"{folder}.dist-info/RECORD,,\n"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{normalized}-{version.replace('-', '_')}-py3-none-any.whl"
    with zipfile.ZipFile(destination, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(f"{folder}.dist-info/RECORD", body.encode())
    return destination


def _venv(directory: Path) -> Path:
    subprocess.check_call([sys.executable, "-m", "venv", str(directory)])
    python = directory / "bin" / "python"
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "-U", "pip"])
    return python


def _pip(python: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(python), "-m", "pip", *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )


def _image_env(constraint: Path, find_links: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PIP_CONSTRAINT"] = str(constraint)
    env["PIP_FIND_LINKS"] = str(find_links)
    env["PIP_ONLY_BINARY"] = "llama-cpp-python"
    env.pop("PIP_REQUIRE_VIRTUALENV", None)
    return env


class ReactorOpenCVContractTests(unittest.TestCase):
    def test_placeholder_wheel_ships_no_cv2(self):
        shim = _load_shim()
        self.assertEqual(shim.CONSTRAINT, "opencv-python-headless==99.0.0")
        self.assertEqual(shim.VERSION, "99.0.0")
        with tempfile.TemporaryDirectory() as tmp:
            path = shim.build_wheel(Path(tmp))
            self.assertEqual(path.name, shim.WHEEL_FILENAME)
            self.assertFalse(shim.wheel_ships_cv2(path))
            with zipfile.ZipFile(path) as archive:
                self.assertTrue(any(name.endswith("comfyfleet-shim") for name in archive.namelist()))

    def test_both_images_keep_torch_numpy_and_llama_pins(self):
        shim = _load_shim()
        for name, kept in KEPT_PINS.items():
            text = (ROOT / name).read_text(encoding="utf-8")
            pins = _constraint_pins(text)
            for pin in kept:
                self.assertIn(pin, pins, name)
            self.assertIn(shim.CONSTRAINT, pins, name)
            self.assertNotIn("opencv-python-headless<0", text)
            res4lyf = text.index("-r /opt/comfyfleet/baked_custom_nodes/RES4LYF/requirements.txt")
            placeholder = text.index("opencv_headless_shim.py --wheel-dir /opt/comfyfleet/wheels")
            self.assertLess(res4lyf, placeholder)


class ReactorResolverTests(unittest.TestCase):
    def test_old_constraint_fails_naming_opencv_python_headless(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = _venv(root / "venv")
            constraint = root / "constraints.txt"
            constraint.write_text("opencv-python-headless<0\nnumpy==2.2.6\n", encoding="utf-8")
            env = os.environ.copy()
            env["PIP_CONSTRAINT"] = str(constraint)
            env.pop("PIP_FIND_LINKS", None)
            completed = _pip(
                python,
                ["install", "--dry-run", "albumentations>=1.4.16"],
                env,
            )
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("opencv-python-headless", output)
        self.assertIn("loosen the range of package versions you've specified", output)
        self.assertIn("no matching distributions available for your environment", output)
        self.assertIn("The user requested (constraint) opencv-python-headless<0", output)

    def test_reactor_requirements_install_on_both_cuda_lines(self):
        shim = _load_shim()
        for name in ("Dockerfile", "Dockerfile.cu124"):
            with self.subTest(image=name):
                pins = _constraint_pins((ROOT / name).read_text(encoding="utf-8"))
                self._install_reactor(shim, pins)

    def _install_reactor(self, shim, pins: list[str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = _venv(root / "venv")
            wheels = root / "wheels"
            shim_wheel = shim.build_wheel(wheels)
            self.assertFalse(shim.wheel_ships_cv2(shim_wheel))
            preinstalled = [
                _empty_wheel(wheels, "opencv-python", OPENCV_PYTHON_VERSION, {"cv2.py": CV2_SENTINEL.encode()})
            ]
            for pin in pins:
                if pin == shim.CONSTRAINT:
                    continue
                dist_name, version = pin.split("==", 1)
                preinstalled.append(_empty_wheel(wheels, dist_name, version, {}))
            install_base = _pip(
                python,
                ["install", "-q", "--no-index", *[str(path) for path in preinstalled], str(shim_wheel)],
                os.environ.copy(),
            )
            self.assertEqual(install_base.returncode, 0, install_base.stderr)
            constraint = root / "constraints.txt"
            constraint.write_text("\n".join(pins) + "\n", encoding="utf-8")
            env = _image_env(constraint, wheels)
            for requirement in REACTOR_REQUIREMENTS:
                completed = _pip(python, ["install", requirement], env)
                output = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 0, f"{requirement}\n{output}")
                self.assertNotIn("ResolutionImpossible", output)
                self.assertNotIn("no matching distributions available for your environment", output)
            versions = _installed(python)
            self.assertEqual(versions["opencv-python-headless"], shim.VERSION)
            self.assertEqual(versions["opencv-python"], OPENCV_PYTHON_VERSION)
            self.assertEqual(versions["numpy"], _pin_version(pins, "numpy"))
            self.assertEqual(versions["torch"], _pin_version(pins, "torch"))
            self.assertEqual(versions["llama-cpp-python"], "0.3.36")
            for required in ("albumentations", "onnx", "ultralytics"):
                self.assertIn(required, versions)
            self.assertTrue(any(name.replace("_", "-") == "segment-anything" for name in versions))
            sentinel = _pip(python, ["install", "opencv-python-headless==5.0.0.93"], env)
            refusal = sentinel.stdout + sentinel.stderr
            self.assertNotEqual(sentinel.returncode, 0, refusal)
            self.assertIn("opencv-python-headless", refusal)
            self.assertEqual(_installed(python)["opencv-python-headless"], shim.VERSION)
            self.assertEqual(_installed(python)["opencv-python"], OPENCV_PYTHON_VERSION)
            probe = subprocess.check_output(
                [str(python), "-c", "import cv2; print(cv2.SENTINEL)"],
                text=True,
            )
            self.assertEqual(probe.strip(), "opencv-python")


def _pin_version(pins: list[str], name: str) -> str:
    prefix = name + "=="
    matches = [pin[len(prefix):] for pin in pins if pin.startswith(prefix)]
    if len(matches) != 1:
        raise AssertionError(f"{name} pin missing from {pins}")
    return matches[0]


def _installed(python: Path) -> dict[str, str]:
    code = (
        "import importlib.metadata as metadata\n"
        "for dist in metadata.distributions():\n"
        "    print(dist.metadata['Name'].lower() + ' ' + dist.version)\n"
    )
    output = subprocess.check_output([str(python), "-c", code], text=True)
    versions: dict[str, str] = {}
    for line in output.splitlines():
        name, version = line.split(" ", 1)
        versions[name] = version
    return versions


if __name__ == "__main__":
    unittest.main()
