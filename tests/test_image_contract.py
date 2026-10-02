import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImageContractTests(unittest.TestCase):
    def test_dockerfile_pins_cuda_130_and_torch_and_nodes(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.14.7-slim-bookworm", text)
        self.assertIn("sys.version_info[:3] == (3, 14, 7)", text)
        self.assertIn("cuda-libraries-13-0=13.0.3-1", text)
        self.assertIn("\n        gcc \\\n", text)
        self.assertNotIn("\n        python3-dev \\\n", text)
        self.assertNotIn("build-essential", text)
        self.assertNotIn("cuda-nvcc", text)
        self.assertIn("cuda-cudart-13-0=13.0.96-1", text)
        self.assertIn("libcudnn9-cuda-13=9.20.0.48-1", text)
        self.assertIn("https://download.pytorch.org/whl/cu130", text)
        self.assertNotIn("whl/cu124", text)
        self.assertIn("torch==2.13.0+cu130", text)
        self.assertIn("torchvision==0.28.0+cu130", text)
        self.assertIn("torchaudio==2.11.0+cu130", text)
        self.assertIn("8ff6dc384ba5c410266b40e137799e049459d4f2", text)
        self.assertNotIn("llama-cpp-python", text)
        self.assertIn("14b5aaab711ad1f1306d420732a923fb058c44d7", text)
        self.assertIn("9259bc49557a92e3fc14796999468c723bd1ecdd", text)
        self.assertIn("3a9ff9eba897bf2388d6c1943b01d819ba05a0c6", text)
        self.assertIn("ComfyUI-Pixaroma", text)
        self.assertIn("ComfyUI-ComfyDock", text)
        self.assertNotIn("COPY examples", text)
        self.assertNotIn("QualitySafe", text)

    def test_numpy_pin_and_res4lyf_xcb_contract(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        entry = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        script = (ROOT / "docker" / "verify_image_pins.py").read_text(encoding="utf-8")
        commit = "3d1d69da69ee47f7647d59e1bd0967e472fccc41"
        apt_packages = (
            "libxcb1",
            "libx11-6",
            "libxext6",
            "libice6",
            "libsm6",
            "libglib2.0-0",
            "libgl1",
        )

        apt_at = dockerfile.index("apt-get install -y --no-install-recommends \\\n        cuda-libraries-13-0")
        apt_block = dockerfile[apt_at:dockerfile.index("ln -sfn /usr/local/cuda-13.0", apt_at)]
        for package in apt_packages:
            self.assertIn(f"\n        {package} \\\n", apt_block)
            self.assertIn(package, pins)
            self.assertIn(package, readme)

        self.assertIn("pip install --no-cache-dir --only-binary=numpy numpy==2.3.2", dockerfile)
        self.assertIn(
            "printf '%s\\n' 'torch==2.13.0+cu130' 'torchvision==0.28.0+cu130' 'torchaudio==2.11.0+cu130' 'numpy==2.3.2'",
            dockerfile,
        )
        self.assertIn("PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt", dockerfile)
        self.assertLess(
            dockerfile.index("printf '%s\\n' 'torch==2.13.0+cu130'"),
            dockerfile.index("ENV PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt"),
        )
        self.assertIn('numpy.__version__ == "2.3.2"', dockerfile)
        self.assertIn("https://github.com/ClownsharkBatwing/RES4LYF.git", dockerfile)
        self.assertIn(commit, dockerfile)
        self.assertIn(
            "-r /opt/comfyfleet/baked_custom_nodes/RES4LYF/requirements.txt",
            dockerfile,
        )
        self.assertIn("python /opt/comfyfleet/verify_image_pins.py", dockerfile)
        self.assertLess(
            dockerfile.index("-r /opt/comfyfleet/baked_custom_nodes/RES4LYF/requirements.txt"),
            dockerfile.index("python /opt/comfyfleet/verify_image_pins.py"),
        )

        self.assertIn('link_baked "RES4LYF"', entry)
        self.assertLess(entry.index('link_baked "RES4LYF"'), entry.index("exec /opt/venv/bin/python main.py"))

        self.assertIn('NUMPY_PIN = "2.3.2"', script)
        self.assertIn("PYTHON_PIN = (3, 14, 7)", script)
        self.assertIn('ctypes.CDLL("libxcb.so.1")', script)
        self.assertIn("libqxcb.so", script)
        self.assertIn("import cv2", script)
        compile(script, "docker/verify_image_pins.py", "exec")

        self.assertIn("numpy==2.3.2", pins)
        self.assertIn(commit, pins)
        self.assertIn("libxcb.so.1", pins)
        self.assertIn("numpy==2.3.2", readme)
        self.assertIn(commit, readme)
        self.assertIn("libxcb.so.1", readme)
        self.assertIn("`RES4LYF`", readme)

    def test_dockerignore_keeps_example_workflows_out_of_the_image(self):
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("examples", text)

    def test_entrypoint_listens_on_all_interfaces_and_refuses_a_missing_workflow(self):
        script = ROOT / "docker" / "entrypoint.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("--listen 0.0.0.0", text)
        self.assertIn('comfy_args=(--listen 0.0.0.0 --port 8188)', text)
        self.assertIn('exec /opt/venv/bin/python main.py "${comfy_args[@]}"', text)
        locked = text.index('comfy_args=(--listen 0.0.0.0 --port 8188)')
        self.assertLess(locked, text.index('exec /opt/venv/bin/python main.py "${comfy_args[@]}"'))
        # The locked pair is assigned once. Later args are appended, never a replacement.
        self.assertEqual(text.count("comfy_args=("), 1)
        self.assertIn("comfy_args+=", text)
        self.assertLess(locked, text.index("comfy_args+="))
        self.assertIn("/opt/comfyfleet/instance/default_workflow.json", text)
        env = os.environ.copy()
        env["COMFYFLEET_WORKFLOW_PATH"] = "/no/such/comfyfleet-operator-workflow.json"
        completed = subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("operator workflow missing", completed.stderr)
        self.assertNotIn("QualitySafe", completed.stdout)

    def test_loader_does_not_embed_a_workflow(self):
        node = ROOT / "docker" / "comfyfleet_default_workflow"
        blobs = []
        for path in node.rglob("*"):
            if path.is_file():
                blobs.append(path.read_text(encoding="utf-8"))
                self.assertFalse(path.suffix == ".json")
        joined = "\n".join(blobs)
        self.assertIn("/comfyfleet/default-workflow", joined)
        self.assertIn("No stock workflow will be substituted", joined)

    def test_docs_example_is_json_and_not_the_image_default(self):
        example = json.loads((ROOT / "examples" / "workflow.example.json").read_text(encoding="utf-8"))
        self.assertIsInstance(example, dict)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("workflow.example.json", dockerfile)

    def test_logo_and_spec_are_in_the_repo(self):
        self.assertTrue((ROOT / "comfyfleet-logo-ships.jpg").is_file())
        spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
        self.assertIn("FR-W1", spec)
        self.assertIn("FR-W5", spec)


if __name__ == "__main__":
    unittest.main()
