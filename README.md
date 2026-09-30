![ComfyFleet](comfyfleet-logo-ships.jpg)

# ComfyFleet

ComfyFleet runs **many** named ComfyUI workflow containers on one GPU host and **starts only a few** of them. Phase 1 is a local/LAN Docker fleet: a CUDA 12.4 image and a host CLI (`comfyfleet`) for create, start, stop, and list. Phase 2 adds a same-origin control HTTP API and an iOS-like web UI on that host. The pages call `/api/...` only. Docker lifecycle stays in `comfyfleet.control`.

It is not a cloud service and not an account system. **Auth is Phase 3 and is not implemented.** `authorize()` is a no-op stub. The control server assumes a **trusted LAN**. Do not expose it to the public internet.

The locked requirements are in [SPEC.md](SPEC.md) (Phase 1) and [SPEC_PHASE2.md](SPEC_PHASE2.md) (Phase 2). The HTTP contract is in [CONTROL_HTTP.md](CONTROL_HTTP.md).

## What you need on the host

- Linux with Docker.
- A working NVIDIA driver. `nvidia-smi` must succeed. Phase 1 does not treat a CPU-only start as success.
- The [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), so `docker create --gpus device=N` works.
- Permission to create directories under `/home` (`/home/models`, `/home/custom_nodes_<name>`, `/home/files/<name>/...`).

Those `/home/...` paths are fixed in Phase 1.

## Build the image

```bash
docker build -t comfyfleet:phase1 .
pip install .
```

`pip install .` installs the host CLI only. It does not build the image. The image does not contain the CLI.

### PyTorch and CUDA 12.4

The image is **Debian bookworm-slim** plus the NVIDIA **CUDA 12.4 runtime** (not the devel toolkit, not a desktop stack):

| Package | Pin |
|---|---|
| `cuda-libraries-12-4` | `12.4.1-1` |
| `cuda-cudart-12-4` | `12.4.127-1` |
| `libcudnn9-cuda-12` | `9.1.0.70-1` (cuDNN runtime for CUDA 12.4) |

Repository: `https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/` (keyring `cuda-keyring_1.1-1_all.deb`).

PyTorch is **not** the PyPI default (that wheel is CUDA 13). The image installs the CUDA 12.4 wheels from:

`https://download.pytorch.org/whl/cu124`

| Wheel | Exact spec |
|---|---|
| torch | `torch==2.6.0+cu124` (`torch-2.6.0+cu124-cp311-cp311-linux_x86_64.whl`) |
| torchvision | `torchvision==0.21.0+cu124` (`torchvision-0.21.0+cu124-cp311-cp311-linux_x86_64.whl`) |

The image Python is Debian bookworm CPython 3.11, which matches those `cp311` wheels. A constraints file at `/opt/comfyfleet/torch-constraints.txt` keeps later `pip install -r requirements.txt` from replacing them with a newer CUDA build.

`comfy-kitchen==0.2.36` is installed from the **pure-Python** wheel so the eager backend runs on CUDA 12.4. The manylinux wheel of that package targets CUDA 13.

### Baked custom nodes

Cloned in full and checked out at these commits (also in `docker/PINS.txt`):

| Component | Upstream | Pin |
|---|---|---|
| ComfyUI | `https://github.com/Comfy-Org/ComfyUI` | tag `v0.38.0`, commit `6b747c0428c343e1417219641db93a4fb7cb69ae` |
| ComfyUI-Manager | `https://github.com/Comfy-Org/ComfyUI-Manager` | `14b5aaab711ad1f1306d420732a923fb058c44d7` |
| pixaroma (full repo) | `https://github.com/pixaroma/ComfyUI-Pixaroma` | tag `v1.4.181`, commit `9259bc49557a92e3fc14796999468c723bd1ecdd` |
| ComfyUI-ComfyDock | `https://github.com/RecognizeYourPrivilege/ComfyUI-ComfyDock` | `3a9ff9eba897bf2388d6c1943b01d819ba05a0c6` |

`git` is installed in the image so Manager and in-container updates can use it. The pixaroma checkout is the whole repository, including that pack's own workflow-browser examples. Those files are **not** the instance default and are **not** used when `--workflow` is omitted.

## Create, start, stop, list

Create **requires** `--workflow`. There is no stock, QualitySafe, or sample workflow inside the image, and create fails if the file is missing, unreadable, or not a JSON object.

`examples/workflow.example.json` shows the shape of a UI-format workflow. It is documentation only. `.dockerignore` excludes `examples/`, and the Dockerfile does not copy it.

