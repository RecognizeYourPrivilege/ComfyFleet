"""Baked abetlen llama-cpp-python on both instance lines.

JoyCaption's Manager pip install must not source-build llama-cpp-python.
Both images bake the 0.3.36 manylinux wheel from the matching abetlen
index, pin it in the pip constraint file, and set PIP_ONLY_BINARY so a
later install cannot select the PyPI sdist. There is no runtime recipe.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = "llama-cpp-python==0.3.36"
WHEEL = "llama_cpp_python-0.3.36-py3-none-manylinux_2_35_x86_64.whl"
CU130_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu130"
CU124_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu124"
DIGESTS = (
    "sha256:3b4ad9c26b550f945cef0627707653d6015d3782782ee9e05cfcafd2fde4043a",
    "sha256:b8b5a2281761a7a438e9610182a0eb7ae94cd1d53ff02a14db393d131db91887",
    "sha256:d7fc7835d54a8b35705583daf539781535a82850494b26d92f32c55f05ab925d",
)

NM_SAMPLE = """\
                 U cuMemCreate
                 U cuInit@libcuda
                 U cudaMalloc
                 U ggml_abort
                 U __cudaRegisterFunction
                 U fclose@GLIBC_2.2.5
"""


def _load_smoke():
    path = ROOT / "docker" / "llama_import_smoke.py"
    spec = importlib.util.spec_from_file_location("llama_import_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_verify():
    path = ROOT / "docker" / "verify_image_pins.py"
    spec = importlib.util.spec_from_file_location("verify_image_pins_llama", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LlamaStubHelperTests(unittest.TestCase):
    def test_parser_keeps_driver_symbols_only(self):
        smoke = _load_smoke()
        self.assertEqual(
            smoke.undefined_cuda_driver_symbols(NM_SAMPLE),
            ["cuMemCreate", "cuInit"],
        )

    def test_stub_source_defines_driver_symbols(self):
        smoke = _load_smoke()
        source = smoke.stub_source(["cuMemCreate", "cuInit"])
        self.assertIn("void cuMemCreate(void) {}", source)
        self.assertIn("void cuInit(void) {}", source)
        self.assertNotIn("cudaMalloc", source)
        self.assertNotIn("ggml_abort", source)
        with self.assertRaises(ValueError):
            smoke.stub_source(["cudaMalloc"])

    def test_import_command_is_the_class_import(self):
        text = (ROOT / "docker" / "llama_import_smoke.py").read_text(encoding="utf-8")
        self.assertIn("from llama_cpp import Llama; print('ok')", text)
        self.assertIn("TemporaryDirectory", text)
        self.assertIn("LD_LIBRARY_PATH", text)
        compile(text, "docker/llama_import_smoke.py", "exec")


class LlamaBakeContractTests(unittest.TestCase):
    def test_cu130_dockerfile_bakes_the_cu130_wheel(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("unset CXX CC CMAKE_ARGS", text)
        self.assertIn(
            "/opt/venv/bin/python -m pip install --no-cache-dir 'llama-cpp-python==0.3.36'",
            text,
        )
        self.assertIn("--only-binary=:all:", text)
        self.assertIn(CU130_INDEX, text)
        self.assertNotIn(CU124_INDEX, text)
        self.assertNotIn("whl/cu124", text)
        self.assertIn("llama-cpp-python==0.3.36", text)
        self.assertIn("PIP_ONLY_BINARY=llama-cpp-python", text)
        self.assertIn("llama_import_smoke.py", text)
        start = text.index("RUN unset CXX CC CMAKE_ARGS")
        llama_run = text[start:text.index("/opt/venv/bin/python /opt/comfyfleet/llama_import_smoke.py", start)]
        self.assertLess(llama_run.index("unset CXX CC CMAKE_ARGS"), llama_run.index("--only-binary=:all:"))
        self.assertLess(llama_run.index("--only-binary=:all:"), llama_run.index(CU130_INDEX))
        self.assertNotIn("--force-reinstall", llama_run)
        self.assertNotIn("CMAKE_ARGS=", llama_run)
        self.assertLess(text.index("torch==2.13.0+cu130"), start)
        self.assertNotIn("--force-reinstall", text)
        self.assertNotIn("CMAKE_ARGS=", text)

    def test_cu130_installs_libc6_dev_so_the_stub_link_finds_crti(self):
        """gcc --no-install-recommends on bookworm-slim has no crti.o.

        Publish run 37069963220 installed the cu130 wheel, then the llama
        import smoke failed: ld cannot find crti.o. libc6-dev provides
        that file. python3-dev stays out (those headers are CPython 3.11).
        The cu124 line already published green. python3-dev there installs
        zlib1g-dev, which depends on libc6-dev, so that Dockerfile is
        unchanged. python3-dev itself only recommends libc6-dev.
        """

        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        apt_at = text.index(
            "apt-get install -y --no-install-recommends \\\n        cuda-libraries-13-0"
        )
        apt_block = text[apt_at:text.index("ln -sfn /usr/local/cuda-13.0", apt_at)]
        self.assertIn("\n        gcc \\\n", apt_block)
        self.assertIn("\n        libc6-dev \\\n", apt_block)
        self.assertNotIn("\n        python3-dev \\\n", apt_block)
        self.assertNotIn("build-essential", text)
        self.assertIn("crti.o", text)
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("libc6-dev", pins)
        self.assertIn("crti.o", pins)
        self.assertIn("libc6-dev", readme)
        self.assertIn("crti.o", readme)
        cu124 = (ROOT / "Dockerfile.cu124").read_text(encoding="utf-8")
        self.assertIn("\n        python3-dev \\\n", cu124)
        self.assertNotIn("libc6-dev", cu124)

    def test_cu124_dockerfile_bakes_the_cu124_wheel(self):
        text = (ROOT / "Dockerfile.cu124").read_text(encoding="utf-8")
        self.assertIn("unset CXX CC CMAKE_ARGS", text)
        self.assertIn(
            "/opt/venv/bin/python -m pip install --no-cache-dir 'llama-cpp-python==0.3.36'",
            text,
        )
        self.assertIn("--only-binary=:all:", text)
        self.assertIn(CU124_INDEX, text)
        self.assertNotIn(CU130_INDEX, text)
        self.assertNotIn("whl/cu130", text)
        self.assertIn("PIP_ONLY_BINARY=llama-cpp-python", text)
        self.assertIn("llama_import_smoke.py", text)
        self.assertLess(text.index("torch==2.6.0+cu124"), text.index("unset CXX CC CMAKE_ARGS"))
        start = text.index("RUN unset CXX CC CMAKE_ARGS")
        llama_run = text[start:text.index("/opt/venv/bin/python /opt/comfyfleet/llama_import_smoke.py", start)]
        self.assertNotIn("--force-reinstall", llama_run)
        self.assertNotIn("CMAKE_ARGS=", llama_run)

    def test_constraint_line_is_appended_for_both_images(self):
        for name in ("Dockerfile", "Dockerfile.cu124"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                "printf '%s\\n' 'llama-cpp-python==0.3.36' >> /opt/comfyfleet/torch-constraints.txt",
                text,
            )
            self.assertLess(
                text.index("printf '%s\\n' 'llama-cpp-python==0.3.36'"),
                text.index("ENV PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt"),
            )
            self.assertLess(
                text.index("ENV PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt"),
                text.index("unset CXX CC CMAKE_ARGS"),
            )

    def test_verify_checks_the_pin_and_the_import(self):
        text = (ROOT / "docker" / "verify_image_pins.py").read_text(encoding="utf-8")
        pins = _load_verify()
        self.assertIn('LLAMA_PIP_PIN = ("llama_cpp_python", "0.3.36")', text)
        self.assertIn("llama_import_smoke.py", text)
        self.assertEqual(pins.llama_pin_errors({"llama_cpp_python": "0.3.36"}), [])
        self.assertEqual(
            pins.llama_pin_errors({}),
            ["llama-cpp-python None != '0.3.36'"],
        )
        self.assertEqual(
            pins.llama_pin_errors({"llama_cpp_python": "0.3.35"}),
            ["llama-cpp-python '0.3.35' != '0.3.36'"],
        )
        compile(text, "docker/verify_image_pins.py", "exec")

    def test_pins_record_the_wheel_for_both_lines(self):
        cu130 = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        cu124 = (ROOT / "docker" / "PINS.cu124.txt").read_text(encoding="utf-8")
        for text, index, other in (
            (cu130, CU130_INDEX, CU124_INDEX),
            (cu124, CU124_INDEX, CU130_INDEX),
        ):
            self.assertIn(PIN, text)
            self.assertIn(WHEEL, text)
            self.assertIn(index, text)
            self.assertNotIn(other, text)
            self.assertIn("PIP_ONLY_BINARY", text)
            self.assertIn("JoyCaption", text)
        self.assertNotIn("optional later install", cu130)
        self.assertNotIn("optional later install", cu124)

    def test_readme_note_has_no_runtime_recipe(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(PIN, readme)
        self.assertIn(CU130_INDEX, readme)
        self.assertIn(CU124_INDEX, readme)
        self.assertIn("JoyCaption", readme)
        self.assertIn("Operators do not pip-install", readme)
        self.assertIn("A source build is not supported", readme)
        self.assertNotIn("pip install llama-cpp-python", readme)
        self.assertNotIn("unset CXX CC CMAKE_ARGS", readme)
        self.assertNotIn("--extra-index-url", readme)
        self.assertNotIn("--force-reinstall", readme)

    def test_no_create_checkbox_or_runtime_install_hatch(self):
        for rel in (
            "comfyfleet/control.py",
            "comfyfleet/cli.py",
            "comfyfleet/launch.py",
            "ui/app.js",
            "docker/entrypoint.sh",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("install_llama", text)
            self.assertNotIn("llama-cpp-python", text)
            self.assertNotIn("llama_cpp", text)

    def test_retired_ghcr_digests_are_not_install_pins(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        for digest in DIGESTS:
            self.assertNotIn(digest, install)
            self.assertNotIn(digest, readme)
            self.assertNotIn(digest, compose)
        self.assertIn("comfyfleet:cu130", install)
        self.assertIn("comfyfleet-manager:latest", compose)


if __name__ == "__main__":
    unittest.main()
