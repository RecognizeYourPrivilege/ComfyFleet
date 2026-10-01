"""GHCR install and publish contract. Does not pull or build images."""

import fcntl
import os
import pty
import select
import shutil
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTANCE = "ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba"
MANAGER = "ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:a4204564c60cf3afc40db17b34a268cf2c1c2f8e685b4601bc1c6e4dedbc713f"


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
        self.assertIn("--build", help_run.stdout)
        self.assertIn("--non-interactive", help_run.stdout)
        self.assertIn("/dev/tty", help_run.stdout)
        self.assertIn("Web UI password", help_run.stdout)

        # No controlling terminal: a missing password must fail before any pull.
        missing = subprocess.run(
            ["setsid", "bash", str(script)],
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
        install = head[head.lower().index("## install"):]
        download = (
            "curl -fsSL https://raw.githubusercontent.com/RecognizeYourPrivilege/ComfyFleet/main/install.sh "
            "-o install.sh && chmod +x install.sh && ./install.sh"
        )
        self.assertIn(download, install)
        pipe_at = install.index("| bash")
        self.assertLess(install.index(download), pipe_at)
        self.assertLess(pipe_at, install.index("--non-interactive"))
        self.assertIn("You type it twice.", install)
        self.assertIn("PyTorch stays inside the image.", install)
        self.assertNotIn("pip install", install.lower())
        self.assertIn("Arch is a fine host.", head)
        self.assertNotIn("Ubuntu", head)
        self.assertIn("docker build -t comfyfleet:phase1 .", tail)
        self.assertIn("Dockerfile.manager", tail)
        self.assertIn("scripts/publish-images.sh", tail)
        self.assertIn("publish-images.yml", readme)
        self.assertIn("write:packages", tail)
        self.assertIn("package is private", tail.lower())
        self.assertIn("install.sh --build", tail)
        self.assertIn("COMFYFLEET_INSTANCE_IMAGE=comfyfleet:phase1", tail)


def _fake_docker_env(tmp: Path, path_prefix: str | None = None) -> tuple[Path, dict[str, str]]:
    """A docker stand-in that records arguments and never pulls or builds."""

    bindir = tmp / "bin"
    bindir.mkdir()
    log = tmp / "docker.log"
    fake = bindir / "docker"
    fake.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    prefix = path_prefix if path_prefix is not None else f"{bindir}:/usr/bin:/bin"
    env = {
        "PATH": prefix,
        "HOME": str(tmp),
        "TERM": "xterm",
        "LANG": "C.UTF-8",
        "DOCKER_LOG": str(log),
    }
    return log, env


def _run_install_tty(args, env, steps, timeout=15, stdin_bytes=None):
    """Run install.sh with a controlling terminal that is not necessarily stdin.

    ``steps`` is a list of (text_to_wait_for, reply_or_None). Replies are written
    only after that text has appeared, so a password is not sitting in the
    terminal buffer before ``read -s`` starts.
    """

    master, slave = pty.openpty()

    def _take_tty():
        os.setsid()
        fcntl.ioctl(1, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if stdin_bytes is not None else slave,
        stdout=slave,
        stderr=slave,
        env=env,
        preexec_fn=_take_tty,
        close_fds=True,
    )
    os.close(slave)
    if stdin_bytes is not None:
        assert proc.stdin is not None
        proc.stdin.write(stdin_bytes)
        proc.stdin.close()

    buf = ""
    pos = 0

    def _read_ready(deadline):
        nonlocal buf
        remaining = max(0.0, deadline - time.time())
        ready, _, _ = select.select([master], [], [], min(0.2, remaining))
        if not ready:
            return
        try:
            chunk = os.read(master, 4096)
        except OSError:
            return
        if chunk:
            buf += chunk.decode("utf-8", "replace")

    try:
        for expect, reply in steps:
            deadline = time.time() + timeout
            while buf.find(expect, pos) == -1:
                if time.time() > deadline or (
                    proc.poll() is not None and not select.select([master], [], [], 0)[0]
                ):
                    proc.kill()
                    raise AssertionError(
                        f"timed out waiting for {expect!r} (exit {proc.returncode})\n{buf}"
                    )
                _read_ready(deadline)
            pos = buf.find(expect, pos) + len(expect)
            if reply is not None:
                os.write(master, reply.encode())
        deadline = time.time() + timeout
        while proc.poll() is None and time.time() < deadline:
            _read_ready(deadline)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError(f"timed out after prompts\n{buf}")
        while select.select([master], [], [], 0.1)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        os.close(master)
    return proc.returncode, buf


class InstallPromptTests(unittest.TestCase):
    def test_prompt_contract_stays_inside_docker(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("/dev/tty", text)
        self.assertIn("read -r -s", text)
        self.assertIn("Those passwords did not match.", text)
        self.assertIn('docker build -t "${LOCAL_INSTANCE_TAG}"', text)
        self.assertIn('docker build -f "${BUILD_ROOT}/Dockerfile.manager"', text)
        self.assertIn("git clone --depth 1 --branch main", text)
        self.assertNotIn("pip install", text)

    def test_noninteractive_pull_starts_manager_with_digest_pins(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            env["COMFYFLEET_PASSWORD"] = "s3cret-value"
            env["COMFYFLEET_PUBLIC_HOST"] = "192.168.1.20"
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--non-interactive"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", log)
            self.assertIn(f"pull {MANAGER}", log)
            self.assertIn(f"tag {INSTANCE} comfyfleet:phase1", log)
            self.assertIn(f"tag {MANAGER} comfyfleet-manager:latest", log)
            self.assertIn("run -d --name comfyfleet-manager", log)
            self.assertNotIn("build", log)
            self.assertIn(f"COMFYFLEET_INSTANCE_IMAGE={INSTANCE}", log)
            self.assertIn("COMFYFLEET_PUBLIC_HOST=192.168.1.20", log)
            self.assertIn("COMFYFLEET_PASSWORD=s3cret-value", log)
            self.assertIn("open http://192.168.1.20:9100/", result.stdout)
            self.assertNotIn("s3cret-value", result.stdout)
            self.assertNotIn("s3cret-value", result.stderr)

    def test_noninteractive_build_uses_local_tags_and_does_not_pull(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            env["COMFYFLEET_PASSWORD"] = "s3cret-value"
            env["COMFYFLEET_PUBLIC_HOST"] = "10.0.0.8"
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--build", "--non-interactive"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("build -t comfyfleet:phase1", log)
            self.assertIn("Dockerfile.manager", log)
            self.assertIn("-t comfyfleet-manager:latest", log)
            self.assertIn("run -d --name comfyfleet-manager", log)
            self.assertNotIn("pull", log)
            self.assertNotIn("ghcr.io", log)
            self.assertIn("COMFYFLEET_INSTANCE_IMAGE=comfyfleet:phase1", log)
            self.assertIn("comfyfleet-manager:latest", log)
            self.assertIn("open http://10.0.0.8:9100/", result.stdout)
            self.assertNotIn("s3cret-value", result.stdout)
            self.assertNotIn("s3cret-value", result.stderr)

    def test_build_without_dockerfiles_or_git_stops_before_build(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            script = tmp / "install.sh"
            shutil.copy(ROOT / "install.sh", script)
            script.chmod(0o755)
            log_path, env = _fake_docker_env(tmp, path_prefix=None)
            tr = shutil.which("tr")
            self.assertIsNotNone(tr)
            os.symlink(tr, tmp / "bin" / "tr")
            env["PATH"] = str(tmp / "bin")
            env["COMFYFLEET_PASSWORD"] = "s3cret-value"
            env["COMFYFLEET_PUBLIC_HOST"] = "192.168.1.20"
            bash = shutil.which("bash")
            self.assertIsNotNone(bash)
            result = subprocess.run(
                [bash, str(script), "--build", "--non-interactive"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                cwd=tmp,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Dockerfile", result.stderr)
            self.assertNotIn("s3cret-value", result.stderr)
            log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            self.assertNotIn("build -t", log)
            self.assertNotIn("pull", log)

    def test_conflicting_image_flags_fail(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--pull", "--build"],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be used together", result.stderr)
        both = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--build", "--pull-only"],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(both.returncode, 0)
        self.assertIn("--pull-only", both.stderr)

    def test_tty_verifies_password_and_defaults_to_pull(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            code, output = _run_install_tty(
                ["bash", str(ROOT / "install.sh")],
                env,
                [
                    ("Web UI password: ", "alpha\n"),
                    ("Type the same password again: ", "beta\n"),
                    ("did not match", None),
                    ("Web UI password: ", "alpha\n"),
                    ("Type the same password again: ", "alpha\n"),
                    ("Device IP or LAN hostname", "0.0.0.0\n"),
                    ("cannot be used", None),
                    ("Device IP or LAN hostname", "192.168.1.20\n"),
                    ("Type 1 or 2, or press Enter for 1: ", "\n"),
                ],
            )
            self.assertEqual(code, 0, output)
            self.assertIn("did not match", output)
            self.assertIn("cannot be used", output)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", log)
            self.assertIn(f"COMFYFLEET_INSTANCE_IMAGE={INSTANCE}", log)
            self.assertIn("COMFYFLEET_PASSWORD=alpha", log)
            self.assertIn("COMFYFLEET_PUBLIC_HOST=192.168.1.20", log)
            self.assertNotIn("build -t", log)

    def test_tty_build_choice_and_stdin_are_not_the_answers(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            code, output = _run_install_tty(
                ["bash", str(ROOT / "install.sh")],
                env,
                [
                    ("Web UI password: ", "\n"),
                    ("Type the same password again: ", "\n"),
                    ("empty", None),
                    ("Web UI password: ", "tty-secret\n"),
                    ("Type the same password again: ", "tty-secret\n"),
                    ("Device IP or LAN hostname", "10.2.0.5\n"),
                    ("Type 1 or 2, or press Enter for 1: ", "nope\n"),
                    ("Please type 1 or 2.", None),
                    ("Type 1 or 2, or press Enter for 1: ", "2\n"),
                ],
                stdin_bytes=b"stdin-password\nstdin-password\n0.0.0.0\n",
            )
            self.assertEqual(code, 0, output)
            self.assertIn("empty", output)
            self.assertIn("Please type 1 or 2.", output)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("build -t comfyfleet:phase1", log)
            self.assertIn("Dockerfile.manager", log)
            self.assertIn("COMFYFLEET_INSTANCE_IMAGE=comfyfleet:phase1", log)
            self.assertIn("COMFYFLEET_PASSWORD=tty-secret", log)
            self.assertIn("COMFYFLEET_PUBLIC_HOST=10.2.0.5", log)
            self.assertNotIn("stdin-password", log)
            self.assertNotIn("pull ", log)
            self.assertNotIn("COMFYFLEET_PUBLIC_HOST=0.0.0.0", log)

    def test_flags_skip_prompts_on_a_tty(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            env["COMFYFLEET_PASSWORD"] = "s3cret-value"
            env["COMFYFLEET_PUBLIC_HOST"] = "192.168.1.20"
            code, output = _run_install_tty(
                ["bash", str(ROOT / "install.sh"), "--non-interactive"],
                env,
                [],
                timeout=15,
            )
            self.assertEqual(code, 0, output)
            self.assertNotIn("Web UI password", output)
            self.assertNotIn("Type 1 or 2", output)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", log)

    def test_set_vars_still_ask_pull_or_build_on_a_tty(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            env["COMFYFLEET_PASSWORD"] = "s3cret-value"
            env["COMFYFLEET_PUBLIC_HOST"] = "192.168.1.20"
            code, output = _run_install_tty(
                ["bash", str(ROOT / "install.sh")],
                env,
                [("Type 1 or 2, or press Enter for 1: ", "1\n")],
            )
            self.assertEqual(code, 0, output)
            self.assertNotIn("Web UI password", output)
            self.assertNotIn("Device IP or LAN hostname", output)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", log)
            self.assertNotIn("s3cret-value", output)

    def test_no_tty_with_vars_pulls_without_prompts(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log_path, env = _fake_docker_env(tmp)
            env["COMFYFLEET_PASSWORD"] = "   "
            refused = subprocess.run(
                ["setsid", "bash", str(ROOT / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("COMFYFLEET_PASSWORD", refused.stderr)
            self.assertNotIn("docker pull", refused.stderr)

            env["COMFYFLEET_PASSWORD"] = "s3cret-value"
            env["COMFYFLEET_PUBLIC_HOST"] = "192.168.1.20"
            result = subprocess.run(
                ["setsid", "bash", str(ROOT / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Web UI password", result.stdout)
            self.assertNotIn("s3cret-value", result.stdout)
            self.assertNotIn("s3cret-value", result.stderr)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn(f"pull {INSTANCE}", log)
