import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker" / "patch_comfy_kitchen_torch26.py"


def _load():
    spec = importlib.util.spec_from_file_location("patch_comfy_kitchen_torch26", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATCH = _load()


SAMPLE = '''\
"""fixture"""
import torch

def helper(stride: list[int]) -> None:
    return None

@torch.library.custom_op("comfy_kitchen::fp16_conv3d_out", mutates_args=("out",))
def _op_fp16_conv3d_out(
    stride: list[int],
    out: torch.Tensor,
) -> None:
    return None

@torch.library.custom_op("comfy_kitchen::na3d", mutates_args=())
def _op_na3d(kernel_size: list[int], is_causal: list[bool]) -> torch.Tensor:
    return out
'''


class KitchenTorch26PatchTests(unittest.TestCase):
    def test_rewrites_only_custom_op_list_annotations(self):
        updated, count = PATCH.rewrite_source(SAMPLE)
        self.assertEqual(count, 3)
        self.assertIn("import typing", updated)
        self.assertIn("def helper(stride: list[int])", updated)
        self.assertIn("stride: typing.List[int]", updated)
        self.assertIn("kernel_size: typing.List[int]", updated)
        self.assertIn("is_causal: typing.List[bool]", updated)
        self.assertEqual(PATCH.custom_op_builtin_list_annotations(updated), [])
        ast.parse(updated)

    def test_rewrite_is_idempotent(self):
        once, count = PATCH.rewrite_source(SAMPLE)
        twice, again = PATCH.rewrite_source(once)
        self.assertGreater(count, 0)
        self.assertEqual(again, 0)
        self.assertEqual(twice, once)

    def test_union_annotation_on_a_custom_op_is_rewritten(self):
        source = (
            "import torch\n"
            "@torch.library.custom_op('comfy_kitchen::demo', mutates_args=())\n"
            "def _op(pad: list[int] | None) -> torch.Tensor:\n"
            "    return pad\n"
        )
        updated, count = PATCH.rewrite_source(source)
        self.assertEqual(count, 1)
        self.assertIn("pad: typing.List[int] | None", updated)
        self.assertIn("import typing", updated)

    def test_leaves_a_custom_op_without_builtin_lists_untouched(self):
        source = (
            "import torch\n"
            "@torch.library.custom_op('comfy_kitchen::demo', mutates_args=())\n"
            "def _op(x: torch.Tensor) -> torch.Tensor:\n"
            "    return x\n"
        )
        updated, count = PATCH.rewrite_source(source)
        self.assertEqual(count, 0)
        self.assertEqual(updated, source)

    def test_image_contract_pins_pure_wheel_and_runs_the_rewrite(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        wheel = (
            "https://files.pythonhosted.org/packages/38/23/"
            "a6787aac01d7c28ae3cb07579ba839297a35e6fad66baac096916246cc7f/"
            "comfy_kitchen-0.2.36-py3-none-any.whl"
        )
        self.assertIn(wheel, dockerfile)
        self.assertIn(wheel, pins)
        self.assertIn("patch_comfy_kitchen_torch26.py", dockerfile)
        self.assertNotIn("import comfy_kitchen", dockerfile)
        self.assertIn("0 active drivers", dockerfile)
        self.assertIn("Failed to find C compiler", dockerfile)
        self.assertIn("\n        gcc \\\n", dockerfile)
        self.assertIn("\n        python3-dev \\\n", dockerfile)
        self.assertNotIn("build-essential", dockerfile)
        self.assertNotIn("cuda-nvcc", dockerfile)
        self.assertIn("torch==2.6.0+cu124", dockerfile)
        self.assertIn("typing.List", pins)
        self.assertNotIn("comfy_kitchen-0.2.36-cp311", dockerfile)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("patch_comfy_kitchen_torch26.py", readme)
        self.assertIn("py3-none-any", readme)
        self.assertIn("python3-dev", readme)
        self.assertIn("Failed to find C compiler", readme)
        self.assertIn("gcc", pins)
        self.assertIn("python3-dev", pins)
        self.assertIn("Failed to find C compiler", pins)

    def test_build_check_imports_patched_eager_modules_only(self):
        source = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertFalse(any(name == "comfy_kitchen" or name.startswith("comfy_kitchen.") for name in imported))
        self.assertNotIn("triton", imported)
        self.assertIn("verify_patched_eager_ops(root)", source)
        self.assertEqual(
            PATCH._eager_module_names(),
            (
                "comfy_kitchen.backends.eager.conv3d",
                "comfy_kitchen.backends.eager.group_norm_pad3d",
                "comfy_kitchen.backends.eager.na",
                "comfy_kitchen.backends.eager.sol_attn",
            ),
        )
        self.assertEqual(
            PATCH.EXPECTED_SCHEMA_MARKERS["fp16_conv3d"],
            ("SymInt[] stride",),
        )
        self.assertEqual(
            PATCH.EXPECTED_SCHEMA_MARKERS["na3d"],
            ("SymInt[] kernel_size", "bool[] is_causal"),
        )
        self.assertIn("0 active drivers", (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
