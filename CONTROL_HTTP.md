# ComfyFleet control HTTP API

Contract for the manager control web UI. The server is a thin adapter over `comfyfleet.control` and the `comfyfleet.gpu` probe. It does not implement Docker lifecycle, mounts, port assignment, or GPU selection itself.

The control UI is in `ui/` (`index.html`, `login.html`, `app.css`, `app.js`, `comfyfleet-logo-ships.jpg`). This process serves the fleet shell at `GET /` after sign-in, and the sign-in page at `GET /login`. The pages call the routes below and do not implement Docker lifecycle.

## Serve

The manager image runs this server on container start (`comfyfleet ui` inside the container, `0.0.0.0:9100`). A host checkout can still start it for development:

```bash
comfyfleet ui
# alias:
comfyfleet serve
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address. Use `127.0.0.1` to keep the server on this machine. |
| `--port` | `9100` | Control port. |
| `--ui-dir` | `./ui` if that directory exists, otherwise `<repo>/ui` | Static files for `GET /`. |

Primary URL: `http://<host>:9100/`.

Startup prints that Auth is required and that a trusted LAN is still recommended. `comfyfleet ui` exits non-zero when `COMFYFLEET_PASSWORD` is missing or empty. It does not bind the port in that case.

Host CLI commands (`create`, `start`, `stop`, `restart`, `list`) do not use this server and do not check the session cookie. They are a local process. Inside the manager image, the entrypoint also refuses to start when the password is missing, including when you pass a CLI subcommand to `docker run`. `docker exec` on an already-running manager is still a local process with the Docker socket.

## Auth

Canonical secret: **`COMFYFLEET_PASSWORD`**. One shared password. No accounts, roles, or OAuth. Do not bake a password into the image. Change it by restarting the manager with a new value. Restart drops every session.

`comfyfleet.control.authorize()` allows a local CLI call. On an HTTP request it **fails closed** until the server has accepted a session cookie or Bearer token. Unauthenticated fleet routes return **401** and do not list or change instances.

