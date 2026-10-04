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

Delete is `POST /api/instances/{name}/delete`. It removes that container and the fleet record. Host mounts, including `custom_nodes` and workflow files, stay. There is no `DELETE` method and no purge flag. `DELETE /api/instances/{name}/delete` is **405** and does not remove the container. There is no HTTP restart route; stop and start, or use `comfyfleet restart` on the CLI. Start and stop do not rebuild the image.

## Endpoints

| Method | Path | Auth | Calls | Success body |
|---|---|---|---|---|
| `GET` | `/api/health` | no | nothing in control | Liveness. No fleet data |
| `POST` | `/api/login` | no | nothing in control | `{"ok": true}` and `Set-Cookie` |
| `POST` | `/api/logout` | no | nothing in control | `{"ok": true}` and a cleared cookie |
| `POST` | `/api/host/fix-owner` | yes | `comfyfleet.ownership.fix_owner` | Allowlisted chown result |
| `POST` | `/api/host/prune-dangling` | yes | `comfyfleet.prune.prune_dangling_containers` | Removed ids and kept fleet ids |
| `GET` | `/api/gpus` | yes | `comfyfleet.gpu.detect_gpus` | GPUs the create form can offer |
| `GET` | `/api/instances` | yes | `list_instances` | Every known instance |
| `POST` | `/api/instances` | yes | `create_instance` | The instance just created |
| `POST` | `/api/instances/{name}/start` | yes | `start_instance` | That instance, running |
| `POST` | `/api/instances/{name}/stop` | yes | `stop_instance` | That instance, not running |
| `POST` | `/api/instances/{name}/force-stop` | yes | `force_stop_instance` | Hard stop (`docker kill`) of that instance only |
| `POST` | `/api/instances/{name}/delete` | yes | `delete_instance` | Container removed and fleet record dropped |
| `GET` | `/api/instances/{name}/terminal` | yes | websocket `docker exec` | Shell proxy. Upgrade required. The Docker socket is not sent to the browser |
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
      "launch": {
        "vram": "--lowvram",
        "attention": "",
        "flags": ["--disable-dynamic-vram"],
        "reserve_vram": null,
        "vram_headroom": null,
        "preview_method": "",
        "preview_size": null,
        "extra_args": "",
        "argv": ["--lowvram", "--disable-dynamic-vram"]
      }
    }
  ]
}
```

An empty fleet is `{"ok": true, "instances": []}`. That is not an error.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Instance name. |
| `status` | string | Docker status: `running`, `created`, `exited`, `missing`, or another Docker state. Treat **only** `running` as running. `created` means the container exists and has not been started. |
| `port` | number | Host port reserved for this instance (from 8188 up). Present even when stopped. The fleet page opens Comfy at `<page-scheme>//<window.location.hostname>:<port>/`. |
| `gpus` | number[] | GPU indexes chosen at create. |
| `image` | string | Image ref used at create. |
| `cuda_tag` | string | `cu130` or `cu124` when the line is known. Empty when the ref does not name a line. |
| `launch` | object | Flags chosen at create. `argv` is what is appended after `--listen 0.0.0.0 --port 8188`. Empty `argv` means stock ComfyUI. |

Every create runs `docker create --shm-size 8g` (Compose `shm_size: '8g'`) before the image name, on both CUDA lines. The list shows `cuda_tag` so the UI can label the line. `start`, `stop`, `restart`, and `POST .../launch` do not change `image` or `cuda_tag`.

