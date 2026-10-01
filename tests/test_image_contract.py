import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImageContractTests(unittest.TestCase):
    def test_dockerfile_pins_cuda_124_and_torch_and_nodes(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM debian:bookworm-slim", text)
        self.assertIn("cuda-libraries-12-4=12.4.1-1", text)
        self.assertIn("\n        gcc \\\n", text)
        self.assertIn("\n        python3-dev \\\n", text)
        self.assertNotIn("build-essential", text)
        self.assertNotIn("cuda-nvcc", text)
        self.assertIn("cuda-cudart-12-4=12.4.127-1", text)
        self.assertIn("libcudnn9-cuda-12=9.1.0.70-1", text)
        self.assertIn("https://download.pytorch.org/whl/cu124", text)
        self.assertIn("torch==2.6.0+cu124", text)
        self.assertIn("torchvision==0.21.0+cu124", text)
        self.assertIn("6b747c0428c343e1417219641db93a4fb7cb69ae", text)
        self.assertIn("14b5aaab711ad1f1306d420732a923fb058c44d7", text)
        self.assertIn("9259bc49557a92e3fc14796999468c723bd1ecdd", text)
        self.assertIn("3a9ff9eba897bf2388d6c1943b01d819ba05a0c6", text)
        self.assertIn("3d1d69da69ee47f7647d59e1bd0967e472fccc41", text)
        self.assertIn("numpy==2.2.6", text)
        self.assertIn("\n        libxcb1 \\\n", text)
        self.assertIn("\n        libgl1 \\\n", text)
        self.assertIn("\n        libglib2.0-0 \\\n", text)
        self.assertIn("ComfyUI-Pixaroma", text)
        self.assertIn("ComfyUI-ComfyDock", text)
        self.assertIn("RES4LYF", text)
        self.assertIn("cv2.medianBlur", text)
        self.assertIn('assert numpy.__version__ == "2.2.6"', text)
        self.assertNotIn("COPY examples", text)
        self.assertNotIn("QualitySafe", text)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        for doc in (readme, pins):
            self.assertIn("numpy==2.2.6", doc)
            self.assertIn("3d1d69da69ee47f7647d59e1bd0967e472fccc41", doc)
            self.assertIn("libxcb1", doc)
            self.assertIn("https://github.com/ClownsharkBatwing/RES4LYF", doc)

    def test_dockerignore_keeps_example_workflows_out_of_the_image(self):
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("examples", text)

    def test_entrypoint_listens_on_all_interfaces_and_refuses_a_missing_workflow(self):
        script = ROOT / "docker" / "entrypoint.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("--listen 0.0.0.0", text)
        self.assertIn('comfy_args=(--listen 0.0.0.0 --port 8188)', text)
        self.assertIn('link_baked "RES4LYF"', text)
        self.assertNotIn("--listen 127.0.0.1", text)
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
