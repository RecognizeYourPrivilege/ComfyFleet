"""Thin HTTP adapter over ``comfyfleet.control``.

This server does not create containers, assign ports, copy workflows, or
select GPUs itself. Those stay in ``comfyfleet.control`` and
``comfyfleet.gpu``. Auth is the existing no-op ``authorize()`` stub
(Phase 3). There is no login.

Contract: CONTROL_HTTP.md.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from email.parser import Parser
from email.policy import compat32
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from comfyfleet import __version__
from comfyfleet.control import (
    Instance,
    create_instance,
    list_instances,
    start_instance,
    stop_instance,
)
from comfyfleet.errors import FleetError
from comfyfleet.gpu import Gpu
from comfyfleet.paths import FleetLayout
from comfyfleet.public_host import (
    PUBLIC_HOST_ENV,
    configured_public_host,
    open_host,
    request_host,
)

DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_BIND_PORT = 9100
MAX_BODY_BYTES = 32 * 1024 * 1024

_JSON = "application/json; charset=utf-8"
_MISSING_WORKFLOW = (
    "workflow is required. Upload a workflow JSON file as multipart field "
    "'workflow', or pass 'workflow_path' to a .json file this process can read. "
    "There is no baked default workflow."
)
_AUTH_NOTE = (
    "Auth is a Phase 3 stub. authorize() is a no-op and this server does not "
    "check a login or token. Serve it only on a trusted LAN; it is not an "
    "internet-exposed auth product."
)
_PLACEHOLDER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ComfyFleet</title>
</head>
<body>
  <p>ComfyFleet control API placeholder. The iOS-like control UI is not included.</p>
  <p>API: <a href="/api/health">/api/health</a>. See CONTROL_HTTP.md.</p>
  <p>Trusted LAN only. Auth is a Phase 3 stub.</p>
</body>
</html>
"""

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class HTTPStatusError(Exception):
    """A response this adapter produces before or around control."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class ApiContext:
    """Dependencies the adapter passes into ``comfyfleet.control``."""

    layout: FleetLayout
    docker: object
    detect_gpus: Callable[[], list[Gpu]]
    port_in_use: Callable[[int], bool]
    ui_dir: Path | None = None
    use_env_limit: bool = True
    host_fallback: str = "127.0.0.1"
    public_host: str | None = None


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str


@dataclass
class _Upload:
    filename: str
    data: bytes


@dataclass
class _CreateForm:
    upload: _Upload | None
    workflow_path: str | None
    gpu: str | None
    gpus: str | None
    start: bool
    force: bool


def resolve_ui_dir(explicit: str | None = None) -> Path | None:
    """Find a static UI directory. Missing is fine; the API still serves."""

    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise FleetError(f"ui directory not found: {path}")
        return path
    for candidate in (Path.cwd() / "ui", Path(__file__).resolve().parent.parent / "ui"):
        if candidate.is_dir():
            return candidate
    return None


def serve(
    *,
    host: str = DEFAULT_BIND_HOST,
    port: int = DEFAULT_BIND_PORT,
    ui_dir: str | None = None,
    layout: FleetLayout | None = None,
    docker: object | None = None,
    detect_gpus: Callable[[], list[Gpu]] | None = None,
    port_in_use: Callable[[int], bool] | None = None,
    use_env_limit: bool = True,
) -> None:
    """Bind the control API and serve until interrupted."""

    if not host or not str(host).strip():
        raise FleetError("bind host is required")
    if port < 1 or port > 65535:
        raise FleetError(f"port must be 1..65535, got {port}")
    if layout is None:
        layout = FleetLayout()
    if docker is None:
        from comfyfleet.docker import DockerCLI

        docker = DockerCLI()
    if detect_gpus is None:
        from comfyfleet.gpu import detect_gpus as default_detect_gpus

        detect_gpus = default_detect_gpus
    if port_in_use is None:
        from comfyfleet.ports import effective_port_in_use as default_port_in_use

        port_in_use = default_port_in_use
    public_host = configured_public_host()
    context = ApiContext(
        layout=layout,
        docker=docker,
        detect_gpus=detect_gpus,
        port_in_use=port_in_use,
        ui_dir=resolve_ui_dir(ui_dir),
        use_env_limit=use_env_limit,
        host_fallback="127.0.0.1" if host in {"0.0.0.0", "::"} else host,
        public_host=public_host,
    )
    try:
        httpd = make_server(host, port, context)
    except OSError as exc:
        raise FleetError(f"cannot bind {host}:{port}: {exc}") from exc
    print(
        f"comfyfleet: control API on http://{host}:{port}/",
        file=sys.stderr,
    )
    print(
        "comfyfleet: trusted LAN only. Auth is a Phase 3 stub "
        "(authorize() is a no-op). Do not expose this port to the internet.",
        file=sys.stderr,
    )
    if public_host:
        print(
            f"comfyfleet: Open links use http://{public_host}:<instance-port> ({PUBLIC_HOST_ENV}).",
            file=sys.stderr,
        )
    else:
        print(
            "comfyfleet: Open links use the request Host header when it is a safe "
            f"hostname or IP. Set {PUBLIC_HOST_ENV} to pin the LAN name.",
            file=sys.stderr,
        )
    if context.ui_dir is None:
        print("comfyfleet: no ui/ directory; / is a placeholder.", file=sys.stderr)
    else:
        print(f"comfyfleet: static files from {context.ui_dir}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        raise
    finally:
        httpd.server_close()


def make_server(host: str, port: int, context: ApiContext) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def log_message(self, fmt: str, *args) -> None:
            print(f"comfyfleet: {self.address_string()} {fmt % args}", file=sys.stderr)

        def _respond(self, method: str) -> None:
            try:
                body = _read_body(self)
                path = urlsplit(self.path).path
                response = dispatch(context, method, path, self.headers.get("Host"), body, self.headers.get("Content-Type"))
            except HTTPStatusError as exc:
                response = _json(exc.status, {"ok": False, "error": exc.message})
            except FleetError as exc:
                response = _json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                print(f"comfyfleet: internal error: {exc}", file=sys.stderr)
                response = _json(500, {"ok": False, "error": "internal error"})
            payload = response.body
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

    class ControlHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True

    return ControlHTTPServer((host, port), Handler)


def dispatch(
    context: ApiContext,
    method: str,
    path: str,
    host_header: str | None,
    body: bytes,
    content_type: str | None,
) -> Response:
    """Route one request. Control errors propagate as ``FleetError``."""

    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    host = open_host(host_header, context.host_fallback, context.public_host)
    if path == "/api/health":
        _require_method(method, "GET")
        return _health()
    if path == "/api/gpus":
        _require_method(method, "GET")
        return _gpus(context)
    if path == "/api/instances":
        if method == "GET":
            return _list(context, host)
        if method == "POST":
            return _create(context, host, body, content_type)
        raise HTTPStatusError(405, "method not allowed")
    action = _instance_action(path)
    if action is not None:
        name, verb = action
        _require_method(method, "POST")
        if verb == "start":
            return _start(context, host, name)
        return _stop(context, host, name)
    if path.startswith("/api/"):
        raise HTTPStatusError(404, "not found")
    if method != "GET":
        raise HTTPStatusError(405, "method not allowed")
    return _static(context, path)


def _health() -> Response:
    return _json(
        200,
        {
            "ok": True,
            "service": "comfyfleet",
            "version": __version__,
            "phase": 2,
            "auth": "phase3-stub",
            "note": _AUTH_NOTE,
        },
    )


def _gpus(context: ApiContext) -> Response:
    try:
        found = context.detect_gpus()
    except FleetError as exc:
        raise HTTPStatusError(503, str(exc)) from exc
    return _json(
        200,
        {
            "ok": True,
            "gpus": [
                {"index": gpu.index, "name": gpu.name, "memory": gpu.memory}
                for gpu in found
            ],
        },
    )


def _list(context: ApiContext, host: str) -> Response:
    rows = list_instances(context.layout, context.docker)
    return _json(
        200,
        {
            "ok": True,
            "instances": [
                _instance_json(instance, status, host) for instance, status in rows
            ],
        },
    )


def _create(context: ApiContext, host: str, body: bytes, content_type: str | None) -> Response:
    form = _parse_create_form(body, content_type)
    if form.upload is None and not form.workflow_path:
        raise FleetError(_MISSING_WORKFLOW)
    if form.upload is not None and form.workflow_path:
        raise FleetError("pass either a workflow upload or workflow_path, not both.")
    if form.upload is not None:
        workflow = _materialize_upload(form.upload)
        cleanup = workflow.parent
    else:
        workflow = Path(form.workflow_path or "")
        cleanup = None
    try:
        result = create_instance(
            workflow,
            layout=context.layout,
            docker=context.docker,
            gpus=context.detect_gpus(),
            gpu=form.gpu,
            gpus_spec=form.gpus,
            interactive=False,
            prompt=None,
            start=form.start,
            force=form.force,
            port_in_use=context.port_in_use,
            use_env_limit=context.use_env_limit,
        )
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)
    status = context.docker.status(result.instance.name) or "missing"
    return _json(
        200,
        {
            "ok": True,
            "started": result.started,
            "warning": result.warning,
            "instance": _instance_json(result.instance, status, host),
        },
    )


def _start(context: ApiContext, host: str, name: str) -> Response:
    result = start_instance(
        name,
        layout=context.layout,
        docker=context.docker,
        gpus=context.detect_gpus(),
        port_in_use=context.port_in_use,
        use_env_limit=context.use_env_limit,
    )
    status = context.docker.status(result.instance.name) or "missing"
    return _json(
        200,
        {
            "ok": True,
            "started": result.started,
            "warning": result.warning,
            "instance": _instance_json(result.instance, status, host),
        },
    )


def _stop(context: ApiContext, host: str, name: str) -> Response:
    instance = stop_instance(name, layout=context.layout, docker=context.docker)
    status = context.docker.status(instance.name) or "missing"
    return _json(200, {"ok": True, "instance": _instance_json(instance, status, host)})


def _instance_json(instance: Instance, status: str, host: str) -> dict:
    running = status == "running"
    return {
        "name": instance.name,
        "status": status,
        "port": instance.port,
        "gpus": list(instance.gpus),
        "url": f"http://{host}:{instance.port}" if running else None,
    }


def _instance_action(path: str) -> tuple[str, str] | None:
    prefix = "/api/instances/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix) :]
    name, sep, verb = rest.partition("/")
    if sep != "/" or not name or not verb or "/" in verb:
        return None
    if verb not in {"start", "stop"}:
        return None
    decoded = unquote(name)
    if (
        not decoded
        or "/" in decoded
        or "\\" in decoded
        or "\x00" in decoded
        or decoded in {".", ".."}
    ):
        raise FleetError(f"invalid instance name {decoded!r}")
    return decoded, verb


def _static(context: ApiContext, path: str) -> Response:
    if path in {"", "/"}:
        path = "/index.html"
    if context.ui_dir is not None:
        found = _safe_static(context.ui_dir, path)
        if found is not None:
            mime = _STATIC_TYPES.get(found.suffix.lower(), "application/octet-stream")
            return Response(200, found.read_bytes(), mime)
    if path == "/index.html":
        return Response(200, _PLACEHOLDER_HTML.encode("utf-8"), "text/html; charset=utf-8")
    raise HTTPStatusError(404, "not found")


def _safe_static(ui_dir: Path, url_path: str) -> Path | None:
    if not url_path.startswith("/") or "\\" in url_path or "\x00" in url_path:
        return None
    raw = unquote(url_path).lstrip("/")
    if not raw or "\x00" in raw:
        return None
    parts = Path(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    root = ui_dir.resolve()
    candidate = (root.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def _parse_create_form(body: bytes, content_type: str | None) -> _CreateForm:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if body == b"" and media in {"", "application/json", "application/x-www-form-urlencoded"}:
        fields: dict[str, str] = {}
        upload = None
    elif media == "application/json":
        fields = _json_fields(body)
        upload = None
    elif media == "application/x-www-form-urlencoded":
        fields = _urlencoded_fields(body)
        upload = None
    elif media == "multipart/form-data":
        fields, upload = _multipart_fields(content_type or "", body)
    else:
        raise HTTPStatusError(
            415,
            "Content-Type must be application/json, multipart/form-data, "
            "or application/x-www-form-urlencoded",
        )
    return _CreateForm(
        upload=upload,
        workflow_path=_optional_str(fields.get("workflow_path")),
        gpu=_optional_str(fields.get("gpu")),
        gpus=_optional_str(fields.get("gpus")),
        start=_as_bool(fields.get("start"), default=False),
        force=_as_bool(fields.get("force"), default=False),
    )


def _json_fields(body: bytes) -> dict[str, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FleetError(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FleetError("JSON body must be an object with workflow_path, gpu, gpus, start, and force.")
    fields: dict[str, str] = {}
    for key in ("workflow_path", "gpu", "gpus", "start", "force"):
        if key not in payload or payload[key] is None:
            continue
        value = payload[key]
        if isinstance(value, bool):
            fields[key] = "true" if value else "false"
        elif isinstance(value, int) and not isinstance(value, bool):
            fields[key] = str(value)
        elif isinstance(value, str):
            fields[key] = value
        else:
            raise FleetError(f"field {key!r} must be a string, boolean, or integer")
    return fields


def _urlencoded_fields(body: bytes) -> dict[str, str]:
    from urllib.parse import parse_qs

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FleetError("form body is not UTF-8") from exc
    parsed = parse_qs(text, keep_blank_values=True)
    fields: dict[str, str] = {}
    for key in ("workflow_path", "gpu", "gpus", "start", "force"):
        values = parsed.get(key)
        if not values:
            continue
        if len(values) > 1:
            raise FleetError(f"duplicate form field {key!r}")
        fields[key] = values[0]
    return fields


def _multipart_fields(content_type: str, body: bytes) -> tuple[dict[str, str], _Upload | None]:
    boundary = _boundary(content_type)
    fields: dict[str, str] = {}
    upload: _Upload | None = None
    for headers, data in _multipart_parts(boundary, body):
        name = headers.get("name")
        if not name:
            continue
        filename = headers.get("filename")
        if filename:
            safe = _upload_filename(filename)
            if name != "workflow":
                raise FleetError("upload the workflow JSON as the multipart field 'workflow'")
            if upload is not None:
                raise FleetError("duplicate workflow upload")
            upload = _Upload(filename=safe, data=data)
            continue
        if name not in {"workflow_path", "gpu", "gpus", "start", "force"}:
            continue
        if name in fields:
            raise FleetError(f"duplicate form field {name!r}")
        try:
            fields[name] = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FleetError(f"form field {name!r} is not UTF-8") from exc
    return fields, upload


def _boundary(content_type: str) -> bytes:
    message = Parser(policy=compat32).parsestr(f"Content-Type: {content_type}\n")
    value = message.get_param("boundary", header="Content-Type")
    if isinstance(value, tuple):
        value = value[-1]
    if not value or not isinstance(value, str):
        raise FleetError("multipart body is missing a boundary")
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FleetError("multipart boundary must be ASCII") from exc


def _multipart_parts(boundary: bytes, body: bytes):
    marker = b"--" + boundary
    chunks = body.split(marker)
    if len(chunks) < 2:
        raise FleetError("malformed multipart body")
    for chunk in chunks[1:]:
        if chunk.startswith(b"--"):
            break
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]
        header_blob, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            header_blob, sep, data = chunk.partition(b"\n\n")
        if not sep:
            raise FleetError("malformed multipart body")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        elif data.endswith(b"\n"):
            data = data[:-1]
        yield _part_headers(header_blob), data


def _part_headers(header_blob: bytes) -> dict[str, str]:
    text = header_blob.decode("iso-8859-1")
    message = Parser(policy=compat32).parsestr(text + "\n")
    found: dict[str, str] = {}
    for key in ("name", "filename"):
        value = message.get_param(key, header="Content-Disposition")
        if isinstance(value, tuple):
            value = value[-1]
        if isinstance(value, str) and value != "":
            found[key] = value
    return found


def _upload_filename(filename: str) -> str:
    base = filename.replace("\\", "/").split("/")[-1]
    if not base or base in {".", ".."} or "\x00" in base or "/" in base:
        raise FleetError("uploaded workflow filename is invalid")
    if not base.lower().endswith(".json"):
        raise FleetError(
            f"uploaded workflow must be a .json file, got {base!r}. "
            "There is no baked default workflow."
        )
    return base


def _materialize_upload(upload: _Upload) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="comfyfleet-upload-"))
    path = directory / upload.filename
    try:
        path.write_bytes(upload.data)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return path


def _optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise FleetError(f"expected true or false, got {value!r}")


def _require_method(method: str, allowed: str) -> None:
    if method != allowed:
        raise HTTPStatusError(405, "method not allowed")


def _json(status: int, payload: dict) -> Response:
    body = json.dumps(payload, indent=2).encode("utf-8") + b"\n"
    return Response(status, body, _JSON)


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    raw = handler.headers.get("Content-Length")
    if raw is None or raw == "":
        return b""
    try:
        size = int(raw)
    except ValueError as exc:
        raise FleetError("Content-Length must be an integer") from exc
    if size < 0:
        raise FleetError("Content-Length must be >= 0")
    if size > MAX_BODY_BYTES:
        raise HTTPStatusError(413, "request body exceeds 32 MiB")
    data = handler.rfile.read(size)
    if len(data) != size:
        raise FleetError("request body ended before Content-Length")
    return data