The API does not return an Open URL. `COMFYFLEET_PUBLIC_HOST` is not used for Open Comfy. The browser builds the link from the host that loaded this manager and the published `port`, with a trailing slash. A phone that opened the manager at `http://192.168.1.20:9100/` opens Comfy at `http://192.168.1.20:8188/`. The shell **Open** button stays on `/terminal.html`. Stop does not destroy the container or the workflow file.

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
| `cuda_tag` | no | `cu130` or `cu124`. `cu130` needs a host driver that supports CUDA 13.0. `cu124` needs CUDA 12.4. Omitted uses `COMFYFLEET_CUDA_TAG`, or `cu130` when that is unset. A wrong line can fail when the instance starts. On `--force` / `force: true`, omitting it keeps a different stored line instead of swapping because the install default changed. The same line follows a new `COMFYFLEET_INSTANCE_IMAGE` digest. |
| `instance_image` | no | Full image ref override. The tag must already be on the host engine. When it names `cu130` or `cu124`, it has to agree with `cuda_tag`. |
| `vram` | no | One of `lowvram`, `novram`, `highvram`. Omit for stock VRAM. Not combinable with `--cpu` or `--gpu-only`. |
| `attention` | no | One attention backend, for example `use-sage-attention`. |
| `flags` | no | Comma-separated toggles, or a JSON array of strings: `disable-smart-memory`, `disable-dynamic-vram`, `force-fp16`, `cuda-malloc`, and the other panel flags. Mutually exclusive pairs are rejected. |
| `reserve_vram` | no | GB passed to `--reserve-vram`. Omit to leave ComfyUI's default. |
| `vram_headroom` | no | GB passed to `--vram-headroom`. |
| `preview_method` | no | `auto`, `latent2rgb`, `taesd`, or `none`. |
| `preview_size` | no | Positive integer for `--preview-size`. |
| `extra_args` | no | Free-text `main.py` arguments for flags that are not in the panel. Appended last. `--listen` and `--port` are stripped. |
| `comfy_extra_args` | no | Same channel as `extra_args`. A string or a JSON array of strings. Appended after `extra_args` when both are set. `--listen` and `--port` are stripped. Blank is ignored. |
| `custom_node_git_urls` | no | HTTPS or SSH git URLs cloned into this instance's `custom_nodes` volume. JSON array, a newline- or comma-separated string, or repeated form fields. Blank entries are ignored. |
| `custom_nodes_zip` | no | Repeatable multipart file. Each archive is extracted into the same `custom_nodes` volume. Absent or empty is a no-op. One file is the same path as before. |
| `custom_nodes_zip_name` | no | Repeatable text field, in the same order as `custom_nodes_zip`. The folder name for that archive. Blank uses `[project].name` from that zip's `pyproject.toml`. A typed name wins. |
| `install_missing_from_workflow` | no | Default **true** when the field is omitted. Install custom nodes referenced by this workflow that are not already present. `false` skips that step. |

`start` and `force` accept JSON booleans and the strings `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`.

Changing `cu130` versus `cu124` on an instance that already exists requires this create call with `force: true` (the container must be stopped). `POST /api/instances/{name}/launch` recreates Comfy arguments only and keeps the CUDA line. `start` and `restart` do not swap it either. Every create still passes `--shm-size 8g`.

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
  "warnings": [],
  "instance": {
    "name": "portrait",
    "status": "created",
    "port": 8188,
    "gpus": [0]
  }
}
```

`warning` is a string when control would have printed a concurrency warning, otherwise `null`. `warnings` is an array of strings. It is empty when optional custom-node steps were skipped or all succeeded. A failed clone, zip extract, or missing-node install appends a message here and create still returns **200**. Those failures are not HTTP 5xx. `started` is true only when this call left the container running because `start` was true. `status` is read back from Docker after control returns. The instance object includes `launch`, same as `GET /api/instances`. `argv` is appended after `--listen 0.0.0.0 --port 8188` inside the container. Listen stays `0.0.0.0`.

#### Optional custom nodes

Blank or absent `custom_node_git_urls` and `custom_nodes_zip` do nothing. Create still requires a workflow. It does not fail because those fields were empty. Nothing is baked into a new instance image. Files land on the host mount `/home/ComfyFleet/custom_nodes_<name>` (container `/opt/ComfyUI/custom_nodes`).

Allowed git schemes are `https://`, `ssh://`, and scp-style `git@host:path`. Other schemes (`http://`, `file://`, `git://`) are skipped and listed in `warnings`. Each accepted URL is `git clone --depth 1` into its own subdirectory (the repository name, without `.git`). `git` runs on the manager, with no shell. The clone timeout is **120** seconds (`COMFYFLEET_GIT_CLONE_TIMEOUT`). `GIT_TERMINAL_PROMPT=0` and SSH `BatchMode` are set so a credential prompt cannot hang the request.