```bash
comfyfleet create --workflow ./examples/workflow.example.json --gpu 0
comfyfleet list
comfyfleet start workflow_example
comfyfleet stop workflow_example
```

The name `workflow_example` is the sanitized stem of `workflow.example.json`. Create does **not** start the container. Pass `--start` only when you want that one instance started immediately.

```bash
comfyfleet create --workflow ~/flows/portrait.json --gpus 0
comfyfleet create --workflow ~/flows/background.json --gpus 1
comfyfleet start portrait
comfyfleet stop portrait
comfyfleet start background
comfyfleet restart background
```

`start`, `stop`, and `restart` do not rebuild the image.

On a host with more than one GPU, create asks which GPU or GPUs to attach. A single-GPU host still asks for a yes/no confirmation. Non-interactive create must pass `--gpu 0` or `--gpus 0,1` (or `--gpus all`). `--gpu` and `--gpus` together are rejected.

`list` prints the instance name, Docker status, host port, `http://0.0.0.0:<port>`, GPU list, and workflow path. From another machine on the LAN, open `http://<host-ip>:<port>`. ComfyUI inside the container is started with `--listen 0.0.0.0` and container port `8188`.

## Naming

The container name is the workflow filename stem, sanitized:

1. Lowercase.
2. Characters outside `[a-z0-9_-]` become `_`.
3. Repeated underscores collapse. Leading and trailing `_` and `-` are removed.
4. An empty result, or one that does not start with a letter or digit, is prefixed with `wf`.
5. Names longer than 63 characters are truncated and given a stable suffix: `-` plus the first 8 hex digits of SHA-256 of the original stem (UTF-8).

`My Flow.json` becomes `my_flow`. A second create that sanitizes to an existing name is refused. `--force` replaces a **stopped** instance (this is also how you change the GPU set). `--force` refuses while the container is running.

## Mounts

| Host | Container |
|---|---|
| `/home/models` | `/opt/ComfyUI/models` (shared, read-write) |
| `/home/custom_nodes_<name>` | `/opt/ComfyUI/custom_nodes` |
| `/home/files/<name>/input` | `/opt/ComfyUI/input` |
| `/home/files/<name>/output` | `/opt/ComfyUI/output` |
| `/home/files/<name>/temp` | `/opt/ComfyUI/temp` |
| `/home/files/<name>` | `/opt/comfyfleet/instance` |

Create creates any of those directories that are missing, including the usual ComfyUI model subfolders under `/home/models`.

`/home/models` is shared by every instance and is read-write. Two running instances that write the same model file can corrupt it.

The host `custom_nodes` directory hides the image's `custom_nodes` folder. On every start the entrypoint symlinks the baked nodes into that mount if those names are not already present. The links point at `/opt/comfyfleet/baked_custom_nodes/...` inside the container, so on the host they look dangling; ComfyUI follows them in the container.

- `ComfyUI-Manager`
- `ComfyUI-Pixaroma`
- `ComfyUI-ComfyDock`
- `comfyfleet_default_workflow` (loader only; it is not a workflow)

A real directory you place at one of those names is left alone.

## Workflow load path

1. `comfyfleet create --workflow <file>` copies that file to `/home/files/<name>/default_workflow.json`.
2. Instance metadata is written to `/home/files/<name>/comfyfleet.json` (name, port, GPUs, image, workflow paths).
3. The whole `/home/files/<name>` directory is bind-mounted at `/opt/comfyfleet/instance`, so the workflow stays editable on the host. Edit that file in place (a rename can break a bind mount; this mount is the directory, so replacing the file inside it is fine).
4. Every container start runs `docker/entrypoint.sh`, which refuses to exec ComfyUI if that JSON object is missing or invalid. It then writes a new boot id to `/tmp/comfyfleet-boot-id`.
5. ComfyUI is executed as `python main.py --listen 0.0.0.0 --port 8188`.
6. The baked loader node serves `GET /comfyfleet/default-workflow` and `GET /comfyfleet/boot`. Its frontend extension calls `app.loadGraphData` (or `app.loadApiJson` for API-format graphs) after the UI comes up. A browser tab loads the file once per boot id and file mtime, so a container restart or a host-side edit shows up on the next page load. If the fetch fails, the loader does not substitute another workflow.

GPU changes are a recreate: `comfyfleet stop <name>` then `comfyfleet create --workflow ... --force --gpus ...`. There is no in-place GPU edit in Phase 1.

## Ports and GPUs

The first instance records host port **8188**. The next free port is 8189, then 8190, and so on. Ports recorded by other instances are reserved even while those containers are stopped, so two created instances do not both claim 8188.

On start, ComfyFleet keeps the recorded port when it is free. If something else has taken it, the container is recreated (same image, no rebuild) on the next free port and the metadata is updated.

