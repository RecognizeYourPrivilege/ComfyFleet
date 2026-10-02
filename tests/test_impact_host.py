"""Impact bake, wildcards bind, ini seed, fix-owner allowlist, prune exclusion."""

import importlib.util
import inspect
import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from comfyfleet.auth import LoginGuard, SessionStore
from comfyfleet.cli import build_parser
from comfyfleet.control import create_instance
from comfyfleet.docker import DockerCLI
from comfyfleet.errors import FleetError
from comfyfleet.gpu import Gpu
from comfyfleet.http_api import ApiContext, make_server
from comfyfleet.ownership import (
    assert_allowlisted,
    chown_new_directory,
    ensure_wildcards_dir,
    fix_owner,
)
from comfyfleet.paths import WILDCARDS_CONTAINER, FleetLayout
from comfyfleet.prune import (
    ContainerRecord,
    is_managed,
    parse_ps,
    prune_dangling_containers,
    select_prune_targets,
)


ROOT = Path(__file__).resolve().parents[1]
PACK = "429d0159ad429e64d2b3916e6e7be9c22d025c3c"
SUB = "50c7b71a6a224734cc9b21963c6d1926816a97f1"
PASSWORD = "test-password"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(directory: Path, filename: str) -> Path:
    path = directory / filename
    path.write_text(
        json.dumps(
            {
                "last_node_id": 0,
                "last_link_id": 0,
                "nodes": [],
                "links": [],
                "extra": {},
                "version": 0.4,
            }
        ),
        encoding="utf-8",
    )
    return path


class _Docker:
    def __init__(self):
        self.containers = {}
        self.calls = []

    def create(self, args):
        self.calls.append(("create", list(args)))
        name = args[args.index("--name") + 1]
        self.containers[name] = {"status": "created", "args": list(args)}

    def start(self, name):
        self.containers[name]["status"] = "running"

    def stop(self, name):
        self.containers[name]["status"] = "exited"

    def remove(self, name):
        self.containers.pop(name, None)

    def update_restart(self, name, policy):
        return None

    def status(self, name):
        item = self.containers.get(name)
        return None if item is None else item["status"]

    def running_names(self):
        return [name for name, item in self.containers.items() if item["status"] == "running"]


