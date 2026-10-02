"""Manager logs "PyTorch is not installed" from `pip list`, not from importing torch.

Pinned ComfyUI-Manager 14b5aaab `PIPFixer.fix_broken` (glob/manager_util.py)
runs after `python -m pip install <package>`. The operator line is:

    ComfyUI-Manager: EXECUTE => ['/opt/venv/bin/python', '-m', 'pip', 'install', 'cryptography']
    [ERROR] [ComfyUI-Manager] PyTorch is not installed

The snapshot is taken before that install. It is the name/version map from
`python -m pip list`. The error is logged when torch, torchvision, or
torchaudio is absent. An image that installed torch and torchvision
and left torchaudio out of `pip list` hit that log on a cryptography
install, which does not touch torch. This image installs
torch 2.13.0+cu130, torchvision 0.28.0+cu130, and torchaudio 2.11.0+cu130
so the snapshot contains all three names.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker" / "verify_image_pins.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_image_pins", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINS = _load()

# `pip list` columns, as Manager splits them. cryptography is not in the
# pre-install snapshot; it is what the operator asked Manager to install.
IMAGE_PIP_LIST_WITHOUT_TORCHAUDIO = """\
Package            Version
------------------ -----------
numpy              2.3.2
pip                23.0.1
torch              2.13.0+cu130
torchvision        0.28.0+cu130
"""

IMAGE_PIP_LIST_WITH_TORCHAUDIO = (
    IMAGE_PIP_LIST_WITHOUT_TORCHAUDIO + "torchaudio         2.11.0+cu130\n"
)


class ManagerTorchVisibilityTests(unittest.TestCase):
    def test_cryptography_install_snapshot_without_torchaudio_logs_the_error(self):
        versions = PINS.parse_manager_pip_list(IMAGE_PIP_LIST_WITHOUT_TORCHAUDIO)
        self.assertEqual(versions["torch"], "2.13.0+cu130")
        self.assertEqual(versions["torchvision"], "0.28.0+cu130")
        self.assertNotIn("torchaudio", versions)
        self.assertNotIn("cryptography", versions)
        self.assertEqual(
            PINS.stock_manager_pytorch_log(versions),
            "[ComfyUI-Manager] PyTorch is not installed",
        )
        # The package being installed does not remove torch from the snapshot.
        after = dict(versions)
        after["cryptography"] = "44.0.0"
        self.assertEqual(
            PINS.stock_manager_pytorch_log(after),
            "[ComfyUI-Manager] PyTorch is not installed",
        )

    def test_cu130_trio_does_not_log_pytorch_missing(self):
        versions = PINS.parse_manager_pip_list(IMAGE_PIP_LIST_WITH_TORCHAUDIO)
        self.assertIsNone(PINS.stock_manager_pytorch_log(versions))
        self.assertEqual(PINS.manager_torch_pin_errors(versions), [])
        after = dict(versions)
        after["cryptography"] = "44.0.0"
        self.assertIsNone(PINS.stock_manager_pytorch_log(after))

    def test_each_missing_package_is_the_same_log_line(self):
        present = {
            "torch": "2.13.0+cu130",
            "torchvision": "0.28.0+cu130",
            "torchaudio": "2.11.0+cu130",
        }
        for name in ("torch", "torchvision", "torchaudio"):
            snapshot = dict(present)
            del snapshot[name]
            self.assertEqual(
                PINS.stock_manager_pytorch_log(snapshot),
                "[ComfyUI-Manager] PyTorch is not installed",
            )
            self.assertTrue(any(name in error for error in PINS.manager_torch_pin_errors(snapshot)))

    def test_version_drift_is_not_the_missing_pytorch_log(self):
        # The elif in fix_broken restores torch. It does not emit this log.
        drifted = {
            "torch": "2.13.0+cu130",
            "torchvision": "0.28.0+cu130",
            "torchaudio": "2.10.0+cu130",
        }
        self.assertIsNone(PINS.stock_manager_pytorch_log(drifted))
        errors = PINS.manager_torch_pin_errors(drifted)
        self.assertEqual(errors, ["torchaudio '2.10.0+cu130' != '2.11.0+cu130'"])
        self.assertFalse(any("PyTorch is not installed" in error for error in errors))

    def test_parser_skips_headers_and_keeps_local_versions(self):
        text = "Package Version\n------- -------\nOpenCV-Python 4.10.0\ntorch 2.13.0+cu130\n"
        parsed = PINS.parse_manager_pip_list(text)
        self.assertEqual(parsed, {"opencv_python": "4.10.0", "torch": "2.13.0+cu130"})

    def test_image_installs_and_constrains_the_cu130_trio(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")

        install_at = dockerfile.index(
            "pip install --no-cache-dir \\\n"
            "        torch==2.13.0+cu130 \\\n"
            "        torchvision==0.28.0+cu130 \\\n"
            "        torchaudio==2.11.0+cu130 \\\n"
            "        --index-url https://download.pytorch.org/whl/cu130 \\"
        )
        constraint_at = dockerfile.index("ENV PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt")
        self.assertLess(install_at, constraint_at)
        self.assertIn(
            "printf '%s\\n' 'torch==2.13.0+cu130' 'torchvision==0.28.0+cu130' "
            "'torchaudio==2.11.0+cu130' 'numpy==2.3.2'",
            dockerfile,
        )
        self.assertLess(
            dockerfile.index("printf '%s\\n' 'torch==2.13.0+cu130'"),
            constraint_at,
        )
        self.assertIn(
            '(("torch", "2.13.0+cu130"), ("torchvision", "0.28.0+cu130"), ("torchaudio", "2.11.0+cu130"))',
            dockerfile,
        )
        # The torch pin stays the cu130 wheel. The constraint file is not dropped.
        self.assertIn("https://download.pytorch.org/whl/cu130", dockerfile)
        self.assertNotIn("whl/cu124", dockerfile)
        self.assertNotIn("whl/cu128", dockerfile)
        self.assertNotIn("torch==2.13.0\n", dockerfile)
        self.assertIn("python /opt/comfyfleet/verify_image_pins.py", dockerfile)
        self.assertIn("sys.version_info[:3] == (3, 14, 7)", dockerfile)

        for name in ("torch==2.13.0+cu130", "torchvision==0.28.0+cu130", "torchaudio==2.11.0+cu130"):
            self.assertIn(name, pins)
            self.assertIn(name, readme)
        self.assertIn("torchaudio-2.11.0+cu130-cp314-cp314-manylinux_2_28_x86_64.whl", pins)
        self.assertIn("PyTorch has not published torchaudio 2.13 on cu130", readme)
        self.assertIn("PyTorch is not installed", pins)
        self.assertIn("pip list", pins)
        self.assertIn("create --force", readme)
        self.assertIn("start` and `restart` alone keep the existing container layers", readme)
        self.assertIn("CUDA 13.0", readme)
        self.assertIn("floating `:phase1` tag does not move a digest-pinned install", readme)
        self.assertIn('MANAGER_PYTORCH_MISSING_LOG = "[ComfyUI-Manager] PyTorch is not installed"', script)
        self.assertIn("parse_manager_pip_list", script)
        self.assertIn('NUMPY_PIN = "2.3.2"', script)
        self.assertIn("PYTHON_PIN = (3, 14, 7)", script)
        compile(script, "docker/verify_image_pins.py", "exec")


if __name__ == "__main__":
    unittest.main()