`create` and `start` abort when `nvidia-smi` is missing, fails, or lists no GPU. `stop` and `list` do not need a GPU.

Starting an instance that would run more containers than the host has GPUs prints a warning and continues. Set `COMFYFLEET_MAX_CONCURRENT` to a positive integer to hard-stop instead. The default does not hard-block.

Containers are created with `--restart no`, so a created-but-never-started instance stays stopped across a Docker daemon restart. `start` sets `--restart unless-stopped` for that container. After `stop`, Docker leaves it stopped.

## Updates

Phase 1 has no zero-downtime or rolling update.

**Rebuild (reproducible):**

```bash
docker build -t comfyfleet:phase1 .
comfyfleet stop <name>
comfyfleet create --workflow /home/files/<name>/default_workflow.json --force --gpu 0
comfyfleet start <name>
```

**In-container git (not pinned after you move HEAD):**

```bash
docker exec -it <name> bash
git -C /opt/ComfyUI fetch origin
git -C /opt/ComfyUI checkout master
git -C /opt/ComfyUI pull --ff-only
/opt/venv/bin/pip install -r /opt/ComfyUI/requirements.txt -c /opt/comfyfleet/torch-constraints.txt
git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager checkout main
git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager pull --ff-only
git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma checkout main
git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma pull --ff-only
git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock checkout main
git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock pull --ff-only
exit
comfyfleet restart <name>
```

The image checkouts start detached at the pins above, so a bare `git pull` will not move them until you check out a branch. Keep the torch constraints file in the pip command so a pull does not replace CUDA 12.4 torch with the PyPI CUDA 13 wheel. Restart the container afterward. Do not expect the old process to keep serving during the pull.

## Control HTTP API (Phase 2)

`comfyfleet ui` (alias `comfyfleet serve`) serves a JSON API that only calls `comfyfleet.control`, plus `comfyfleet.gpu.detect_gpus` for the create form. Default bind is `0.0.0.0:9100`. Primary URL: `http://<host>:9100/`.

```bash
comfyfleet ui
comfyfleet ui --host 127.0.0.1 --port 9100
```

`0.0.0.0` is so a phone on the LAN can open the UI. Use `--host 127.0.0.1` to keep it on this machine. There is no login. Do not put this port on the public internet.

The UI and the API are same-origin. This server does not send CORS headers. `comfyfleet ui` serves the iOS-like pages in `ui/` (dark glass, fleet logo, large touch targets) at `http://<host>:9100/`. From a phone on the LAN, open that URL, upload a workflow JSON, pick GPUs, then create, start, stop, or open a running instance. Open uses the `url` field, which is set only while `status` is `running`. There is no baked default workflow. A failed GPU probe is shown as an error; the page does not invent a GPU.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/health` | `ok`, and a note that Auth is a Phase 3 stub |
| `GET` | `/api/gpus` | Detected GPUs (`index`, `name`, `memory`) |
| `GET` | `/api/instances` | `name`, `status`, `port`, `gpus`, and `url` when `status` is `running` |
| `POST` | `/api/instances` | Create. Requires an uploaded workflow JSON or `workflow_path`. Requires `gpu` or `gpus`. `start` defaults to false. `force` defaults to false. No baked workflow. |
| `POST` | `/api/instances/{name}/start` | Start without rebuilding the image |
| `POST` | `/api/instances/{name}/stop` | Stop without destroying the container |

Field names, error status codes, and the open-URL rule are in [CONTROL_HTTP.md](CONTROL_HTTP.md).

Phase 1 mount, port, and GPU behavior is unchanged. Create still copies the operator workflow to `/home/files/<name>/default_workflow.json` and still refuses a missing or invalid file.

## Phase 3

**Auth is not in this tree.** No login, token, or multi-user gate. `comfyfleet.control.authorize` is the hook a later Auth layer should wrap. Create, start, stop, restart, and list go through `comfyfleet.control` so mounts and workflow copy do not need a second implementation.

ComfyUI-ComfyDock is baked so the stock ComfyUI page is usable on a phone. It is not the fleet control app. The fleet control UI is the Phase 2 web UI in `ui/`, served from this API's origin.

## Tests

```bash
python -m unittest discover -s tests
```

The tests cover naming, port reservation, workflow rejection, `nvidia-smi` failures, GPU prompts, create-without-start, collision, start/stop without an image rebuild, and the Phase 2 HTTP adapter (happy path and a missing workflow). `tests/test_ui.py` checks that the pages call that API and do not implement Docker themselves. They do not build the CUDA image and they do not need a GPU.
