"""Instance images drop pip caches and baked-node git history.

ComfyUI itself keeps a full clone. Pinned Manager reads that repo at
startup (revision count via iter_commits, plus commit hash and date).
A depth-1 checkout would report revision 1, and a missing repo makes the
notice route say ComfyUI is not a git repo. Baked custom nodes, including
ComfyUI-Manager, do not keep .git.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTANCE_DOCKERFILES = ("Dockerfile", "Dockerfile.cu124")

COMFYUI = {
    "Dockerfile": "8ff6dc384ba5c410266b40e137799e049459d4f2",
    "Dockerfile.cu124": "6b747c0428c343e1417219641db93a4fb7cb69ae",
}

BAKED_NODES = (
    (
        "https://github.com/Comfy-Org/ComfyUI-Manager.git",
        "/opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager",
        "14b5aaab711ad1f1306d420732a923fb058c44d7",
    ),
    (
        "https://github.com/pixaroma/ComfyUI-Pixaroma.git",
        "/opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma",
        "9259bc49557a92e3fc14796999468c723bd1ecdd",
    ),
    (
        "https://github.com/RecognizeYourPrivilege/ComfyUI-ComfyDock.git",
        "/opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock",
        "3a9ff9eba897bf2388d6c1943b01d819ba05a0c6",
    ),
    (
        "https://github.com/ClownsharkBatwing/RES4LYF.git",
        "/opt/comfyfleet/baked_custom_nodes/RES4LYF",
        "3d1d69da69ee47f7647d59e1bd0967e472fccc41",
    ),
    (
        "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git",
        "/opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Pack",
        "429d0159ad429e64d2b3916e6e7be9c22d025c3c",
    ),
    (
        "https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git",
        "/opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Subpack",
        "50c7b71a6a224734cc9b21963c6d1926816a97f1",
    ),
)


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buffer: list[str] = []
    for raw in text.splitlines():
        if raw.endswith("\\"):
            buffer.append(raw[:-1])
            continue
        buffer.append(raw)
        lines.append("\n".join(buffer))
        buffer = []
    if buffer:
        lines.append("\n".join(buffer))
    return lines


def _instructions(text: str) -> list[tuple[str, str]]:
    instructions: list[tuple[str, str]] = []
    for line in _logical_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        token, _, body = stripped.partition(" ")
        instructions.append((token, body))
    return instructions


class InstanceImageSlimTests(unittest.TestCase):
    def test_pip_cache_is_disabled_and_removed_in_each_install_layer(self):
        for name in INSTANCE_DOCKERFILES:
            text = (ROOT / name).read_text(encoding="utf-8")
            instructions = _instructions(text)
            envs = [body for token, body in instructions if token == "ENV"]
            self.assertTrue(envs, name)
            self.assertIn("PIP_NO_CACHE_DIR=1", envs[0], name)
            self.assertLess(text.index("PIP_NO_CACHE_DIR=1"), text.index("pip install"), name)
            pip_runs = [body for token, body in instructions if token == "RUN" and "pip install" in body]
            self.assertGreaterEqual(len(pip_runs), 3, name)
            for body in pip_runs:
                self.assertIn("rm -rf /root/.cache/pip", body, name)

    def test_sam2_uses_the_installed_torch_without_build_isolation(self):
        for name in INSTANCE_DOCKERFILES:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                "SAM2_BUILD_CUDA=0 pip install --no-cache-dir --no-build-isolation "
                "-c /opt/comfyfleet/torch-constraints.txt -r /tmp/impact-requirements.txt",
                text,
                name,
            )
            self.assertIn("wheel 'setuptools>=70.1'", text, name)
            self.assertIn("from setuptools.command.bdist_wheel import bdist_wheel", text, name)

    def test_baked_nodes_are_shallow_and_drop_git_except_comfyui(self):
        for name in INSTANCE_DOCKERFILES:
            text = (ROOT / name).read_text(encoding="utf-8")
            comfy_sha = COMFYUI[name]
            self.assertIn("ComfyUI keeps its .git", text, name)
            self.assertIn("iter_commits", text, name)
            self.assertIn("Your ComfyUI isn't git repo.", text, name)
            self.assertIn("git clone https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI", text, name)
            self.assertIn(f"git -C /opt/ComfyUI checkout {comfy_sha}", text, name)
            self.assertNotIn("rm -rf /opt/ComfyUI/.git", text, name)
            self.assertNotIn(f"fetch --depth 1 origin {comfy_sha}", text, name)
            self.assertIn('git -C "$dest" fetch --depth 1 origin "$rev"', text, name)
            self.assertIn('rm -rf "$dest/.git"', text, name)
            self.assertIn(
                "find /opt/comfyfleet/baked_custom_nodes /opt/comfyfleet/stock_custom_nodes "
                "-depth -name .git -exec rm -rf {} +",
                text,
                name,
            )
            self.assertNotIn("find /opt/ComfyUI", text, name)
            clone_lines = [
                line.strip().rstrip("\\").strip()
                for line in text.splitlines()
                if "git clone" in line and not line.strip().startswith("#")
            ]
            self.assertEqual(
                clone_lines,
                ["&& git clone https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI"],
                name,
            )
            for url, dest, rev in BAKED_NODES:
                self.assertIn(f"pin_git {url} {dest} {rev}", text, name)
                self.assertNotIn(f"git clone {url}", text, name)

    def test_manager_image_is_unchanged_by_the_instance_cache_rule(self):
        text = (ROOT / "Dockerfile.manager").read_text(encoding="utf-8")
        self.assertNotIn("PIP_NO_CACHE_DIR", text)
        self.assertNotIn("pin_git", text)
        self.assertIn("pip install --no-cache-dir /opt/comfyfleet", text)


if __name__ == "__main__":
    unittest.main()