class ImpactBakeContractTests(unittest.TestCase):
    def test_both_dockerfiles_pin_the_same_impact_shas_and_keep_opencv(self):
        cu130 = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cu124 = (ROOT / "Dockerfile.cu124").read_text(encoding="utf-8")
        pins = (ROOT / "docker" / "PINS.txt").read_text(encoding="utf-8")
        pins124 = (ROOT / "docker" / "PINS.cu124.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        entry = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        for text in (cu130, cu124, pins, pins124, readme):
            self.assertIn(PACK, text)
            self.assertIn(SUB, text)
        for dockerfile in (cu130, cu124):
            self.assertIn("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", dockerfile)
            self.assertIn("https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git", dockerfile)
            self.assertIn("opencv-python-headless<0", dockerfile)
            self.assertNotIn("pip install opencv-python-headless", dockerfile)
            self.assertNotIn("submodule update", dockerfile)
            self.assertIn("SAM2_BUILD_CUDA=0", dockerfile)
            self.assertIn("impact_bake.py", dockerfile)
            self.assertIn("--check-weights", dockerfile)
            self.assertIn("seed_impact_config.py", dockerfile)
            touch = dockerfile.index("touch /opt/comfyfleet/baked_custom_nodes/skip_download_model")
            install = dockerfile.index("ComfyUI-Impact-Pack/install.py")
            sub_install = dockerfile.index("ComfyUI-Impact-Subpack/install.py")
            remove = dockerfile.index("rm -f /opt/comfyfleet/baked_custom_nodes/skip_download_model")
            self.assertLess(touch, install)
            self.assertLess(install, sub_install)
            self.assertLess(sub_install, remove)
            self.assertLess(remove, dockerfile.index("--check-weights"))
        for name in ("ComfyUI-Impact-Pack", "ComfyUI-Impact-Subpack"):
            self.assertIn(f'link_baked "{name}"', entry)
            self.assertLess(entry.index(f'link_baked "{name}"'), entry.index("exec /opt/venv/bin/python main.py"))
        self.assertIn("/opt/comfyfleet/seed_impact_config.py", entry)
        self.assertLess(
            entry.index("/opt/comfyfleet/seed_impact_config.py"),
            entry.index("exec /opt/venv/bin/python main.py"),
        )
        self.assertIn("/home/models/sams", readme)
        self.assertIn("custom_wildcards = /home/wildcards", readme)
        self.assertIn("comfyfleet fix-owner", readme)
        script = (ROOT / "docker" / "verify_image_pins.py").read_text(encoding="utf-8")
        self.assertIn("opencv_python_headless", script)
        self.assertIn("import cv2", script)
        self.assertIn("RES4LYF/images.py", script)
        self.assertIn("ComfyUI-Impact-Pack/modules/impact/utils.py", script)

    def test_requirement_filter_drops_headless_and_keeps_the_git_line(self):
        bake = _load(ROOT / "docker" / "impact_bake.py", "impact_bake")
        body = bake.merged_requirements(
            [
                "segment-anything\nopencv-python-headless\ngit+https://github.com/facebookresearch/sam2\n",
                "ultralytics>=8.3.162\nopencv-python-headless\nmatplotlib\n",
            ]
        )
        self.assertNotIn("opencv-python-headless", body)
        self.assertNotIn("opencv-python\n", body)
        self.assertIn("git+https://github.com/facebookresearch/sam2", body)
        self.assertIn("ultralytics>=8.3.162", body)
        self.assertIn("segment-anything", body)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sam_vit_b_01ec64.pth").write_bytes(b"weight")
            with self.assertRaises(SystemExit) as ctx:
                bake.assert_no_weight_files([root])
            self.assertIn("sam_vit_b", str(ctx.exception))
            (root / "sam_vit_b_01ec64.pth").unlink()
            bake.assert_no_weight_files([root])

    def test_opencv_distribution_check_rejects_headless(self):
        verify = _load(ROOT / "docker" / "verify_image_pins.py", "verify_image_pins_impact")
        self.assertEqual(verify.opencv_distribution_errors({"opencv_python": "5.0.0.93"}), [])
        errors = verify.opencv_distribution_errors(
            {"opencv_python": "5.0.0.93", "opencv_python_headless": "5.0.0.93"}
        )
        self.assertTrue(any("headless" in item for item in errors))
        missing = verify.opencv_distribution_errors({})
        self.assertTrue(any("opencv-python is not installed" in item for item in missing))


class WildcardsBindTests(unittest.TestCase):
    def test_create_binds_wildcards_and_chowns_only_when_it_creates_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            layout = FleetLayout(root)
            sources = Path(tmp) / "src"
            sources.mkdir()
            docker = _Docker()
            created = []

            def spy(path):
                created.append(Path(path))

            with mock.patch("comfyfleet.ownership.chown_new_directory", spy):
                create_instance(
                    _workflow(sources, "Portrait.json"),
                    layout=layout,
                    docker=docker,
                    gpus=[Gpu(0, "GPU0", "8192 MiB")],
                    gpu="0",
                    port_in_use=lambda _port: False,
                )
            args = docker.containers["portrait"]["args"]
            self.assertIn(f"{root}/wildcards:{WILDCARDS_CONTAINER}", args)
            self.assertEqual(WILDCARDS_CONTAINER, "/home/wildcards")
            self.assertTrue((root / "wildcards").is_dir())
            self.assertTrue((root / "models" / "sams").is_dir())
            self.assertEqual(created, [root / "wildcards"])

            marker = root / "wildcards" / "kept.txt"
            marker.write_text("leave-me", encoding="utf-8")
            again = []
            with mock.patch("comfyfleet.ownership.chown_new_directory", lambda path: again.append(path)):
                create_instance(
                    _workflow(sources, "Other.json"),
                    layout=layout,
                    docker=docker,
                    gpus=[Gpu(0, "GPU0", "8192 MiB")],
                    gpu="0",
                    port_in_use=lambda _port: False,
                )
            self.assertEqual(again, [])
            self.assertEqual(marker.read_text(encoding="utf-8"), "leave-me")
            other = docker.containers["other"]["args"]
            self.assertIn(f"{root}/wildcards:{WILDCARDS_CONTAINER}", other)

    def test_manager_process_refuses_a_missing_comfyui_user_on_the_one_shot_chown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wildcards"
            path.mkdir()
            with mock.patch.dict(os.environ, {"COMFYFLEET_MANAGER": "1"}):
                with mock.patch(
                    "comfyfleet.ownership.resolve_comfyui_ids",
                    side_effect=FleetError("cannot resolve host user comfyui by name"),
                ):
                    with self.assertRaises(FleetError) as ctx:
                        chown_new_directory(path)
            self.assertIn("comfyui", str(ctx.exception))
            with mock.patch.dict(os.environ, {"COMFYFLEET_MANAGER": ""}, clear=False):
                os.environ.pop("COMFYFLEET_MANAGER", None)
                with mock.patch(
                    "comfyfleet.ownership.resolve_comfyui_ids",
                    side_effect=FleetError("cannot resolve host user comfyui by name"),
                ):
                    chown_new_directory(path)

    def test_existing_directory_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = FleetLayout(Path(tmp) / "home")
            layout.wildcards.mkdir(parents=True)
            (layout.wildcards / "a.txt").write_text("x", encoding="utf-8")
            self.assertFalse(ensure_wildcards_dir(layout))
            self.assertEqual((layout.wildcards / "a.txt").read_text(encoding="utf-8"), "x")


class ImpactIniTests(unittest.TestCase):
    def test_seed_writes_custom_wildcards_without_quotes(self):
        seed = _load(ROOT / "docker" / "seed_impact_config.py", "seed_impact_config")
        created = seed.ensure_impact_config("")
        self.assertIn("custom_wildcards = /home/wildcards\n", created)
        self.assertNotIn('custom_wildcards = "', created)
        self.assertNotIn("custom_wildcards = '", created)
        quoted = seed.ensure_impact_config(
            "[default]\nsam_editor_cpu = False\ncustom_wildcards = \"/home/wildcards\"\n"
        )
        self.assertIn("sam_editor_cpu = False", quoted)
        self.assertIn("custom_wildcards = /home/wildcards\n", quoted)
        self.assertNotIn('"/home/wildcards"', quoted)
        single = seed.ensure_impact_config("[default]\ncustom_wildcards = '/home/wildcards'\n")
        self.assertIn("custom_wildcards = /home/wildcards\n", single)
        self.assertNotIn("'", single.split("custom_wildcards", 1)[1].splitlines()[0])
        again = seed.ensure_impact_config(created)
        self.assertEqual(again, created)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pack" / "impact-pack.ini"
            seed.seed(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("custom_wildcards = /home/wildcards\n", text)
            seed.seed(path)
            self.assertEqual(path.read_text(encoding="utf-8"), text)


class FixOwnerTests(unittest.TestCase):
    def test_cli_has_no_path_argument(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["fix-owner", "/etc"])
        args = parser.parse_args(["fix-owner"])
        self.assertEqual(args.command, "fix-owner")
        parameters = inspect.signature(fix_owner).parameters
        self.assertNotIn("path", parameters)

    def test_allowlist_refuses_escapes_and_chowns_only_listed_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            layout = FleetLayout(root)
            layout.wildcards.mkdir(parents=True)
            (layout.wildcards / "nested.txt").write_text("w", encoding="utf-8")
            layout.models.mkdir()
            (layout.root / "custom_nodes_portrait").mkdir()
            (layout.root / "custom_nodes_portrait" / "node.py").write_text("n", encoding="utf-8")
            layout.files.mkdir()
            (layout.root / "secret").mkdir()
            (layout.root / "custom_nodes").mkdir()
            outside = Path(tmp) / "etc"
            outside.mkdir()
            escape = layout.wildcards / "escape"
            escape.symlink_to(outside)

            with self.assertRaises(FleetError):
                assert_allowlisted(Path("/etc"), layout)
            with self.assertRaises(FleetError):
                assert_allowlisted(layout.root / ".." / "etc", layout)
            with self.assertRaises(FleetError):
                assert_allowlisted(layout.wildcards / ".." / ".." / "etc", layout)
            with self.assertRaises(FleetError):
                assert_allowlisted(layout.root / "secret", layout)
            with self.assertRaises(FleetError):
                assert_allowlisted(layout.root / "custom_nodes", layout)
            with self.assertRaises(FleetError):
                assert_allowlisted(escape, layout)
            assert_allowlisted(layout.wildcards / "nested.txt", layout)
            assert_allowlisted(layout.root / "custom_nodes_portrait", layout)

            chowned = []

            def spy(path, uid, gid):
                chowned.append(Path(path))
                self.assertEqual((uid, gid), (7, 8))

            with self.assertRaises(FleetError):
                fix_owner(layout, resolve=lambda: (7, 8), chown=spy)
            self.assertNotIn(outside, chowned)
            self.assertTrue(all(item != outside and outside not in item.parents for item in chowned))

            escape.unlink()
            chowned.clear()
            result = fix_owner(layout, resolve=lambda: (7, 8), chown=spy)
            names = {path.name for path in chowned}
            self.assertIn("wildcards", names)
            self.assertIn("nested.txt", names)
            self.assertIn("models", names)
            self.assertIn("custom_nodes_portrait", names)
            self.assertIn("node.py", names)
            self.assertIn("files", names)
            self.assertNotIn("secret", names)
            self.assertNotIn("custom_nodes", names)
            self.assertEqual(result.user, "comfyui")
            self.assertEqual(result.uid, 7)
            self.assertTrue(any(path.endswith("/wildcards") or path.endswith("\\wildcards") for path in result.paths))


class PruneTests(unittest.TestCase):
    def test_stopped_fleet_instances_are_excluded(self):
        managed = "comfyfleet.managed=true,comfyfleet.name=portrait"
        text = "\n".join(
            [
                "aaaaaaaaaaaa\tjunk\tExited (0) 3 hours ago\t",
                f"bbbbbbbbbbbb\tportrait\tExited (1) 2 days ago\t{managed}",
                "cccccccccccc\trunning\tUp 4 seconds\t",
                "dddddddddddd\tfleet-up\tUp 4 seconds\tcomfyfleet.managed=true",
                "eeeeeeeeeeee\tcreated-junk\tCreated\t",
                "ffffffffffff\tpaused\tPaused\t",
            ]
        )
        records = parse_ps(text)
        self.assertTrue(is_managed(records[1]))
        self.assertTrue(is_managed(records[3]))
        remove, kept = select_prune_targets(records)
        self.assertEqual([item.id for item in remove], ["aaaaaaaaaaaa", "eeeeeeeeeeee"])
        self.assertEqual([item.id for item in kept], ["bbbbbbbbbbbb", "dddddddddddd"])

        class Engine:
            def __init__(self):
                self.records = records
                self.removed = []

            def list_container_records(self):
                return list(self.records)

            def remove_stopped(self, container_id):
                self.removed.append(container_id)

        engine = Engine()
        result = prune_dangling_containers(engine)
        self.assertEqual(result.removed, ("aaaaaaaaaaaa", "eeeeeeeeeeee"))
        self.assertEqual(engine.removed, ["aaaaaaaaaaaa", "eeeeeeeeeeee"])
        self.assertIn("bbbbbbbbbbbb", result.kept_managed)
        self.assertNotIn("bbbbbbbbbbbb", result.removed)

    def test_docker_rm_is_not_force_and_rejects_a_managed_id_shape(self):
        calls = []

        def run(argv, timeout=None):
            calls.append(list(argv))
            if argv[1:3] == ["ps", "-a"]:
                stdout = "aaaaaaaaaaaa\tjunk\tExited (0) 1s\t\nbbbbbbbbbbbb\tfleet\tExited (0) 1s\tcomfyfleet.managed=true\n"
            else:
                stdout = ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        result = prune_dangling_containers(DockerCLI(run=run))
        self.assertEqual(result.removed, ("aaaaaaaaaaaa",))
        rm = [argv for argv in calls if argv[1] == "rm"]
        self.assertEqual(rm, [["docker", "rm", "aaaaaaaaaaaa"]])
        self.assertNotIn("-f", rm[0])
        client = DockerCLI(run=run)
        with self.assertRaises(FleetError):
            client.remove_stopped("comfyfleet.managed=true")


class HostApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "home"
        self.layout = FleetLayout(root)
        self.layout.wildcards.mkdir(parents=True)
        self.docker = _PruneDocker()
        self.ctx = ApiContext(
            layout=self.layout,
            docker=self.docker,
            detect_gpus=lambda: [],
            port_in_use=lambda _port: False,
            ui_dir=root,
            password=PASSWORD,
            sessions=SessionStore(),
            login_guard=LoginGuard(fail_delay_s=0),
        )
        self.httpd = make_server("127.0.0.1", 0, self.ctx)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _open(self, method, path, auth=True):
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {PASSWORD}"
        request = urllib.request.Request(self.base + path, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_fix_owner_requires_auth_and_uses_the_helper(self):
        chowned = []
        status, raw = self._open("POST", "/api/host/fix-owner", auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(chowned, [])
        with mock.patch("comfyfleet.ownership.resolve_comfyui_ids", return_value=(4, 5)), mock.patch(
            "comfyfleet.ownership.chown_inode",
            lambda path, uid, gid: chowned.append((str(path), uid, gid)),
        ):
            status, raw = self._open("POST", "/api/host/fix-owner", auth=True)
        self.assertEqual(status, 200, raw)
        payload = json.loads(raw.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["uid"], 4)
        self.assertEqual(payload["user"], "comfyui")
        self.assertTrue(any(path.endswith("wildcards") for path in payload["paths"]))
        self.assertTrue(chowned)
        status, raw = self._open("GET", "/api/host/fix-owner", auth=True)
        self.assertEqual(status, 405)

    def test_prune_route_keeps_managed_containers(self):
        status, _raw = self._open("POST", "/api/host/prune-dangling", auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(self.docker.removed, [])
        status, raw = self._open("POST", "/api/host/prune-dangling", auth=True)
        self.assertEqual(status, 200, raw)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload["removed"], ["aaaaaaaaaaaa"])
        self.assertIn("bbbbbbbbbbbb", payload["kept_managed"])
        self.assertNotIn("bbbbbbbbbbbb", self.docker.removed)


class _PruneDocker:
    def __init__(self):
        self.removed = []

    def list_container_records(self):
        return parse_ps(
            "aaaaaaaaaaaa\tjunk\tExited (0) 1s\t\n"
            "bbbbbbbbbbbb\tportrait\tExited (0) 1s\tcomfyfleet.managed=true\n"
        )

    def remove_stopped(self, container_id):
        self.removed.append(container_id)

    def status(self, _name):
        return None

    def running_names(self):
        return []


if __name__ == "__main__":
    unittest.main()
