"""ComfyFleet seeds Manager config.ini and skips the loopback install gate."""

import ast
import configparser
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "docker" / "seed_manager_config.py"
PATCH_PATH = ROOT / "docker" / "patch_manager_trusted_install.py"

# glob/manager_server.py @ 14b5aaab711ad1f1306d420732a923fb058c44d7
# The helper and the network_mode log are the two anchors the image patch
# rewrites. The em dash in the docstring is the upstream character.
PINNED_MANAGER_SERVER = textwrap.dedent(
    '''\
    import os
    import logging
    import manager_core as core

    logging.info("[ComfyUI-Manager] network_mode: " + core.get_config()['network_mode'])


    def is_loopback(address):
        import ipaddress
        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            return False


    def is_dedicated_install_allowed(flag_value: bool, listen_address: str) -> bool:
        """P-direct predicate (adopter-degraded form): flag AND loopback.

        Pure helper for the dedicated install flags
        (allow_git_url_install / allow_pip_install) — callers pass the
        flag value from their own config read and the listener address
        from the CLI arguments (request-time evaluation; the import-time
        snapshot above is NOT consulted).
        """
        return bool(flag_value) and is_loopback(listen_address)


    is_local_mode = is_loopback(args.listen)
    '''
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEED = _load(SEED_PATH, "seed_manager_config")
PATCH = _load(PATCH_PATH, "patch_manager_trusted_install")


def _predicate(source: str):
    tree = ast.parse(source)
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "is_dedicated_install_allowed"
    )
    segment = ast.get_source_segment(source, func)
    namespace = {"os": os}

    def is_loopback(address):
        import ipaddress

        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            return False

    namespace["is_loopback"] = is_loopback
    exec(segment, namespace)
    return namespace["is_dedicated_install_allowed"]


