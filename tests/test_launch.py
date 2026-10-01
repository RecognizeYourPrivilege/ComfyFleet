"""Per-instance ComfyUI launch flags: catalog, argv order, and docker create."""

import json
import tempfile
import unittest
from pathlib import Path

from comfyfleet.control import Instance, create_instance, start_instance
from comfyfleet.errors import FleetError
from comfyfleet.gpu import Gpu
from comfyfleet.launch import (
    ATTENTION_FLAGS,
    BOOL_FLAGS,
    VRAM_FLAGS,
    LaunchConfig,
    parse_launch,
    strip_locked_args,
)
from comfyfleet.paths import DEFAULT_IMAGE, FleetLayout


ROOT = Path(__file__).resolve().parents[1]


class FakeDocker:
    def __init__(self):
        self.containers = {}
        self.calls = []

    def create(self, args):
        self.calls.append(("create", list(args)))
        name = args[args.index("--name") + 1]
        self.containers[name] = {"status": "created", "args": list(args)}

    def start(self, name):
        self.calls.append(("start", name))
        self.containers[name]["status"] = "running"

    def stop(self, name):
        self.calls.append(("stop", name))
        self.containers[name]["status"] = "exited"

    def remove(self, name):
        self.calls.append(("rm", name))
        self.containers.pop(name, None)

    def update_restart(self, name, policy):
        self.calls.append(("update-restart", name, policy))

    def status(self, name):
        item = self.containers.get(name)
        if item is None:
            return None
        return item["status"]

    def running_names(self):
        return [name for name, item in self.containers.items() if item["status"] == "running"]


def _workflow(directory: Path) -> Path:
    path = directory / "Portrait.json"
    path.write_text(
        json.dumps(
            {
                "last_node_id": 0,
                "last_link_id": 0,
                "nodes": [],
                "links": [],
                "version": 0.4,
            }
        ),
        encoding="utf-8",
    )
    return path


class LaunchParseTests(unittest.TestCase):
    def test_default_adds_nothing(self):
        launch = parse_launch()
        self.assertEqual(launch.argv(), [])

    def test_order_is_vram_attention_flags_values_then_extra(self):
        launch = parse_launch(
            vram="lowvram",
            attention="use-pytorch-cross-attention",
            flags=["disable-smart-memory", "--force-fp16"],
            reserve_vram="1.5",
            preview_method="auto",
            extra_args="--listen 127.0.0.1 --port 9 --cache-none",
        )
        self.assertEqual(
            launch.argv(),
            [
                "--lowvram",
                "--use-pytorch-cross-attention",
                "--force-fp16",
                "--disable-smart-memory",
                "--reserve-vram",
                "1.5",
                "--preview-method",
                "auto",
                "--cache-none",
            ],
        )
        self.assertNotIn("--listen", launch.argv())
        self.assertNotIn("--port", launch.argv())
        self.assertNotIn("127.0.0.1", launch.extra_args)

    def test_strip_listen_and_port_without_eating_the_next_flag(self):
        self.assertEqual(
            strip_locked_args(["--listen", "--cache-none", "--port=8188", "--mmap-torch-files"]),
            ["--cache-none", "--mmap-torch-files"],
        )
        self.assertEqual(
            strip_locked_args(["--port", "9000", "--listen=10.0.0.2", "plain"]),
            ["plain"],
        )
        launch = parse_launch(extra_args="--listen --disable-metadata")
        self.assertEqual(launch.argv(), ["--disable-metadata"])

    def test_conflicts_and_unknown_flags_are_refused(self):
        with self.assertRaises(FleetError):
            parse_launch(vram="--lowvram", flags=["--cpu"])
        with self.assertRaises(FleetError):
            parse_launch(flags=["--force-fp16", "--force-fp32"])
        with self.assertRaises(FleetError):
            parse_launch(flags=["--cuda-malloc", "disable-cuda-malloc"])
        with self.assertRaises(FleetError):
            parse_launch(vram="--gpu-only")
        with self.assertRaises(FleetError):
            parse_launch(flags=["--enable-cors-header"])
        with self.assertRaises(FleetError):
            parse_launch(extra_args="--lowvram")
        with self.assertRaises(FleetError):
            parse_launch(extra_args="--preview-method auto")

    def test_unlisted_real_flag_stays_in_extra(self):
        launch = parse_launch(extra_args="--cache-none --mmap-torch-files")
        self.assertEqual(launch.argv(), ["--cache-none", "--mmap-torch-files"])

    def test_metadata_without_launch_stays_stock(self):
        instance = Instance.from_json(
            {
                "name": "portrait",
                "port": 8188,
                "gpus": [0],
                "image": DEFAULT_IMAGE,
                "workflow_host_path": "/home/files/portrait/default_workflow.json",
            },
            Path("comfyfleet.json"),
        )
        self.assertEqual(instance.launch, LaunchConfig())
        self.assertEqual(instance.launch.argv(), [])

    def test_ui_lists_every_catalog_flag(self):
        html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        for flag in VRAM_FLAGS:
            self.assertIn(f'name="vram" value="{flag}"', html)
        for flag in ATTENTION_FLAGS:
            self.assertIn(f'name="attention" value="{flag}"', html)
        for item in BOOL_FLAGS:
            if item.exclusive:
                needle = f'value="{item.flag}" data-exclusive="{item.exclusive}"'
            else:
                needle = f'value="{item.flag}"'
            self.assertIn(needle, html)


class LaunchCreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.layout = FleetLayout(self.root)
        self.docker = FakeDocker()
        self.sources = Path(self.tmp.name) / "src"
        self.sources.mkdir()
        self.gpus = [Gpu(0, "RTX A2000", "12288 MiB")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_appends_flags_after_the_image_and_persists_them(self):
        launch = parse_launch(
            vram="--lowvram",
            flags=["--disable-dynamic-vram", "--disable-smart-memory"],
            reserve_vram="1",
            extra_args="--listen 127.0.0.1 --cache-classic",
        )
        result = create_instance(
            _workflow(self.sources),
            layout=self.layout,
            docker=self.docker,
            gpus=self.gpus,
            gpu="0",
            port_in_use=lambda _port: False,
            launch=launch,
        )
        self.assertEqual(
            result.instance.launch.argv(),
            [
                "--lowvram",
                "--disable-smart-memory",
                "--disable-dynamic-vram",
                "--reserve-vram",
                "1",
                "--cache-classic",
            ],
        )
        args = self.docker.containers["portrait"]["args"]
        image_at = args.index(DEFAULT_IMAGE)
        self.assertEqual(args[image_at + 1 :], result.instance.launch.argv())
        self.assertNotIn("--listen", args)
        self.assertNotIn("127.0.0.1", args)
        stored = json.loads((self.root / "files" / "portrait" / "comfyfleet.json").read_text())
        self.assertEqual(stored["launch"]["vram"], "--lowvram")
        self.assertNotIn("argv", stored["launch"])

    def test_stock_create_does_not_append_args(self):
        create_instance(
            _workflow(self.sources),
            layout=self.layout,
            docker=self.docker,
            gpus=self.gpus,
            gpu="0",
            port_in_use=lambda _port: False,
        )
        args = self.docker.containers["portrait"]["args"]
        self.assertEqual(args[-1], DEFAULT_IMAGE)

    def test_start_recreate_keeps_saved_flags(self):
        create_instance(
            _workflow(self.sources),
            layout=self.layout,
            docker=self.docker,
            gpus=self.gpus,
            gpu="0",
            port_in_use=lambda _port: False,
            launch=parse_launch(vram="novram", attention="use-sage-attention"),
        )
        moved = start_instance(
            "portrait",
            layout=self.layout,
            docker=self.docker,
            gpus=self.gpus,
            port_in_use=lambda port: port == 8188,
        )
        self.assertEqual(moved.instance.port, 8189)
        args = self.docker.containers["portrait"]["args"]
        self.assertEqual(
            args[args.index(DEFAULT_IMAGE) + 1 :],
            ["--novram", "--use-sage-attention"],
        )
        self.assertEqual(args[args.index("-p") + 1], "8189:8188")


if __name__ == "__main__":
    unittest.main()
