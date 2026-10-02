"""GHCR install and publish contract. Does not pull or build images."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTANCE = "ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:67b958f4062b13620ab04bfb7b368e37ce5410db5f905ebd33e881ff8adcd36c"
MANAGER = "ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:31b99db3d3fd79dcbde50f3b4d7096dc7c6b93ac1140d1f87a971b6be6813910"


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
        self.assertIn("COMFYFLEET_INSTANCE_DIGEST", head)
        self.assertNotIn("docker build", head)
        self.assertIn("docker build -t comfyfleet:phase1 .", tail)
        self.assertIn("Dockerfile.manager", tail)
        self.assertIn("scripts/publish-images.sh", tail)
        self.assertIn("publish-images.yml", readme)
        self.assertIn("write:packages", tail)
        self.assertIn("package is private", tail.lower())
