#!/usr/bin/env python3
"""Rewrite comfy_kitchen custom-op annotations so torch 2.6 can import them.

comfy-kitchen 0.2.36 registers ``torch.library.custom_op`` functions with PEP
585 annotations (``list[int]``, ``list[bool]``). ``torch._library.infer_schema``
in torch 2.6.0 accepts ``typing.List[int]`` / ``typing.List[bool]`` and rejects
the builtin generics, so ``import comfy_kitchen`` dies in
``backends/eager/conv3d.py`` before ComfyUI starts.

Torch 2.7 learned the builtin forms. There is no stable torch>=2.7 cu124
wheel, so this image keeps torch 2.6.0+cu124 and rewrites only the custom-op
parameter annotations. ``float | None`` and ``tuple[torch.Tensor, ...]``
returns already match what torch 2.6 accepts and are left alone.

The installed distribution must stay the pure-Python wheel. The manylinux
wheel targets CUDA 13.

``import comfy_kitchen`` also imports the Triton backend. That backend
decorates kernels with ``@triton.autotune``, which initializes Triton's driver
and raises ``RuntimeError: 0 active drivers`` when the process has no NVIDIA
driver. Image builds have no driver, so the build check imports only the
patched eager modules. Those modules are what ``infer_schema`` rejects, and
loading them does not import Triton.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import importlib.util
import shutil
import sys
import types
from pathlib import Path

EXPECTED_VERSION = "0.2.36"
# These modules are the 0.2.36 custom ops whose parameters use builtin lists.
# The import crash is the first of them (conv3d); the others fail the same way
# once conv3d is fixed.
REQUIRED_RELATIVE_PATHS = (
    "backends/eager/conv3d.py",
    "backends/eager/group_norm_pad3d.py",
    "backends/eager/na.py",
    "backends/eager/sol_attn.py",
)
# torch 2.6 infer_schema turns typing.List[int] into SymInt[] and
# typing.List[bool] into bool[]. Builtin list[...] never reaches a schema:
# infer_schema raises ValueError while the module is imported.
EXPECTED_SCHEMA_MARKERS = {
    "fp16_conv3d": ("SymInt[] stride",),
    "fp16_conv3d_out": ("SymInt[] stride",),
    "group_norm_silu_pad3d": ("SymInt[] pad",),
    "group_norm_silu_pad3d_out": ("SymInt[] pad",),
    "na3d": ("SymInt[] kernel_size", "bool[] is_causal"),
    "sol_attn": ("SymInt[] sink_blocks", "SymInt[] sink_q"),
}


def _span(source: str, node: ast.AST) -> tuple[int, int]:
    lines = source.splitlines(keepends=True)
    if node.end_lineno is None or node.end_col_offset is None:
        raise ValueError("AST node is missing end positions")
    start = sum(len(lines[i]) for i in range(node.lineno - 1)) + node.col_offset
    end = sum(len(lines[i]) for i in range(node.end_lineno - 1)) + node.end_col_offset
    return start, end


def _is_custom_op(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        segment = ast.get_source_segment(source, decorator) or ""
        if "custom_op" in segment:
            return True
    return False


def _has_import_typing(tree: ast.AST) -> bool:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import) and any(alias.name == "typing" for alias in node.names):
            return True
    return False


def _typing_import_offset(source: str, tree: ast.AST) -> int:
    """Offset just before the newline that ends the leading import block."""
    last_import: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import = node
            continue
        break
    if last_import is None:
        return 0
    _, end = _span(source, last_import)
    newline = source.find("\n", end)
    return len(source) if newline < 0 else newline


def _rewrite_annotation(text: str) -> str:
    return text.replace("list[int]", "typing.List[int]").replace("list[bool]", "typing.List[bool]")


def rewrite_source(source: str) -> tuple[str, int]:
    """Return rewritten source and how many custom-op annotations changed.

    Non-custom-op signatures are not modified. A second call is a no-op.
    """
    tree = ast.parse(source)
    edits: list[tuple[int, int, str]] = []
    changed = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_custom_op(source, node):
            continue
        arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        for arg in arguments:
            if arg.annotation is None:
                continue
            start, end = _span(source, arg.annotation)
            original = source[start:end]
            updated = _rewrite_annotation(original)
            if updated != original:
                edits.append((start, end, updated))
                changed += 1
    if changed and not _has_import_typing(tree):
        offset = _typing_import_offset(source, tree)
        edits.append((offset, offset, "\nimport typing"))
    if not edits:
        return source, 0
    pieces: list[str] = []
    cursor = 0
    for start, end, text in sorted(edits, key=lambda item: item[0]):
        if start < cursor:
            raise ValueError("overlapping annotation edits")
        pieces.append(source[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces), changed


def custom_op_builtin_list_annotations(source: str, path: Path | None = None) -> list[str]:
    """Annotations on custom ops that still use builtin ``list[...]``."""
    tree = ast.parse(source)
    found: list[str] = []
    label = str(path) if path is not None else "<source>"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_custom_op(source, node):
            continue
        arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        for arg in arguments:
            if arg.annotation is None:
                continue
            text = ast.get_source_segment(source, arg.annotation) or ""
            if "list[" in text:
                found.append(f"{label}:{node.lineno} {node.name} {arg.arg}: {text}")
    return found


def _namespace_module(name: str, path: Path) -> None:
    """Register ``name`` without executing a package ``__init__``.

    A normal ``import comfy_kitchen`` runs ``__init__.py``, which imports the
    Triton backend. The same is true of ``backends/eager/__init__.py``: it
    pulls the rest of the eager package, and that path imports Triton through
    PyTorch. The patched custom ops live in the four eager modules below.
    """
    if name in sys.modules:
        raise RuntimeError(f"{name} is already imported")
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    module.__file__ = str(path / "__init__.py")
    sys.modules[name] = module
    parent, _, attr = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], attr, module)


def _eager_module_names() -> tuple[str, ...]:
    return tuple(
        "comfy_kitchen." + relative[:-3].replace("/", ".")
        for relative in REQUIRED_RELATIVE_PATHS
    )


def verify_patched_eager_ops(root: Path) -> dict[str, str]:
    """Import patched eager modules so torch 2.6 ``infer_schema`` runs.

    Returns ``op name -> schema`` for the custom ops whose parameters were
    builtin lists. Raises if an annotation is still rejected, an expected op
    is missing, or the import loaded Triton.
    """
    triton_before = "triton" in sys.modules
    _namespace_module("comfy_kitchen", root)
    _namespace_module("comfy_kitchen.backends", root / "backends")
    _namespace_module("comfy_kitchen.backends.eager", root / "backends" / "eager")
    for module_name in _eager_module_names():
        importlib.import_module(module_name)
    if "comfy_kitchen.backends.triton" in sys.modules or "comfy_kitchen.backends.cuda" in sys.modules:
        raise RuntimeError("eager custom-op check imported a GPU backend")
    if not triton_before and "triton" in sys.modules:
        raise RuntimeError("eager custom-op check imported triton")

    import torch

    schemas: dict[str, str] = {}
    for name, markers in EXPECTED_SCHEMA_MARKERS.items():
        try:
            packet = getattr(torch.ops.comfy_kitchen, name)
        except AttributeError as exc:
            raise RuntimeError(f"custom op comfy_kitchen::{name} was not registered") from exc
        schema = " | ".join(str(value) for value in packet._schemas.values())
        missing = [marker for marker in markers if marker not in schema]
        if missing:
            raise RuntimeError(
                f"comfy_kitchen::{name} schema missing {missing}: {schema}"
            )
        schemas[name] = schema
    return schemas


def _purge_bytecode(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache)
    for compiled in root.rglob("*.pyc"):
        compiled.unlink()


def patch_installed_package() -> int:
    spec = importlib.util.find_spec("comfy_kitchen")
    if spec is None or not spec.origin:
        print("comfy_kitchen is not installed", file=sys.stderr)
        return 1
    version = importlib.metadata.version("comfy-kitchen")
    if version != EXPECTED_VERSION:
        print(f"expected comfy-kitchen {EXPECTED_VERSION}, found {version}", file=sys.stderr)
        return 1
    wheel = importlib.metadata.distribution("comfy-kitchen").read_text("WHEEL") or ""
    if "Root-Is-Purelib: true" not in wheel or "Tag: py3-none-any" not in wheel:
        print("comfy-kitchen must be the pure-Python py3-none-any wheel, not the CUDA 13 manylinux build", file=sys.stderr)
        print(wheel, file=sys.stderr)
        return 1

    root = Path(spec.origin).resolve().parent
    changed_files = 0
    changed_annotations = 0
    for path in sorted(root.rglob("*.py")):
        original = path.read_text(encoding="utf-8")
        updated, count = rewrite_source(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
            changed_annotations += count

    _purge_bytecode(root)

    remaining: list[str] = []
    for path in sorted(root.rglob("*.py")):
        remaining.extend(custom_op_builtin_list_annotations(path.read_text(encoding="utf-8"), path))
    if remaining:
        print("custom_op annotations still use builtin list[...]:", file=sys.stderr)
        for item in remaining:
            print(item, file=sys.stderr)
        return 1

    for relative in REQUIRED_RELATIVE_PATHS:
        path = root / relative
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
        if "typing.List[int]" not in text and "typing.List[bool]" not in text:
            print(f"{relative} has no typing.List custom-op annotation after rewrite", file=sys.stderr)
            return 1

    schemas = verify_patched_eager_ops(root)
    print(
        f"patched comfy_kitchen {version}: {changed_annotations} custom-op annotations in {changed_files} files"
    )
    print(
        "eager infer_schema registered "
        f"{len(schemas)} custom ops without importing triton"
    )
    return 0


def main() -> int:
    return patch_installed_package()


if __name__ == "__main__":
    raise SystemExit(main())