### Public routes

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/health` | Liveness only. No instance list and no password. |
| `POST` | `/api/login` | JSON `{"password":"<COMFYFLEET_PASSWORD>"}` or form field `password`. |
| `POST` | `/api/logout` | Clears the cookie and the server session. No credential required. |
| `GET` | `/login` | Sign-in page (`ui/login.html`). |
| `GET` | `/app.css`, `/comfyfleet-logo-ships.jpg`, `/favicon.ico`, `/robots.txt` | Assets the sign-in page needs. |

### Protected routes

Every other `/api/*` route, including `GET /api/gpus`, `GET /api/instances`, `POST /api/instances`, `POST /api/instances/{name}/start`, and `POST /api/instances/{name}/stop`. The fleet shell (`GET /`, `GET /index.html`, `GET /app.js`) redirects to `/login` when the request has no valid cookie or Bearer token.

A protected `/api/*` call with no valid credential returns **401**:

```json
{"ok": false, "error": "unauthorized"}
```

If a session cookie was sent and it is no longer valid, and there is no Bearer token, `error` is `session expired`. That string is not a Docker or GPU failure.

Wrong login password:

```json
{"ok": false, "error": "invalid credentials"}
```

There is no separate API-key table. Bearer uses the same shared password:

```http
Authorization: Bearer <COMFYFLEET_PASSWORD>
```

Either the cookie or Bearer is enough. The password is compared with a SHA-256 digest and `hmac.compare_digest`. The server does not log the raw password, the `Authorization` header, or the query string.

Failed logins wait `COMFYFLEET_LOGIN_FAIL_DELAY` seconds (default **0.25**). After **8** failures from the same client address within **60** seconds, `POST /api/login` returns **429** `too many login attempts` until the window passes. This is a small in-memory limit, not a CAPTCHA.

### Session cookie (for the web UI)

Sessions are **in-memory** on the manager process. They are not written to disk. A restart, or a new process, invalidates them. `COMFYFLEET_SESSION_SECRET` is not required.

| Piece | Value |
|---|---|
| Cookie name | `comfyfleet_session` |
| Flags | `HttpOnly`; `Path=/`; `SameSite=Lax`; `Max-Age=43200` (12 hours) |
| `Secure` | Set only when `X-Forwarded-Proto` is `https`. Plain `http://` on a LAN omits `Secure` so the browser will store the cookie. A TLS proxy must send `X-Forwarded-Proto: https`. |
| Login success | **200** `{"ok": true}` and `Set-Cookie`. The JSON body does not contain the session id or the password. |
| Logout | **200** `{"ok": true}` and `Set-Cookie` with `Max-Age=0`. The id is dropped from memory, so replaying the old cookie returns **401**. Bearer still works. |

The browser UI must send the cookie on API calls. `fetch` should use `credentials: "same-origin"` (same-origin is the default; the pages set it explicitly). JavaScript cannot read the cookie (`HttpOnly`). Do not put the password on later fleet requests when the cookie is in use. Do not add `Access-Control-Allow-Origin` that would let another site call these routes without the cookie rules above.

Sign-in page behavior the UI implements:

1. `POST /api/login` with `Content-Type: application/json` and `{"password":"..."}`.
2. On `{"ok": true}`, navigate to `/`.
3. On **401**, show invalid credentials. On **429**, show that sign-in is temporarily limited.
4. Fleet `fetch` calls that return **401** `session expired` navigate to `/login?expired=1`. Other **401** responses navigate to `/login`.
5. Log out with `POST /api/logout` and `credentials: "same-origin"`, then navigate to `/login`.

Scripts can skip the cookie and send `Authorization: Bearer` instead.

Do not expose port 9100 to the public internet. The default bind is every interface so a phone on the LAN can open the UI. Auth is a gate. It is not a full internet-hardening product. Terminate TLS at a reverse proxy if you need HTTPS. ComfyUI instance ports (8188 and up) are not covered by this login.

The UI and the API should be same-origin. Load the UI from `http://<host>:9100/` and call `/api/...` on that origin. This server does not send `Access-Control-Allow-Origin`. A page on another origin will not be able to call the API from a browser.

There is no delete route. Control has no destroy API. There is no HTTP restart route; stop and start, or use `comfyfleet restart` on the CLI. Start and stop do not rebuild the image.

## Endpoints

| Method | Path | Auth | Calls | Success body |
|---|---|---|---|---|
| `GET` | `/api/health` | no | nothing in control | Liveness. No fleet data |
| `POST` | `/api/login` | no | nothing in control | `{"ok": true}` and `Set-Cookie` |
| `POST` | `/api/logout` | no | nothing in control | `{"ok": true}` and a cleared cookie |
| `GET` | `/api/gpus` | yes | `comfyfleet.gpu.detect_gpus` | GPUs the create form can offer |
| `GET` | `/api/instances` | yes | `list_instances` | Every known instance |
| `POST` | `/api/instances` | yes | `create_instance` | The instance just created |
| `POST` | `/api/instances/{name}/start` | yes | `start_instance` | That instance, running |
| `POST` | `/api/instances/{name}/stop` | yes | `stop_instance` | That instance, not running |
| `GET` | `/login` | no | `ui/login.html` | Sign-in page |
| `GET` | `/` and other non-API paths | yes, except login assets | static files under `ui/` | Fleet UI. If `ui/` is missing, an authenticated `/` is a short placeholder |

`{name}` is the instance name from create (the sanitized workflow filename stem). It is URL-safe: lowercase `[a-z0-9_-]`, at most 63 characters.

Query strings are ignored. Send create options in the body so a workflow path is not written into the request line.

### `GET /api/health`

```json
{
  "ok": true,
  "service": "comfyfleet",
  "version": "0.1.0",
  "auth": "required",
  "note": "Liveness only. This response has no fleet data. Fleet routes require a session cookie from POST /api/login or Authorization: Bearer. Trusted LAN is still recommended. This gate is not a full internet-hardening product; terminate TLS at a reverse proxy if you need HTTPS."
}
```

### `GET /api/gpus`

```json
{
  "ok": true,
  "gpus": [
    {"index": 0, "name": "NVIDIA GeForce RTX 4090", "memory": "24576 MiB"}
  ]
}
```

`index` is the integer to send back as `gpu` or inside `gpus`. If `nvidia-smi` is missing, fails, or lists nothing, the probe raises and this route returns **503** with the probe's message. The create form should show that message and not invent a GPU.

### `GET /api/instances`

```json
{
  "ok": true,
  "instances": [
    {
      "name": "portrait",
      "status": "running",
      "port": 8188,
      "gpus": [0],
      "url": "http://192.168.1.20:8188"
    }
  ]
}
```

An empty fleet is `{"ok": true, "instances": []}`. That is not an error.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Instance name. |
| `status` | string | Docker status: `running`, `created`, `exited`, `missing`, or another Docker state. Treat **only** `running` as running. `created` means the container exists and has not been started. |
| `port` | number | Host port reserved for this instance (from 8188 up). Present even when stopped. |
| `gpus` | number[] | GPU indexes chosen at create. |
| `url` | string or null | Open target. A string **only while `status` is `running`**. Otherwise `null`. |

`url` is `http://<open-host>:<port>`. `<open-host>` is `COMFYFLEET_PUBLIC_HOST` when that variable is a hostname or IP (port suffix stripped). Otherwise it is the `Host` header the browser used to reach this control server, with the control port removed, when that header is a safe hostname or IP. A phone that opened `http://192.168.1.20:9100/` gets `http://192.168.1.20:8188` for a running instance. Set `COMFYFLEET_PUBLIC_HOST=192.168.1.20` when Open links should stay on that LAN name even if a request arrives with a different Host. Empty values, `0.0.0.0`, `::`, and values with spaces or slashes are not used; the server then falls back to `127.0.0.1` when it is bound on all interfaces. An invalid `COMFYFLEET_PUBLIC_HOST` is an error when the server starts. Open that URL in a new tab. When `url` is null, Open is disabled. Stop does not destroy the container or the workflow file.

### `POST /api/instances`

Create **requires** an operator workflow. There is no baked default. The server is non-interactive, so GPU selection is required too (`gpu` or `gpus`, not both), same as `comfyfleet create` without a TTY.

Send **one** of:

| Source | How |
|---|---|
| File upload | `multipart/form-data` field `workflow` (a `.json` file). The filename stem becomes the instance name, same sanitizer as the CLI. `My Flow.json` → `my_flow`. |
| Host path | Field `workflow_path`: a `.json` path the `comfyfleet ui` process can read (same user as the server). The path's filename stem is the instance name. |

The JSON body is **create options**, not the Comfy graph. Posting a workflow object as `application/json` does not create an instance.

| Field | Required | Meaning |
|---|---|---|
| `workflow` | one of workflow / `workflow_path` | Multipart file only. |
| `workflow_path` | one of workflow / `workflow_path` | Host path. |
| `gpu` | one of `gpu` / `gpus` | One index, for example `"0"`. |
| `gpus` | one of `gpu` / `gpus` | `"0,1"` or `"all"`. |
| `start` | no | Default **false**. `true` creates and starts that one instance. Leave false to create many and run few. |
| `force` | no | Default **false**. Replace a **stopped** instance with the same name. Refuses while it is running. |

`start` and `force` accept JSON booleans and the strings `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`.

Content types:

- `multipart/form-data` (upload, and/or the text fields above)
- `application/json` (path form only)
- `application/x-www-form-urlencoded` (path form only)

Maximum body size is 32 MiB (`413` above that). Clients must send `Content-Length`.

```json
{
  "ok": true,
  "started": false,
  "warning": null,
  "instance": {
    "name": "portrait",
    "status": "created",
    "port": 8188,
    "gpus": [0],
    "url": null
  }
}
```

`warning` is a string when control would have printed a concurrency warning, otherwise `null`. `started` is true only when this call started the container. `status` is read back from Docker after control returns.

A failed `start: true` can still leave a created instance behind (same as the CLI). Refresh the list.

### `POST /api/instances/{name}/start`

```json
{
  "ok": true,
  "started": true,
  "warning": null,
  "instance": {
    "name": "portrait",
    "status": "running",
    "port": 8188,
    "gpus": [0],
    "url": "http://192.168.1.20:8188"
  }
}
```

Starting an instance that is already running returns 200 and the running instance. No image rebuild.

### `POST /api/instances/{name}/stop`

```json
{
  "ok": true,
  "instance": {
    "name": "portrait",
    "status": "exited",
    "port": 8188,
    "gpus": [0],
    "url": null
  }
}
```

Stopping an already stopped instance returns 200. The container, mounts, and workflow file stay.

## Errors

```json
{"ok": false, "error": "workflow is required. Upload a workflow JSON file ... There is no baked default workflow."}
```

Show `error` to the operator.

| Status | When |
|---|---|
| 400 | Control or validation refused the call: missing workflow, bad JSON, unknown instance, name collision, GPU selection, `nvidia-smi` failure on create/start, and the other `FleetError` messages. |
| 401 | Protected route without a valid session cookie or Bearer token (`unauthorized` or `session expired`). Wrong login password is also 401, with `invalid credentials`. |
| 404 | Unknown path, after Auth succeeds. Unauthenticated `/api/*` is 401, not 404. |
| 405 | Wrong method on a known path. |
| 413 | Body larger than 32 MiB. |
| 415 | Unsupported `Content-Type` on create. |
| 429 | Too many failed login attempts from this client. |
| 503 | `GET /api/gpus` and the GPU probe failed. |
| 500 | Unexpected server error. `error` is `internal error`. |

Unknown instances are **400** with control's "no instance named …" text, not 404.

## Examples

```bash
curl -s http://127.0.0.1:9100/api/health

curl -s -c /tmp/comfyfleet.cookies -H 'Content-Type: application/json' \
  -d '{"password":"'"$COMFYFLEET_PASSWORD"'"}' \
  http://127.0.0.1:9100/api/login

curl -s -b /tmp/comfyfleet.cookies http://127.0.0.1:9100/api/gpus
curl -s -b /tmp/comfyfleet.cookies http://127.0.0.1:9100/api/instances

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  http://127.0.0.1:9100/api/instances

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -F "workflow=@./examples/workflow.example.json;type=application/json" \
  -F "gpu=0" \
  http://127.0.0.1:9100/api/instances

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"workflow_path":"/home/me/flows/portrait.json","gpus":"0","start":false}' \
  http://127.0.0.1:9100/api/instances

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/instances/portrait/start
curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/instances/portrait/stop

curl -s -b /tmp/comfyfleet.cookies -X POST http://127.0.0.1:9100/api/logout
```

`examples/workflow.example.json` is documentation. The API will not use it unless the operator uploads it or passes it as `workflow_path`.
