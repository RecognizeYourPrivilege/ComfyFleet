#!/usr/bin/env python3
"""Import ``llama_cpp.Llama`` during the image build.

The abetlen wheel's ``libggml-cuda`` is linked to ``libcuda.so.1``. That
file is the host NVIDIA driver. It is not part of this image, and
``docker build`` does not mount it. The dynamic loader resolves those
driver symbols when the class is imported, so a straight import fails
the build with ``libcuda.so.1: cannot open shared object file`` or
``undefined symbol``.

This script tries the import. If the driver library is missing, it
compiles a temporary stub that defines the undefined ``cu*`` driver
symbols, retries the import with that directory on ``LD_LIBRARY_PATH``,
and deletes the stub. The stub is not installed onto the runtime linker
path, so a started container still uses the host driver.

The import does not construct a ``Llama`` and does not read a model.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

IMPORT_CODE = "from llama_cpp import Llama; print('ok')"
_DRIVER_SYMBOL = re.compile(r"cu[A-Z][A-Za-z0-9_]*\Z")


def undefined_cuda_driver_symbols(nm_text: str) -> list[str]:
    """Undefined CUDA driver symbols (``cuInit``), not the runtime (``cudaMalloc``).

    ``nm -D --undefined-only`` rows look like ``U cuMemCreate`` or
    ``U cuMemCreate@GLIBC``. Version suffixes are stripped. Names that
    are not C identifiers are dropped.
    """

    symbols: list[str] = []
    seen: set[str] = set()
    for line in nm_text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[-2] != "U":
            continue
        name = parts[-1].split("@", 1)[0]
        if not _DRIVER_SYMBOL.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        symbols.append(name)
    return symbols


def stub_source(symbols: list[str]) -> str:
    """C source for a soname-``libcuda.so.1`` stub. Empty bodies are enough.

    Import only needs the symbols to exist. It does not call them.
    """

    lines = [
        "/* temporary libcuda.so.1 symbol stub for the image-build import */",
    ]
    for name in symbols:
        if not _DRIVER_SYMBOL.fullmatch(name):
            raise ValueError(f"refusing non-driver symbol {name!r}")
        lines.append(f"void {name}(void) {{}}")
    lines.append("")
    return "\n".join(lines)


def ggml_cuda_library() -> Path:
    """Path to the installed wheel's ``libggml-cuda``, without loading it."""

    spec = importlib.util.find_spec("llama_cpp")
    if spec is None or not spec.origin:
        sys.exit("comfyfleet: llama_cpp is not installed")
    lib_dir = Path(spec.origin).resolve().parent / "lib"
    preferred = lib_dir / "libggml-cuda.so.0"
    if preferred.is_file():
        return preferred
    matches = sorted(path for path in lib_dir.glob("libggml-cuda.so*") if path.is_file())
    if not matches:
        sys.exit(f"comfyfleet: libggml-cuda missing under {lib_dir}")
    return matches[0]


def _run_import(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", IMPORT_CODE],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _compile_stub(symbols: list[str], directory: Path) -> Path:
    source = directory / "libcuda_stub.c"
    library = directory / "libcuda.so.1"
    source.write_text(stub_source(symbols), encoding="utf-8")
    completed = subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-Wl,-soname,libcuda.so.1",
            "-o",
            str(library),
            str(source),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        sys.exit(
            "comfyfleet: gcc failed to compile the libcuda.so.1 import stub\n"
            f"{completed.stderr}"
        )
    return library


def main() -> None:
    env = os.environ.copy()
    first = _run_import(env)
    if first.returncode == 0:
        sys.stdout.write(first.stdout)
        return

    library = ggml_cuda_library()
    nm = subprocess.run(
        ["nm", "-D", "--undefined-only", str(library)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if nm.returncode != 0:
        sys.exit(f"comfyfleet: nm failed for {library}\n{nm.stderr}\n{first.stderr}")
    symbols = undefined_cuda_driver_symbols(nm.stdout)
    if not symbols:
        sys.exit(
            "comfyfleet: libggml-cuda has no undefined CUDA driver symbols\n"
            f"{first.stderr}"
        )

    with tempfile.TemporaryDirectory(prefix="comfyfleet-libcuda-") as tmp:
        _compile_stub(symbols, Path(tmp))
        stub_env = dict(env)
        previous = stub_env.get("LD_LIBRARY_PATH", "")
        stub_env["LD_LIBRARY_PATH"] = tmp if not previous else f"{tmp}{os.pathsep}{previous}"
        second = _run_import(stub_env)

    if second.returncode != 0:
        sys.exit(
            "comfyfleet: from llama_cpp import Llama failed\n"
            f"{first.stderr}\n{second.stderr}"
        )
    sys.stdout.write(second.stdout)


if __name__ == "__main__":
    main()