class SeedManagerConfigTests(unittest.TestCase):
    def test_missing_file_gets_the_three_keys(self):
        updated = SEED.ensure_manager_config("")
        parsed = configparser.ConfigParser(interpolation=None, strict=False)
        parsed.read_string(updated)
        self.assertEqual(parsed["default"]["allow_git_url_install"], "true")
        self.assertEqual(parsed["default"]["allow_pip_install"], "true")
        self.assertEqual(parsed["default"]["security_level"], "normal")

    def test_existing_file_keeps_unrelated_keys_and_sections(self):
        original = (
            "# operator note\n"
            "[default]\n"
            "channel_url = https://example.test/channel\n"
            "allow_git_url_install = false\n"
            "share_option = all\n"
            "\n"
            "[channels]\n"
            "extra = 1\n"
        )
        updated = SEED.ensure_manager_config(original)
        self.assertIn("# operator note\n", updated)
        self.assertIn("channel_url = https://example.test/channel\n", updated)
        self.assertIn("share_option = all\n", updated)
        self.assertIn("[channels]\nextra = 1\n", updated)
        self.assertIn("allow_git_url_install = true\n", updated)
        self.assertNotIn("allow_git_url_install = false", updated)
        parsed = configparser.ConfigParser(interpolation=None, strict=False)
        parsed.read_string(updated)
        self.assertEqual(parsed["default"]["allow_pip_install"], "true")
        self.assertEqual(parsed["default"]["security_level"], "normal")
        self.assertEqual(parsed["default"]["channel_url"], "https://example.test/channel")
        self.assertEqual(parsed["channels"]["extra"], "1")

    def test_true_and_normal_any_case_are_left_in_place(self):
        original = (
            "[default]\n"
            "allow_git_url_install = True\n"
            "allow_pip_install = TRUE\n"
            "security_level = Normal\n"
            "db_mode = cache\n"
        )
        updated = SEED.ensure_manager_config(original)
        self.assertEqual(updated, original if original.endswith("\n") else original + "\n")
        self.assertIn("allow_git_url_install = True\n", updated)
        self.assertIn("security_level = Normal\n", updated)
        self.assertIn("db_mode = cache\n", updated)

    def test_second_pass_is_identical(self):
        original = "[other]\nkeep = yes\n"
        once = SEED.ensure_manager_config(original)
        twice = SEED.ensure_manager_config(once)
        self.assertEqual(twice, once)
        self.assertIn("[other]\nkeep = yes\n", twice)

    def test_weak_security_level_is_set_to_normal(self):
        updated = SEED.ensure_manager_config("[default]\nsecurity_level = weak\n")
        parsed = configparser.ConfigParser(interpolation=None, strict=False)
        parsed.read_string(updated)
        self.assertEqual(parsed["default"]["security_level"], "normal")

    def test_commented_key_does_not_count_as_set(self):
        updated = SEED.ensure_manager_config("# allow_git_url_install = false\n")
        self.assertIn("# allow_git_url_install = false\n", updated)
        parsed = configparser.ConfigParser(interpolation=None, strict=False)
        parsed.read_string(updated)
        self.assertEqual(parsed["default"]["allow_git_url_install"], "true")

    def test_script_writes_the_log_line_and_is_idempotent_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user" / "__manager" / "config.ini"
            path.parent.mkdir(parents=True)
            path.write_text("[default]\nfile_logging = true\n", encoding="utf-8")
            first = subprocess.run(
                [sys.executable, str(SEED_PATH), str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            disk = path.read_text(encoding="utf-8")
            second = subprocess.run(
                [sys.executable, str(SEED_PATH), str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), disk)
            for completed in (first, second):
                self.assertIn(
                    f"comfyfleet: Manager config {path} allow_git_url_install=true "
                    "allow_pip_install=true security_level=normal",
                    completed.stdout,
                )
            self.assertIn("file_logging = true\n", disk)


class TrustedInstallPatchTests(unittest.TestCase):
    def test_pinned_helper_allows_wildcard_listen_only_with_flag_and_env(self):
        self.assertNotIn(PATCH.FLAG_LOG_MARKER, PINNED_MANAGER_SERVER)
        patched = PATCH.patch_source(PINNED_MANAGER_SERVER)
        self.assertIn("is_local_mode = is_loopback(args.listen)", patched)
        self.assertEqual(patched.count(PATCH.FLAG_LOG_MARKER), 1)
        self.assertIn(
            'logging.info("[ComfyUI-Manager] network_mode: " + core.get_config()[\'network_mode\'])\n'
            + PATCH.FLAG_LOG,
            patched,
        )
        allow = _predicate(patched)
        previous = os.environ.get("COMFYFLEET_TRUSTED_INSTALL")
        try:
            os.environ["COMFYFLEET_TRUSTED_INSTALL"] = "1"
            self.assertTrue(allow(True, "0.0.0.0"))
            self.assertFalse(allow(False, "0.0.0.0"))
            self.assertTrue(allow(True, "127.0.0.1"))
            os.environ["COMFYFLEET_TRUSTED_INSTALL"] = "0"
            self.assertFalse(allow(True, "0.0.0.0"))
            self.assertTrue(allow(True, "127.0.0.1"))
            os.environ.pop("COMFYFLEET_TRUSTED_INSTALL", None)
            self.assertFalse(allow(True, "0.0.0.0"))
            self.assertTrue(allow(True, "::1"))
        finally:
            if previous is None:
                os.environ.pop("COMFYFLEET_TRUSTED_INSTALL", None)
            else:
                os.environ["COMFYFLEET_TRUSTED_INSTALL"] = previous

    def test_second_apply_is_identical(self):
        once = PATCH.patch_source(PINNED_MANAGER_SERVER)
        twice = PATCH.patch_source(once)
        self.assertEqual(twice, once)
        ast.parse(twice)

    def test_unrecognized_helper_is_refused(self):
        source = PINNED_MANAGER_SERVER.replace(
            PATCH.STOCK_RETURN,
            "return False",
        )
        with self.assertRaises(PATCH.ManagerPatchError):
            PATCH.patch_source(source)

    def test_script_patches_a_file_and_reports_already_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manager_server.py"
            path.write_text(PINNED_MANAGER_SERVER, encoding="utf-8")
            first = subprocess.run(
                [sys.executable, str(PATCH_PATH), str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            rewritten = path.read_text(encoding="utf-8")
            second = subprocess.run(
                [sys.executable, str(PATCH_PATH), str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("patched Manager dedicated-install gate", first.stdout)
            self.assertIn("already applied", second.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), rewritten)

    def test_image_contract_keeps_listen_and_documents_the_gate(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        self.assertIn("patch_manager_trusted_install.py", dockerfile)
        self.assertIn("seed_manager_config.py", dockerfile)
        self.assertIn("python /opt/comfyfleet/patch_manager_trusted_install.py", dockerfile)
        self.assertIn("COMFYFLEET_TRUSTED_INSTALL", dockerfile)
        self.assertIn("0.0.0.0", dockerfile)
        self.assertLess(
            dockerfile.index("torch==2.13.0+cu130"),
            dockerfile.index("patch_manager_trusted_install.py"),
        )
        self.assertIn('comfy_args=(--listen 0.0.0.0 --port 8188)', entrypoint)
        exec_lines = [line for line in entrypoint.splitlines() if line.startswith("exec ")]
        self.assertEqual(
            exec_lines,
            ['exec /opt/venv/bin/python main.py "${comfy_args[@]}"'],
        )
        self.assertLess(
            entrypoint.index('comfy_args=(--listen 0.0.0.0 --port 8188)'),
            entrypoint.index("exec "),
        )
        self.assertIn("export COMFYFLEET_TRUSTED_INSTALL=1", entrypoint)
        self.assertIn(
            'echo "comfyfleet: COMFYFLEET_TRUSTED_INSTALL=${COMFYFLEET_TRUSTED_INSTALL}"',
            entrypoint,
        )
        self.assertLess(
            entrypoint.index("export COMFYFLEET_TRUSTED_INSTALL=1"),
            entrypoint.index("exec "),
        )
        self.assertIn("/opt/comfyfleet/seed_manager_config.py", entrypoint)
        self.assertNotIn("--listen 127.0.0.1", entrypoint)
        self.assertIn("/opt/ComfyUI/user/__manager/config.ini", readme)
        self.assertIn("allow_git_url_install = true", readme)
        self.assertIn("COMFYFLEET_TRUSTED_INSTALL=1", readme)
        self.assertIn("docker.sock LAN", readme)
        self.assertIn("COMFYFLEET_TRUSTED_INSTALL", pins)
        self.assertIn("/opt/ComfyUI/user/__manager/config.ini", pins)


if __name__ == "__main__":
    unittest.main()
