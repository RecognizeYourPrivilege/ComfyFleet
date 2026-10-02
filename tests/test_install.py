"""GHCR install and publish contract. Does not pull or build images."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTANCE = "ghcr.io/recognizeyourprivilege/comfyfleet:cu130@sha256:2735a1bcccce1932d8180793340dd8342e86d4fff094ac7bc49805d23bb1db87"
INSTANCE_CU124 = "ghcr.io/recognizeyourprivilege/comfyfleet:cu124@sha256:d8eaa73135490896c491e98ad84d9b5fff750117ee6bd7ad2e2741c2a4ca7be5"
MANAGER = "ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:c843418f18cbe37e24bee1e16951ccce9328d2fbca4d82ed3c6e76d52e21ed73"


class InstallScriptTests(unittest.TestCase):
    def test_install_script_pulls_and_starts_the_manager(self):
        script = ROOT / "install.sh"
        text = script.read_text(encoding="utf-8")
        self.assertTrue(script.stat().st_mode & 0o111, "install.sh must be executable")
        self.assertIn("recognizeyourprivilege", text)
        self.assertIn("ghcr.io", text)
        self.assertIn("comfyfleet:cu130", text)
        self.assertIn("comfyfleet:cu124", text)
        self.assertIn("--cuda-tag", text)
        self.assertIn("CUDA 13.0", text)
        self.assertIn("CUDA 12.4", text)
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
        self.assertIn(INSTANCE_CU124, help_run.stdout)
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

        rejected = subprocess.run(
            ["bash", str(script), "--cuda-tag", "cu128", "--pull-only"],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("cu130 or cu124", rejected.stderr)
        self.assertIn("both", rejected.stderr)
        self.assertIn("CUDA 13.0", rejected.stderr)
        self.assertNotIn("docker pull", rejected.stderr)

    def test_pull_only_defaults_to_cu130_and_can_select_cu124(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            log = Path(tmp) / "docker.log"
            fake = bin_dir / "docker"
            fake.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            base = {"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin", "DOCKER_LOG": str(log)}

            silent = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--pull-only"],
                check=False,
                capture_output=True,
                text=True,
                env=base,
            )
            self.assertEqual(silent.returncode, 0, silent.stderr)
            pulled = log.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", pulled)
            self.assertIn(f"tag {INSTANCE} comfyfleet:cu130", pulled)
            self.assertIn(f"tag {INSTANCE} comfyfleet:latest", pulled)
            self.assertNotIn(":cu124", pulled)

            log.write_text("", encoding="utf-8")
            chosen = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--pull-only", "--cuda-tag", "cu124"],
                check=False,
                capture_output=True,
                text=True,
                env=base,
            )
            self.assertEqual(chosen.returncode, 0, chosen.stderr)
            pulled = log.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE_CU124}\n", pulled)
            self.assertIn(
                f"tag {INSTANCE_CU124} comfyfleet:cu124\n",
                pulled,
            )
            self.assertIn(
                f"tag {INSTANCE_CU124} comfyfleet:phase1\n",
                pulled,
            )
            self.assertIn("comfyfleet:cu124", chosen.stdout)
            self.assertIn("CUDA 12.4", (ROOT / "install.sh").read_text(encoding="utf-8"))
            self.assertIn("both   pull cu130 and cu124", (ROOT / "install.sh").read_text(encoding="utf-8"))

            log.write_text("", encoding="utf-8")
            both = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--pull-only", "--cuda-tag", "both"],
                check=False,
                capture_output=True,
                text=True,
                env=base,
            )
            self.assertEqual(both.returncode, 0, both.stderr)
            pulled = log.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", pulled)
            self.assertIn(f"tag {INSTANCE} comfyfleet:cu130", pulled)
            self.assertIn(f"tag {INSTANCE} comfyfleet:latest", pulled)
            self.assertIn(f"pull {INSTANCE_CU124}\n", pulled)
            self.assertIn(
                f"tag {INSTANCE_CU124} comfyfleet:cu124\n",
                pulled,
            )
            self.assertIn(
                f"tag {INSTANCE_CU124} comfyfleet:phase1\n",
                pulled,
            )
            self.assertIn("manager default cu130", both.stdout)
            self.assertNotIn("manager default both", both.stdout)

            log.write_text("", encoding="utf-8")
            started = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--cuda-tag", "both"],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **base,
                    "COMFYFLEET_PASSWORD": "not-printed",
                    "COMFYFLEET_PUBLIC_HOST": "192.168.1.20",
                },
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            started_log = log.read_text(encoding="utf-8")
            self.assertIn("COMFYFLEET_CUDA_TAG=cu130", started_log)
            self.assertNotIn("COMFYFLEET_CUDA_TAG=both", started_log)
            self.assertIn(f"pull {INSTANCE}", started_log)
            self.assertIn(f"pull {INSTANCE_CU124}\n", started_log)
            self.assertNotIn("not-printed", started.stdout + started.stderr)

            log.write_text("", encoding="utf-8")
            from_env = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--pull-only"],
                check=False,
                capture_output=True,
                text=True,
                env={**base, "COMFYFLEET_CUDA_TAG": "both"},
            )
            self.assertEqual(from_env.returncode, 0, from_env.stderr)
            self.assertIn("manager default cu130", from_env.stdout)
            from_env_log = log.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE_CU124}\n", from_env_log)
            self.assertIn(f"tag {INSTANCE_CU124} comfyfleet:cu124\n", from_env_log)

            log.write_text("", encoding="utf-8")
            kept = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--pull-only"],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **base,
                    "COMFYFLEET_CUDA_TAG": "both",
                    "COMFYFLEET_INSTANCE_IMAGE": "ghcr.io/recognizeyourprivilege/comfyfleet:cu124",
                },
            )
            self.assertEqual(kept.returncode, 0, kept.stderr)
            pulled = log.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", pulled)
            self.assertIn(f"tag {INSTANCE} comfyfleet:cu130", pulled)
            self.assertIn(f"tag {INSTANCE} comfyfleet:latest", pulled)
            self.assertIn("pull ghcr.io/recognizeyourprivilege/comfyfleet:cu124\n", pulled)
            self.assertIn(
                "tag ghcr.io/recognizeyourprivilege/comfyfleet:cu124 comfyfleet:phase1\n",
                pulled,
            )
            self.assertIn("manager default cu124", kept.stdout)
            self.assertNotIn("COMFYFLEET_CUDA_TAG=both", kept.stdout + kept.stderr)

    def test_tty_prompt_offers_both_and_empty_stays_cu130(self):
        import pty
        import select
        import time

        def run_tty(answer: str) -> tuple[int, str, str]:
            with tempfile.TemporaryDirectory() as tmp:
                bin_dir = Path(tmp) / "bin"
                bin_dir.mkdir()
                log = Path(tmp) / "docker.log"
                fake = bin_dir / "docker"
                fake.write_text(
                    "#!/bin/sh\n"
                    "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
                master, slave = pty.openpty()
                proc = subprocess.Popen(
                    ["bash", str(ROOT / "install.sh"), "--pull-only"],
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    env={
                        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
                        "DOCKER_LOG": str(log),
                        "TERM": "xterm",
                    },
                    close_fds=True,
                )
                os.close(slave)
                os.write(master, answer.encode("utf-8"))
                chunks: list[bytes] = []
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        while True:
                            ready, _, _ = select.select([master], [], [], 0.05)
                            if not ready:
                                break
                            try:
                                data = os.read(master, 4096)
                            except OSError:
                                data = b""
                            if not data:
                                break
                            chunks.append(data)
                        break
                    ready, _, _ = select.select([master], [], [], 0.2)
                    if not ready:
                        continue
                    try:
                        data = os.read(master, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    chunks.append(data)
                if proc.poll() is None:
                    proc.kill()
                code = proc.wait(timeout=5)
                os.close(master)
                text = b"".join(chunks).decode("utf-8", errors="replace")
                return code, text, log.read_text(encoding="utf-8")

        empty_code, empty_text, empty_log = run_tty("\n")
        self.assertEqual(empty_code, 0, empty_text)
        self.assertIn("cu130", empty_text)
        self.assertIn("cu124", empty_text)
        self.assertIn("both", empty_text)
        self.assertIn(f"pull {INSTANCE}", empty_log)
        self.assertNotIn(":cu124", empty_log)

        both_code, both_text, both_log = run_tty("both\n")
        self.assertEqual(both_code, 0, both_text)
        self.assertIn("manager default cu130", both_text)
        self.assertIn(f"pull {INSTANCE}", both_log)
        self.assertIn(f"pull {INSTANCE_CU124}\n", both_log)
        self.assertIn(f"tag {INSTANCE} comfyfleet:latest", both_log)
        self.assertIn(
            f"tag {INSTANCE_CU124} comfyfleet:phase1\n",
            both_log,
        )

    def test_publish_script_pushes_both_dockerfiles(self):
        script = ROOT / "scripts" / "publish-images.sh"
        text = script.read_text(encoding="utf-8")
        self.assertTrue(script.stat().st_mode & 0o111, "publish-images.sh must be executable")
        self.assertIn("Dockerfile.manager", text)
        self.assertIn("Dockerfile.cu124", text)
        self.assertIn("linux/amd64", text)
        self.assertIn(":cu130", text)
        self.assertIn(":cu124", text)
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
        self.assertIn("cu130", help_run.stdout)
        self.assertIn("cu124", help_run.stdout)
        self.assertIn("phase1", help_run.stdout)
        self.assertIn("comfyfleet-manager", help_run.stdout)

    def test_workflow_builds_both_images_and_records_digests(self):
        text = (ROOT / ".github" / "workflows" / "publish-images.yml").read_text(encoding="utf-8")
        self.assertIn("dockerfile: Dockerfile\n", text)
        self.assertIn("dockerfile: Dockerfile.cu124\n", text)
        self.assertIn("file: Dockerfile.manager\n", text)
        self.assertIn("packages: write", text)
        self.assertIn("linux/amd64", text)
        self.assertIn("cuda_tag: cu130", text)
        self.assertIn("cuda_tag: cu124", text)
        self.assertIn(":phase1", text)
        self.assertIn(":latest", text)
        self.assertIn(":cu130", text)
        self.assertIn(":cu124", text)
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
        self.assertIn("comfyfleet:cu130", overlay)
        self.assertIn("Dockerfile.cu124", overlay)
        self.assertIn("comfyfleet-manager:latest", overlay)

    def test_readme_leads_with_pull_and_keeps_local_build_optional(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        dev_at = readme.lower().index("development / optional")
        head = readme[:dev_at]
        tail = readme[dev_at:]
        self.assertLess(head.index("install.sh"), head.index("docker run"))
        self.assertIn(INSTANCE, head)
        self.assertIn(INSTANCE_CU124, head)
        self.assertIn(MANAGER, head)
        self.assertIn("COMFYFLEET_INSTANCE_IMAGE", head)
        self.assertIn("--shm-size 8g", head)
        self.assertIn("shm_size: '8g'", head)
        self.assertIn("COMFYFLEET_INSTANCE_DIGEST", head)
        self.assertNotIn("docker build", head)
        self.assertIn("docker build -t comfyfleet:cu130 .", tail)
        self.assertIn("Dockerfile.cu124", tail)
        self.assertIn("cu124", head)
        self.assertIn("CUDA 12.4", head)
        self.assertIn("CUDA 13.0", head)
        self.assertIn("Dockerfile.manager", tail)
        self.assertIn("scripts/publish-images.sh", tail)
        self.assertIn("publish-images.yml", readme)
        self.assertIn("write:packages", tail)
        self.assertIn("package is private", tail.lower())
