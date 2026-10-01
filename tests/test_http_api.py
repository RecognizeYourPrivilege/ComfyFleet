"""HTTP adapter: control happy path, missing workflow, no second lifecycle."""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from comfyfleet.auth import LoginGuard, SessionStore
from comfyfleet.cli import build_parser
from comfyfleet.control import authorize
from comfyfleet.errors import FleetError
from comfyfleet.gpu import Gpu
from comfyfleet.http_api import (
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
    MAX_BODY_BYTES,
    ApiContext,
    dispatch,
    make_server,
    request_host,
)
from comfyfleet.paths import FleetLayout


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


class Probe:
    def __init__(self, gpus):
        self.gpus = list(gpus)
        self.error = None
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise FleetError(self.error)
        return list(self.gpus)


PASSWORD = "test-password"


def _auth_context(ctx):
    ctx.password = PASSWORD
    ctx.sessions = SessionStore()
    ctx.login_guard = LoginGuard(fail_delay_s=0)


def _workflow(marker: str) -> bytes:
    payload = {
        "last_node_id": 0,
        "last_link_id": 0,
        "nodes": [],
        "links": [],
        "extra": {"marker": marker},
        "version": 0.4,
    }
    return json.dumps(payload).encode("utf-8") + b"\r\n"


def _multipart(fields, files, boundary="----comfyfleetboundary"):
    marker = boundary.encode("ascii")
    chunks = []
    for key, value in fields:
        chunks.append(
            b"--"
            + marker
            + b"\r\n"
            + f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
            + str(value).encode()
            + b"\r\n"
        )
    for key, filename, content in files:
        chunks.append(
            b"--"
            + marker
            + b"\r\n"
            + (
                f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
            ).encode()
            + b"Content-Type: application/json\r\n\r\n"
            + content
            + b"\r\n"
        )
    chunks.append(b"--" + marker + b"--\r\n")
    body = b"".join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.layout = FleetLayout(root / "home")
        self.docker = FakeDocker()
        self.probe = Probe([Gpu(0, "GPU0", "8192 MiB"), Gpu(1, "GPU1", "8192 MiB")])
        self.ui = root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("UI-PLACEHOLDER-MARK\n", encoding="utf-8")
        (root / "secret.txt").write_text("SECRET-MARK\n", encoding="utf-8")
        (self.ui / "leak.txt").symlink_to(root / "secret.txt")
        self.ctx = ApiContext(
            layout=self.layout,
            docker=self.docker,
            detect_gpus=self.probe,
            port_in_use=lambda _port: False,
            ui_dir=self.ui,
            use_env_limit=True,
        )
        _auth_context(self.ctx)
        self.httpd = make_server("127.0.0.1", 0, self.ctx)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _open(self, method, path, data=None, headers=None, auth=True):
        merged = dict(headers or {})
        if auth and "Authorization" not in merged:
            merged["Authorization"] = f"Bearer {PASSWORD}"
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=merged,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _body(self, status, raw):
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload.get("ok"), status < 400)
        return payload

    def test_health_is_public_and_has_no_fleet_data(self):
        status, raw = self._open("GET", "/api/health", auth=False)
        self.assertEqual(status, 200)
        payload = self._body(status, raw)
        self.assertEqual(payload["auth"], "required")
        self.assertNotIn("phase", payload)
        self.assertNotIn("no-op", payload["note"])
        self.assertNotIn(PASSWORD, raw.decode("utf-8"))
        self.assertNotIn("instances", payload)

    def test_gpus_and_probe_failure(self):
        status, raw = self._open("GET", "/api/gpus")
        self.assertEqual(status, 200)
        payload = self._body(status, raw)
        self.assertEqual(
            payload["gpus"],
            [
                {"index": 0, "name": "GPU0", "memory": "8192 MiB"},
                {"index": 1, "name": "GPU1", "memory": "8192 MiB"},
            ],
        )
        self.probe.error = "nvidia-smi was not found. Install the NVIDIA driver."
        status, raw = self._open("GET", "/api/gpus")
        self.assertEqual(status, 503)
        payload = json.loads(raw.decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertIn("nvidia-smi", payload["error"])

    def test_multipart_create_start_stop_and_open_url(self):
        raw_workflow = _workflow("portrait-bytes")
        body, content_type = _multipart(
            [("gpu", "0")],
            [("workflow", "Portrait.json", raw_workflow)],
        )
        seen = []

        def spy(action):
            seen.append(action)
            return authorize(action)

        with mock.patch("comfyfleet.control.authorize", spy):
            status, raw = self._open(
                "POST",
                "/api/instances",
                data=body,
                headers={"Content-Type": content_type},
            )
        self.assertEqual(status, 200, raw)
        payload = self._body(status, raw)
        self.assertFalse(payload["started"])
        self.assertIsNone(payload["warning"])
        instance = payload["instance"]
        self.assertEqual(instance["name"], "portrait")
        self.assertEqual(instance["status"], "created")
        self.assertEqual(instance["port"], 8188)
        self.assertEqual(instance["gpus"], [0])
        self.assertIsNone(instance["url"])
        # create reserves ports through list_instances, which calls authorize("list").
        self.assertEqual(seen, ["create", "list"])
        self.assertNotIn(("start", "portrait"), self.docker.calls)
        args = self.docker.containers["portrait"]["args"]
        root = self.layout.root
        self.assertIn(f"{root}/models:/opt/ComfyUI/models", args)
        self.assertIn(f"{root}/custom_nodes_portrait:/opt/ComfyUI/custom_nodes", args)
        self.assertIn(f"{root}/files/portrait/input:/opt/ComfyUI/input", args)
        self.assertIn(f"{root}/files/portrait/output:/opt/ComfyUI/output", args)
        self.assertIn(f"{root}/files/portrait/temp:/opt/ComfyUI/temp", args)
        self.assertIn(f"{root}/files/portrait:/opt/comfyfleet/instance", args)
        self.assertEqual(args[args.index("--restart") + 1], "no")
        self.assertEqual(args[args.index("--gpus") + 1], "device=0")
        self.assertEqual(args[args.index("-p") + 1], "8188:8188")
        stored = (root / "files" / "portrait" / "default_workflow.json").read_bytes()
        self.assertEqual(stored, raw_workflow)

        status, raw = self._open("GET", "/api/instances")
        listed = self._body(status, raw)["instances"]
        self.assertEqual(len(listed), 1)
        self.assertIsNone(listed[0]["url"])
        self.assertEqual(listed[0]["status"], "created")

        status, raw = self._open("POST", "/api/instances/portrait/start")
        self.assertEqual(status, 200, raw)
        started = self._body(status, raw)
        self.assertTrue(started["started"])
        self.assertEqual(started["instance"]["status"], "running")
        self.assertEqual(started["instance"]["url"], "http://127.0.0.1:8188")
        self.assertIn(("start", "portrait"), self.docker.calls)
        self.assertNotIn("build", [call[0] for call in self.docker.calls])

        viewed = dispatch(
            self.ctx,
            "GET",
            "/api/instances",
            "phone.lan:9100",
            b"",
            None,
            {"Authorization": f"Bearer {PASSWORD}"},
        )
        listed = json.loads(viewed.body.decode("utf-8"))["instances"]
        self.assertEqual(listed[0]["url"], "http://phone.lan:8188")

        status, raw = self._open("POST", "/api/instances/portrait/stop")
        stopped = self._body(status, raw)
        self.assertEqual(stopped["instance"]["status"], "exited")
        self.assertIsNone(stopped["instance"]["url"])
        self.assertEqual(stopped["instance"]["port"], 8188)
        self.assertNotIn(("rm", "portrait"), self.docker.calls)
        self.assertTrue((root / "files" / "portrait" / "comfyfleet.json").is_file())

    def test_json_workflow_path_start_flag_and_next_port(self):
        sources = Path(self.tmp.name) / "src"
        sources.mkdir()
        first = sources / "Portrait.json"
        second = sources / "Background.json"
        first.write_bytes(_workflow("one"))
        second.write_bytes(_workflow("two"))
        created = self._post_json(
            {"workflow_path": str(first), "gpus": "0", "start": False, "force": False}
        )
        self.assertEqual(created["instance"]["name"], "portrait")
        self.assertEqual(created["instance"]["port"], 8188)
        self.assertFalse(created["started"])
        self.assertEqual(self.docker.status("portrait"), "created")

        started = self._post_json({"workflow_path": str(second), "gpu": 1, "start": True})
        self.assertEqual(started["instance"]["name"], "background")
        self.assertEqual(started["instance"]["port"], 8189)
        self.assertEqual(started["instance"]["gpus"], [1])
        self.assertTrue(started["started"])
        self.assertEqual(started["instance"]["status"], "running")
        self.assertEqual(started["instance"]["url"], "http://127.0.0.1:8189")
        create_args = self.docker.containers["background"]["args"]
        self.assertEqual(create_args[create_args.index("--gpus") + 1], "device=1")

    def test_missing_workflow_does_not_create(self):
        cases = [
            (b"", {}),
            (b"{}", {"Content-Type": "application/json"}),
            (
                json.dumps({"gpu": "0", "start": True}).encode(),
                {"Content-Type": "application/json"},
            ),
            _multipart_request([("gpu", "0"), ("start", "true")]),
        ]
        for body, headers in cases:
            status, raw = self._open("POST", "/api/instances", data=body, headers=headers)
            self.assertEqual(status, 400, raw)
            payload = json.loads(raw.decode("utf-8"))
            self.assertFalse(payload["ok"])
            self.assertIn("workflow", payload["error"].lower())
            self.assertIn("no baked default", payload["error"].lower())
        self.assertEqual(self.docker.calls, [])
        self.assertEqual(self.probe.calls, 0)

    def test_missing_workflow_file_and_both_sources(self):
        status, raw = self._open(
            "POST",
            "/api/instances",
            data=json.dumps({"workflow_path": "/tmp/does-not-exist-comfyfleet.json", "gpu": "0"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400, raw)
        self.assertIn("not found", json.loads(raw.decode("utf-8"))["error"])
        self.assertEqual(self.docker.calls, [])

        body, content_type = _multipart(
            [("workflow_path", "/tmp/also-missing.json"), ("gpu", "0")],
            [("workflow", "Portrait.json", _workflow("both"))],
        )
        status, raw = self._open(
            "POST",
            "/api/instances",
            data=body,
            headers={"Content-Type": content_type},
        )
        self.assertEqual(status, 400, raw)
        self.assertIn("not both", json.loads(raw.decode("utf-8"))["error"])
        self.assertEqual(self.docker.calls, [])

    def test_urlencoded_workflow_path(self):
        path = Path(self.tmp.name) / "My Flow.json"
        path.write_bytes(_workflow("form"))
        from urllib.parse import urlencode

        status, raw = self._open(
            "POST",
            "/api/instances",
            data=urlencode({"workflow_path": str(path), "gpu": "0"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 200, raw)
        payload = self._body(status, raw)
        self.assertEqual(payload["instance"]["name"], "my_flow")
        self.assertFalse(payload["started"])

    def test_launch_flags_reach_docker_create_and_strip_listen(self):
        path = Path(self.tmp.name) / "Portrait.json"
        path.write_bytes(_workflow("flags"))
        created = self._post_json(
            {
                "workflow_path": str(path),
                "gpu": "0",
                "vram": "lowvram",
                "attention": "use-flash-attention",
                "flags": ["disable-dynamic-vram", "disable-xformers"],
                "reserve_vram": 1.5,
                "extra_args": "--listen 127.0.0.1 --port 1 --cache-none",
            }
        )
        argv = created["instance"]["launch"]["argv"]
        self.assertEqual(
            argv,
            [
                "--lowvram",
                "--use-flash-attention",
                "--disable-dynamic-vram",
                "--disable-xformers",
                "--reserve-vram",
                "1.5",
                "--cache-none",
            ],
        )
        args = self.docker.containers["portrait"]["args"]
        self.assertEqual(args[args.index("comfyfleet:phase1") + 1 :], argv)
        self.assertNotIn("--listen", args)

        body, content_type = _multipart(
            [
                ("workflow_path", str(path)),
                ("gpu", "0"),
                ("force", "true"),
                ("vram", "--novram"),
                ("flags", "--disable-xformers"),
                ("extra_args", "--listen=0.0.0.0 --mmap-torch-files"),
            ],
            [],
        )
        status, raw = self._open(
            "POST",
            "/api/instances",
            data=body,
            headers={"Content-Type": content_type},
        )
        self.assertEqual(status, 200, raw)
        replaced = self._body(status, raw)
        self.assertEqual(
            replaced["instance"]["launch"]["argv"],
            ["--novram", "--disable-xformers", "--mmap-torch-files"],
        )

        status, raw = self._open(
            "POST",
            "/api/instances",
            data=json.dumps(
                {
                    "workflow_path": str(path),
                    "gpu": "0",
                    "force": True,
                    "vram": "lowvram",
                    "flags": "--cpu",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400, raw)
        self.assertIn("cannot be combined", json.loads(raw.decode("utf-8"))["error"])

    def test_collision_and_force(self):
        path = Path(self.tmp.name) / "Portrait.json"
        path.write_bytes(_workflow("v1"))
        self._post_json({"workflow_path": str(path), "gpu": "0"})
        status, raw = self._open(
            "POST",
            "/api/instances",
            data=json.dumps({"workflow_path": str(path), "gpu": "0"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400, raw)
        self.assertIn("already exists", json.loads(raw.decode("utf-8"))["error"])
        replaced = self._post_json({"workflow_path": str(path), "gpu": "0", "force": True})
        self.assertEqual(replaced["instance"]["name"], "portrait")
        self.assertIn(("rm", "portrait"), self.docker.calls)

    def test_unknown_instance_and_routes(self):
        status, raw = self._open("POST", "/api/instances/missing/start")
        self.assertEqual(status, 400)
        self.assertIn("no instance named", json.loads(raw.decode("utf-8"))["error"])
        status, raw = self._open("GET", "/api/instances/portrait/start")
        self.assertEqual(status, 405)
        status, raw = self._open("POST", "/api/instances/portrait/delete")
        self.assertEqual(status, 404)
        status, raw = self._open("POST", "/api/nope")
        self.assertEqual(status, 404)

    def test_static_placeholder_and_traversal(self):
        status, raw = self._open("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"UI-PLACEHOLDER-MARK", raw)
        status, raw = self._open("GET", "/leak.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(b"SECRET-MARK", raw)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/../secret.txt")
        response = connection.getresponse()
        body = response.read()
        self.assertIn(response.status, (302, 404))
        self.assertNotIn(b"SECRET-MARK", body)
        connection.close()

        self.ctx.ui_dir = None
        status, raw = self._open("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"placeholder", raw.lower())
        self.assertIn(b"Bearer", raw)

    def test_body_too_large(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("POST", "/api/instances")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 413)
        self.assertIn(b"32 MiB", body)
        connection.close()
        self.assertEqual(self.docker.calls, [])

    def test_adapter_does_not_reimplement_docker(self):
        text = Path(__file__).resolve().parents[1].joinpath("comfyfleet", "http_api.py").read_text(
            encoding="utf-8"
        )
        for banned in ("build_create_args", "subprocess", "NVIDIA_VISIBLE_DEVICES", "docker create"):
            self.assertNotIn(banned, text)

    def _post_json(self, payload):
        status, raw = self._open(
            "POST",
            "/api/instances",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, raw)
        return self._body(status, raw)


def _multipart_request(fields):
    body, content_type = _multipart(fields, [])
    return body, {"Content-Type": content_type}


class RequestHostTests(unittest.TestCase):
    def test_strips_control_port(self):
        self.assertEqual(request_host("phone.lan:9100"), "phone.lan")
        self.assertEqual(request_host("127.0.0.1:9100"), "127.0.0.1")
        self.assertEqual(request_host("[::1]:9100"), "[::1]")
        self.assertEqual(request_host(None, "127.0.0.1"), "127.0.0.1")
        self.assertEqual(request_host("   "), "127.0.0.1")


class CliServeTests(unittest.TestCase):
    def test_ui_and_serve_defaults(self):
        parser = build_parser()
        for command in ("ui", "serve"):
            args = parser.parse_args([command])
            self.assertEqual(args.host, DEFAULT_BIND_HOST)
            self.assertEqual(args.host, "0.0.0.0")
            self.assertEqual(args.port, DEFAULT_BIND_PORT)
            self.assertEqual(args.port, 9100)
            self.assertIsNone(args.ui_dir)
            self.assertEqual(args.func.__name__, "_cmd_ui")
        args = parser.parse_args(["serve", "--host", "127.0.0.1", "--port", "9200", "--ui-dir", "ui"])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9200)
        self.assertEqual(args.ui_dir, "ui")
