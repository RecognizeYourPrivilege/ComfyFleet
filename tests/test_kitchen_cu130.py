"""Torch 2.13 accepts PEP 585 custom-op annotations, so the torch 2.6 rewrite is gone.

ComfyUI v0.37.4 installs comfy-kitchen==0.2.35 from its requirements file.
On CPython 3.14 pip selects the cp312-abi3 manylinux wheel. The image does
not force the pure-Python wheel and does not import comfy_kitchen at build
time. gcc stays, for Triton 3.7.1. Debian python3-dev does not: those
headers are CPython 3.11.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class KitchenCu130Tests(unittest.TestCase):
    def test_torch26_annotation_rewrite_is_not_in_the_image(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "docker" / "patch_comfy_kitchen_torch26.py").is_file())
        self.assertNotIn("patch_comfy_kitchen_torch26.py", dockerfile)
        cu124 = (ROOT / "Dockerfile.cu124").read_text(encoding="utf-8")
        self.assertIn("patch_comfy_kitchen_torch26.py", cu124)
        self.assertIn("torch==2.6.0+cu124", cu124)
        self.assertNotIn("comfy_kitchen-0.2.36", dockerfile)
        self.assertNotIn("import comfy_kitchen", dockerfile)
        self.assertIn("comfy-kitchen==0.2.35", dockerfile)
        self.assertIn("comfy-kitchen==0.2.35", pins)
        self.assertIn("comfy-kitchen==0.2.35", readme)
        self.assertIn("0 active drivers", dockerfile)
        self.assertIn("Failed to find C compiler", dockerfile)
        self.assertIn("\n        gcc \\\n", dockerfile)
        self.assertNotIn("\n        python3-dev \\\n", dockerfile)
        self.assertNotIn("build-essential", dockerfile)
        self.assertNotIn("cuda-nvcc", dockerfile)
        self.assertIn("torch==2.13.0+cu130", dockerfile)
        self.assertIn("Failed to find C compiler", readme)
        self.assertIn("python3-dev", readme)
        self.assertIn("gcc", pins)
        self.assertIn("python3-dev", pins)
        self.assertIn("Failed to find C compiler", pins)
        self.assertIn("0 active drivers", pins)
        self.assertNotIn("llama-cpp-python", dockerfile)
        self.assertIn("llama-cpp-python", readme)
        self.assertIn("https://abetlen.github.io/llama-cpp-python/whl/cu130", readme)
        self.assertIn("https://abetlen.github.io/llama-cpp-python/whl/cu130", pins)


if __name__ == "__main__":
    unittest.main()