`custom_nodes_zip` is a multipart file, repeated once per archive. Each archive is checked on its own: a rejected archive writes nothing from that zip, and the others are still extracted. An archive is rejected when any member is absolute, contains `..`, contains NUL, is a symlink, or is encrypted. A malformed zip is a warning. Extraction is in-process (the manager image also includes `unzip` for operators).

A matching `custom_nodes_zip_name` sets that pack's directory under `custom_nodes`. Leave it blank to use `[project].name` from `pyproject.toml` in the zip. That is the pack id. A Comfy registry zip does not put this id in JSON (`node_list.json` lists renamed node classes). `[tool.comfy].DisplayName` is a label and is not the folder. A name you type wins. One shared top directory inside the zip (a GitHub archive's `Repo-main/` wrapper) is removed so the pack's files sit in that folder. With no typed name and no project name, members keep the paths stored in the zip.

`install_missing_from_workflow` defaults to **true**. It reads only the workflow JSON from this create. A node is a candidate when it has an embedded git URL or `aux_id` (`owner/repo`, installed as `https://github.com/owner/repo`), or when its class name is in `COMFYFLEET_EXTENSION_NODE_MAP` (a JSON file of class name → git URL, or Manager's extension-node-map object). Class names the workflow does not reference are not installed. Nodes already present are skipped: a `NODE_CLASS_MAPPINGS` entry on the volume, a pack directory already in that volume, or a baked pack (`ComfyUI-Manager`, `ComfyUI-Pixaroma`, `ComfyUI-ComfyDock`, `RES4LYF`, `comfyfleet_default_workflow`). Core nodes with no git URL and no map entry are left alone. This does not install the Manager registry.

When at least one URL is missing, create starts the instance if it is stopped, waits up to **180** seconds for ComfyUI (`COMFYFLEET_NODE_READY_TIMEOUT`), and posts each URL to `POST http://127.0.0.1:8188/customnode/install/git_url` **inside** the instance (`docker exec`), where `COMFYFLEET_TRUSTED_INSTALL=1`. Each install times out after **180** seconds (`COMFYFLEET_NODE_INSTALL_TIMEOUT`). A successful install restarts the instance so Comfy registers the new nodes. If `start` was false, the instance is then stopped. The container and the volume remain. Git clones and zip extracts that happen before the first start are loaded on that first start; they do not by themselves restart a container that was left stopped.

The manager image includes `git` for these clones. A process without `git` still creates the instance and records a warning.

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
    "gpus": [0]
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
    "gpus": [0]
  }
}
```

Stopping an already stopped instance returns 200. The container, mounts, and workflow file stay.

### `POST /api/instances/{name}/force-stop`

`docker kill` on that instance container (SIGKILL). It does not remove the container or the fleet record. A container that is already stopped returns 200 and is left as it is.

### `POST /api/instances/{name}/delete`

Force-stops the container if it is running, removes **only** that container, and deletes `comfyfleet.json`. Host workflow, input, output, and custom-node files are kept (`/home/ComfyFleet/custom_nodes_<name>` and `/home/ComfyFleet/files/<name>` stay). The UI asks for confirmation before calling this. `DELETE` on this path is **405** and does not remove the container. There is no second delete API and no purge of host mounts.

```json
{"ok": true, "deleted": "portrait"}
```

### `POST /api/instances/{name}/launch`

JSON body with the same launch fields as create (`vram`, `attention`, `flags`, `reserve_vram`, `vram_headroom`, `preview_method`, `preview_size`, `extra_args`). Stops the instance if it is running, removes that container, and creates it again with the same name, host port, GPU set, image, CUDA line, workflow file, and mount paths. Only the arguments after `--listen 0.0.0.0 --port 8188` change. `cuda_tag` in this body is ignored; changing the line is `POST /api/instances` with `force: true`. If it was running, it is started again. The previous container is removed before the replacement is created. `--shm-size 8g` is set again.

### `GET /api/instances/{name}/terminal`

WebSocket upgrade. The manager runs `docker exec -it <name> /bin/bash` and copies bytes to the socket. The instance must already be running and must be a fleet record. The browser does not receive the Docker socket and cannot choose the command. A GET without `Upgrade: websocket` is **400**.

Open in the fleet UI loads `/terminal.html?name=<name>`. Open Comfy is a separate link built in the browser from `location.hostname`, `location.protocol`, and the published `port`.

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

### `POST /api/host/fix-owner`

Session cookie or `Authorization: Bearer`. There is no path argument. The route calls the same helper as `comfyfleet fix-owner`.

An empty body chowns to `comfyuser:comfyuser`. A JSON object may set `user` and `group`. A blank or omitted field uses `comfyuser` for that name. When either name is missing it creates the account with `groupadd` and `useradd` (`--no-create-home`, home directory `/home/ComfyFleet`, so it does not add `/home/<name>`). It then recursively chowns only:

| Path | Rule |
|---|---|
| `/home/ComfyFleet/wildcards` | recurse when the directory exists |
| `/home/ComfyFleet/models` | recurse when the directory exists |
| `/home/ComfyFleet/custom_nodes_*` | recurse each matching directory |
| `/home/ComfyFleet/files` | recurse when the directory exists |

A path outside that allowlist is refused, including `..` and a symlink whose target leaves the allowlist. A symlink that resolves to `/opt/comfyfleet/baked_custom_nodes` or a path under it is the instance entrypoint's link to an image directory (`ComfyUI-Manager` and the other baked custom nodes). That link does not fail the walk. The symlink inode on the host volume is chowned to the requested user and group, and the image directory is not entered, so the baked files are not chowned. A symlink that resolves anywhere else outside the allowlist still refuses the run. An unauthenticated call is **401** and does not chown anything.

```json
{
  "ok": true,
  "user": "comfyuser",
  "group": "comfyuser",
  "uid": 1001,
  "gid": 1001,
  "paths": ["/home/ComfyFleet/wildcards", "/home/ComfyFleet/models", "/home/ComfyFleet/files"]
}
```

`paths` lists the allowlisted directories that were present. A failure to create the requested user or group is **400**. The error names that host user or group and the `useradd` or `groupadd` failure. An invalid account name is **400** and does not chown anything.

### `POST /api/host/prune-dangling`

Session cookie or `Authorization: Bearer`. Removes stopped containers that are not ComfyFleet instances (`exited`, `created`, or `dead`, the set `docker container prune` removes). A container labeled `comfyfleet.managed=true` is not removed, whether it is running or stopped. Running containers that are not fleet instances are not dangling and are left alone. The implementation removes by id with `docker rm` (not `docker rm -f`) and never passes a managed id.

```json
{
  "ok": true,
  "removed": ["abc123abc123"],
  "kept_managed": ["def456def456"]
}
```

The Host menu sends this after a confirm dialog. It does not delete a fleet instance.

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
  -F "custom_node_git_urls=https://github.com/ltdrdata/ComfyUI-Impact-Pack" \
  -F "custom_node_git_urls=ssh://git@github.com/example/Another-Node.git" \
  -F "custom_nodes_zip=@./nodes.zip;type=application/zip" \
  -F "custom_nodes_zip_name=" \
  -F "custom_nodes_zip=@./other.zip;type=application/zip" \
  -F "custom_nodes_zip_name=OtherPack" \
  -F "install_missing_from_workflow=true" \
  -F "comfy_extra_args=--mmap-torch-files" \
  http://127.0.0.1:9100/api/instances

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/instances/portrait/delete

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"workflow_path":"/home/me/flows/portrait.json","gpus":"0","start":false}' \
  http://127.0.0.1:9100/api/instances

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/instances/portrait/start
curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/instances/portrait/stop

curl -s -b /tmp/comfyfleet.cookies -X POST http://127.0.0.1:9100/api/logout

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/host/fix-owner
curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"user":"comfyuser","group":"comfyuser"}' \
  -X POST http://127.0.0.1:9100/api/host/fix-owner

curl -s -H "Authorization: Bearer $COMFYFLEET_PASSWORD" \
  -X POST http://127.0.0.1:9100/api/host/prune-dangling
```

`examples/workflow.example.json` is documentation. The API will not use it unless the operator uploads it or passes it as `workflow_path`.
