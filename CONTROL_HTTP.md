# ComfyFleet control HTTP API

Phase 2 contract for the control web UI. The server is a thin adapter over `comfyfleet.control` and the `comfyfleet.gpu` probe. It does not implement Docker lifecycle, mounts, port assignment, or GPU selection itself. It does not implement login.

The iOS-like control UI is in `ui/` (`index.html`, `app.css`, `app.js`, `comfyfleet-logo-ships.jpg`). This process serves it at `GET /`. The pages call the routes below and do not implement Docker lifecycle.

## Serve

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

Startup prints a trusted-LAN warning. Phase 1 commands (`create`, `start`, `stop`, `restart`, `list`) stay available and do not need this server.

## Trusted LAN, same origin, no Auth

Auth is Phase 3. `comfyfleet.control.authorize()` is still a no-op stub: it checks that the action name is one of `create`, `start`, `stop`, `restart`, or `list`, then returns. This server does not read cookies, tokens, or an `Authorization` header. A successful response is not a login.

Do not expose port 9100 to the public internet. The default bind is every interface so a phone on the LAN can open the UI. That is only safe on a network you trust.

The UI and the API should be same-origin. Load the UI from `http://<host>:9100/` and call `/api/...` on that origin. This server does not send `Access-Control-Allow-Origin`. A page on another origin will not be able to call the API from a browser.

There is no delete route. Phase 1 control has no destroy API. There is no HTTP restart route; stop and start, or use `comfyfleet restart` on the CLI. Start and stop do not rebuild the image.

## Endpoints

| Method | Path | Calls | Success body |
|---|---|---|---|
| `GET` | `/api/health` | nothing in control | Liveness plus the Phase 3 Auth note |
| `GET` | `/api/gpus` | `comfyfleet.gpu.detect_gpus` | GPUs the create form can offer |
| `GET` | `/api/instances` | `list_instances` | Every known instance |
| `POST` | `/api/instances` | `create_instance` | The instance just created |
| `POST` | `/api/instances/{name}/start` | `start_instance` | That instance, running |
| `POST` | `/api/instances/{name}/stop` | `stop_instance` | That instance, not running |
| `GET` | `/` and other non-API paths | static files under `ui/` | Control UI assets. If `ui/` is missing, `/` is a short placeholder |

`{name}` is the instance name from create (the sanitized workflow filename stem). It is URL-safe: lowercase `[a-z0-9_-]`, at most 63 characters.

Query strings are ignored. Send create options in the body so a workflow path is not written into the request line.

### `GET /api/health`

```json
{
  "ok": true,
  "service": "comfyfleet",
  "version": "0.1.0",
  "phase": 2,
  "auth": "phase3-stub",
  "note": "Auth is a Phase 3 stub. authorize() is a no-op and this server does not check a login or token. Serve it only on a trusted LAN; it is not an internet-exposed auth product."
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
| `port` | number | Host port reserved for this instance (Phase 1 allocation, from 8188 up). Present even when stopped. |
| `gpus` | number[] | GPU indexes chosen at create. |
| `url` | string or null | Open target. A string **only while `status` is `running`**. Otherwise `null`. |

`url` is `http://<request-host>:<port>`. `<request-host>` is the `Host` header the browser used to reach this control server, with the control port removed. A phone that opened `http://192.168.1.20:9100/` gets `http://192.168.1.20:8188` for a running instance. Open that URL in a new tab. When `url` is null, Open is disabled. Stop does not destroy the container or the workflow file.

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
| 404 | Unknown path. |
| 405 | Wrong method on a known path. |
| 413 | Body larger than 32 MiB. |
| 415 | Unsupported `Content-Type` on create. |
| 503 | `GET /api/gpus` and the GPU probe failed. |
| 500 | Unexpected server error. `error` is `internal error`. |

Unknown instances are **400** with control's "no instance named …" text, not 404.

## Examples

```bash
curl -s http://127.0.0.1:9100/api/health
curl -s http://127.0.0.1:9100/api/gpus
curl -s http://127.0.0.1:9100/api/instances

curl -s -F "workflow=@./examples/workflow.example.json;type=application/json" \
  -F "gpu=0" \
  http://127.0.0.1:9100/api/instances

curl -s -H 'Content-Type: application/json' \
  -d '{"workflow_path":"/home/me/flows/portrait.json","gpus":"0","start":false}' \
  http://127.0.0.1:9100/api/instances

curl -s -X POST http://127.0.0.1:9100/api/instances/portrait/start
curl -s -X POST http://127.0.0.1:9100/api/instances/portrait/stop
```

`examples/workflow.example.json` is documentation. The API will not use it unless the operator uploads it or passes it as `workflow_path`.
