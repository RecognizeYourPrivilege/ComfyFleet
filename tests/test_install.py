"""GHCR install and publish contract. Does not pull or build images."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTANCE = "ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:cfa4afde856b909a8d3878688cb22eb3c65d17fe4e20efb22a959a3ce9890e75"
MANAGER = "ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:766e70fb3b70650269c8d2cac495f85b1ccba5390eafa14a2cf9767d309c5e2a"


class InstallScriptTests(unittest.TestCase):
    def test_install_script_pulls_and_starts_the_manager(self):
        script = ROOT / "install.sh"
        text = script.read_text(encoding="utf-8")
        self.assertTrue(script.stat().st_mode & 0o111, "install.sh must be executable")
        self.assertIn("recognizeyourprivilege", text)
        self.assertIn("ghcr.io", text)
        self.assertIn("comfyfleet:phase1", text)
        self.assertIn("comfyfleet-manager", text)
        self.assertIn("COMFYFLEET_INSTANCE_IMAGE", text)
        self.assertIn("COMFYFLEET_PASSWORD", text)
        self.assertIn("COMFYFLEET_PUBLIC_HOST", text)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", text)
        self.assertIn("/home:/home", text)
        self.assertIn("--gpus all", text)
        self.assertIn("--shm-size 8g", text)
        self.assertIn("shm_size: '8g'", text)
        self.assertIn("9100:9100", text)
        self.assertIn("--compose", text)
        self.assertIn("--pull-only", text)
        self.assertNotIn('echo "${COMFYFLEET_PASSWORD', text)
        self.assertNotIn("echo ${COMFYFLEET_PASSWORD", text)
        checked = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        help_run = subprocess.run(
            ["bash", str(script), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn(INSTANCE, help_run.stdout)
        self.assertIn(MANAGER, help_run.stdout)
        self.assertIn("--compose", help_run.stdout)

        missing = subprocess.run(
            ["bash", str(script)],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("COMFYFLEET_PASSWORD", missing.stderr)
        self.assertNotIn("docker pull", missing.stderr)

        bad_host = subprocess.run(
            ["bash", str(script)],
            check=False,
            capture_output=True,
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "COMFYFLEET_PASSWORD": "not-printed",
                "COMFYFLEET_PUBLIC_HOST": "0.0.0.0",
            },
        )
        self.assertNotEqual(bad_host.returncode, 0)
        self.assertIn("COMFYFLEET_PUBLIC_HOST", bad_host.stderr)
        self.assertNotIn("not-printed", bad_host.stderr)
        self.assertNotIn("docker pull", bad_host.stderr)

    def test_publish_script_pushes_both_dockerfiles(self):
        script = ROOT / "scripts" / "publish-images.sh"
        text = script.read_text(encoding="utf-8")
        self.assertTrue(script.stat().st_mode & 0o111, "publish-images.sh must be executable")
        self.assertIn("Dockerfile.manager", text)
        self.assertIn("linux/amd64", text)
        self.assertIn(":phase1", text)
        self.assertIn(":latest", text)
        self.assertIn("comfy_kitchen", text)
        self.assertIn("write:packages", text)
        checked = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        help_run = subprocess.run(
            ["bash", str(script), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn("phase1", help_run.stdout)
        self.assertIn("comfyfleet-manager", help_run.stdout)

    def test_workflow_builds_both_images_and_records_digests(self):
        text = (ROOT / ".github" / "workflows" / "publish-images.yml").read_text(encoding="utf-8")
        self.assertIn("file: Dockerfile\n", text)
        self.assertIn("file: Dockerfile.manager\n", text)
        self.assertIn("packages: write", text)
        self.assertIn("linux/amd64", text)
        self.assertIn(":phase1", text)
        self.assertIn(":latest", text)
        self.assertIn("github.sha", text)
        self.assertIn("provenance: false", text)
        self.assertIn("needs.instance.outputs.digest", text)
        self.assertIn("needs.manager.outputs.digest", text)
        self.assertIn("ubuntu-24.04", text)
        self.assertIn("No GPU", text)

    def test_compose_pulls_ghcr_and_keeps_the_manager_name(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn(MANAGER, compose)
        self.assertIn(INSTANCE, compose)
        self.assertIn("container_name: comfyfleet-manager", compose)
        self.assertIn("--shm-size 8g", compose)
        self.assertIn("shm_size: '8g'", compose)
        self.assertNotIn("dockerfile:", compose.lower())
        overlay = (ROOT / "compose.build.yaml").read_text(encoding="utf-8")
        self.assertIn("Dockerfile.manager", overlay)
        self.assertIn("comfyfleet:phase1", overlay)
        self.assertIn("comfyfleet-manager:latest", overlay)

    def test_readme_leads_with_pull_and_keeps_local_build_optional(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        dev_at = readme.lower().index("development / optional")
        head = readme[:dev_at]
        tail = readme[dev_at:]
        self.assertLess(head.index("install.sh"), head.index("docker run"))
        self.assertIn(INSTANCE, head)
        self.assertIn(MANAGER, head)
        self.assertIn("COMFYFLEET_INSTANCE_IMAGE", head)
        self.assertIn("--shm-size 8g", head)
        self.assertIn("shm_size: '8g'", head)
        self.assertIn("COMFYFLEET_INSTANCE_DIGEST", head)
        self.assertNotIn("docker build", head)
        self.assertIn("docker build -t comfyfleet:phase1 .", tail)
        self.assertIn("Dockerfile.manager", tail)
        self.assertIn("scripts/publish-images.sh", tail)
        self.assertIn("publish-images.yml", readme)
        self.assertIn("write:packages", tail)
        self.assertIn("package is private", tail.lower())
