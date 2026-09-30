"""The control pages are a client of the HTTP API.

They are served by ``comfyfleet ui`` from ``ui/``. These tests do not
reimplement create, start, stop, or Docker.
"""

import json
import threading
import unittest
from pathlib import Path

from comfyfleet.auth import LoginGuard, SessionStore
from comfyfleet.http_api import ApiContext, make_server
from comfyfleet.paths import FleetLayout


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
PASSWORD = "test-password"


class IdleDocker:
    def __init__(self):
        self.calls = []

    def create(self, args):
        self.calls.append(("create", list(args)))

    def start(self, name):
        self.calls.append(("start", name))

    def stop(self, name):
        self.calls.append(("stop", name))

    def remove(self, name):
        self.calls.append(("rm", name))

    def update_restart(self, name, policy):
        self.calls.append(("update-restart", name, policy))

    def status(self, name):
        return None

    def running_names(self):
        return []


class UiContractTests(unittest.TestCase):
    def test_pages_call_the_published_api_only(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        login = (UI / "login.html").read_text(encoding="utf-8")
        script = (UI / "app.js").read_text(encoding="utf-8")
        css = (UI / "app.css").read_text(encoding="utf-8")
        self.assertIn("comfyfleet-logo-ships.jpg", html)
        self.assertIn("New instance", html)
        self.assertIn("Create &amp; start", html)
        self.assertIn("Trusted LAN", html)
        self.assertIn("Log out", html)
        self.assertNotIn("not included", html.lower())
        self.assertIn("/api/login", login)
        self.assertIn('type="password"', login)
        self.assertIn('credentials: "same-origin"', login)
        self.assertIn("backdrop-filter", css)
        self.assertIn("--hit: 50px", css)
        self.assertIn('instance.status === "running"', script)
        self.assertIn("/api/health", script)
        self.assertIn("/api/gpus", script)
        self.assertIn("/api/instances", script)
        self.assertIn('"/api/instances"', script)
        self.assertIn('body.append("workflow"', script)
        self.assertIn("workflow_path", script)
        self.assertIn('body.append("gpus"', script)
        self.assertIn("submitCreate(false)", script)
        self.assertIn("submitCreate(true)", script)
        self.assertIn('credentials: "same-origin"', script)
        self.assertIn("/api/logout", script)
        self.assertIn("session expired", script)
        lowered = script.lower()
        for banned in (
            "docker create",
            "docker start",
            "subprocess",
            "nvidia-smi",
            "/opt/comfyui",
            "build_create_args",
        ):
            self.assertNotIn(banned, lowered)
        self.assertNotIn("http://", script)
        self.assertTrue((UI / "comfyfleet-logo-ships.jpg").is_file())

    def test_control_server_serves_the_ui(self):
        docker = IdleDocker()
        context = ApiContext(
            layout=FleetLayout(ROOT / "does-not-need-to-exist"),
            docker=docker,
            detect_gpus=lambda: [],
            port_in_use=lambda _port: False,
            ui_dir=UI,
            password=PASSWORD,
            sessions=SessionStore(),
            login_guard=LoginGuard(fail_delay_s=0),
        )
        httpd = make_server("127.0.0.1", 0, context)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        try:
            anon = _get(port, "/")
            self.assertEqual(anon.status, 302)
            self.assertIn("/login", anon.getheader("Location") or "")
            anon.read()
            login = _get(port, "/login")
            self.assertEqual(login.status, 200)
            login_body = login.read()
            self.assertIn(b"ComfyFleet", login_body)
            self.assertIn(b"password", login_body)
            logo = _get(port, "/comfyfleet-logo-ships.jpg")
            self.assertEqual(logo.status, 200)
            self.assertTrue(logo.read().startswith(b"\xff\xd8"))
            cookie = _login(port)
            page = _get(port, "/", cookie=cookie)
            self.assertEqual(page.status, 200)
            body = page.read()
            self.assertIn(b"ComfyFleet", body)
            self.assertIn(b"/comfyfleet-logo-ships.jpg", body)
            self.assertIn(b"New instance", body)
            self.assertNotIn(b"not included", body.lower())
            script = _get(port, "/app.js", cookie=cookie)
            self.assertEqual(script.status, 200)
            self.assertIn(b"/api/instances", script.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        self.assertEqual(docker.calls, [])


def _get(port: int, path: str, cookie: str | None = None):
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Cookie": cookie} if cookie else {}
    connection.request("GET", path, headers=headers)
    return connection.getresponse()


def _login(port: int) -> str:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps({"password": PASSWORD}).encode("utf-8")
    connection.request(
        "POST",
        "/api/login",
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    response = connection.getresponse()
    response.read()
    set_cookie = response.getheader("Set-Cookie") or ""
    connection.close()
    return set_cookie.split(";", 1)[0]


if __name__ == "__main__":
    unittest.main()
