![ComfyFleet](comfyfleet-logo-ships.jpg)

# ComfyFleet

ComfyFleet is a **manager container**. You run that image on a Docker host with NVIDIA GPUs. As soon as the container is up it serves a control web UI. From the UI you create **many** named ComfyUI workflow containers and **start a few** of them so the GPUs stay usable.

Create, start, and stop run **inside the manager**. The manager talks to the host Docker engine through the mounted Docker socket and starts **sibling** ComfyUI containers. There is no Docker daemon inside the manager.

The manager requires **`COMFYFLEET_PASSWORD`**. If that variable is missing or empty, the manager **refuses to start**. There is no open-LAN fallback. Sign in once in the browser, or send `Authorization: Bearer` with the same value. A trusted LAN is still recommended. This password is a gate, not a full internet-hardening product: terminate TLS at a reverse proxy if you need HTTPS. Do not publish port **9100** or the ComfyUI ports (8188 and up) on the public internet. ComfyUI on 8188 and up is not behind this login.

The Docker socket you mount into the manager is **root-equivalent on the host**. A process that can use it can start privileged containers and mount host paths. Run the manager only on a machine you trust.

Requirements and contracts: [SPEC.md](SPEC.md), [SPEC_PHASE2.md](SPEC_PHASE2.md). HTTP contract: [CONTROL_HTTP.md](CONTROL_HTTP.md).

## What the host needs

- Linux with Docker.
- A working NVIDIA driver. `nvidia-smi` must succeed on the host. A CPU-only start is a failure.
- The [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), so `docker create --gpus device=N` works and so the manager can see host GPUs.
- Permission to create directories under `/home` (`/home/models`, `/home/custom_nodes_<name>`, `/home/files/<name>/...`).
- Disk space to pull the images. A normal install does not compile torch on the host.

Those `/home/...` paths are the default contract.

The images are `linux/amd64` Debian bookworm-slim. The host can be Arch. The host does not need Debian, a CUDA toolkit, or a local image rebuild.

## Run the manager

Pull the prebuilt images and start the manager. That is the normal install. A local image rebuild is optional and is documented under Development.

Two images. [`.github/workflows/publish-images.yml`](.github/workflows/publish-images.yml) is what publishes them to GHCR from `main`:

| Image | Pull | Also tagged locally by `install.sh` | Role |
|---|---|---|---|
| Manager | `ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest` | `comfyfleet-manager:latest` | Control HTTP API and web UI. No CUDA stack. |
| Instance | `ghcr.io/recognizeyourprivilege/comfyfleet:phase1` | `comfyfleet:phase1` | ComfyUI container the manager creates. CUDA 12.4. |

Each successful publish also tags the git commit SHA (`ghcr.io/recognizeyourprivilege/comfyfleet:<sha>` and `ghcr.io/recognizeyourprivilege/comfyfleet-manager:<sha>`). `ghcr.io/recognizeyourprivilege/comfyfleet:latest` is the same instance build as `:phase1`.

Digests are not pinned in this file yet. After a successful publish, the workflow job summary prints:

```text
instance ghcr.io/recognizeyourprivilege/comfyfleet@sha256:<digest>
manager ghcr.io/recognizeyourprivilege/comfyfleet-manager@sha256:<digest>
```

Copy those lines into this section when they exist. Until then, installs follow the tags above. To pin a later install:

```bash
export COMFYFLEET_INSTANCE_DIGEST=sha256:<instance-digest>
export COMFYFLEET_MANAGER_DIGEST=sha256:<manager-digest>
```

Until that Actions run has succeeded on `main` and a maintainer has made both GHCR packages public, `docker pull` fails. This tree does not claim the images are already pullable. What a human still has to do is listed under Development.

The instance tag has to exist in the **host** engine before create, because sibling containers are started by that engine. `install.sh` pulls it and sets `COMFYFLEET_INSTANCE_IMAGE` to that ref. The manager's own default, when that variable is unset, remains `comfyfleet:phase1`. The script also applies that local tag, so a manager started without the variable still finds the image.

### Install script

From a checkout of this repo, replace `192.168.1.20` with the address browsers on your LAN use:

```bash
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
./install.sh
```

Without a checkout:

```bash
curl -fsSL https://raw.githubusercontent.com/RecognizeYourPrivilege/ComfyFleet/main/install.sh \
  | COMFYFLEET_PASSWORD=replace-with-a-long-secret COMFYFLEET_PUBLIC_HOST=192.168.1.20 bash
```

Compose uses the same mounts and environment. The script still pulls the instance image, which is not a compose service:

```bash
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
./install.sh --compose
```

`replace-with-a-long-secret` is a placeholder. Pick a long password and do not commit it. Changing the password means recreating or restarting the manager with the new value. Sessions live in process memory and end on that restart.

Open `http://192.168.1.20:9100/`. The browser shows a sign-in page until the password is accepted.

The script pulls both images, tags the local names above, removes an existing container named `comfyfleet-manager`, and starts the manager with `--gpus all`, `-p 9100:9100`, the Docker socket, `/home`, `COMFYFLEET_PASSWORD`, `COMFYFLEET_PUBLIC_HOST`, and `COMFYFLEET_INSTANCE_IMAGE`. Re-running it updates the manager container. It does not delete workflow instances.

### The same start by hand

```bash
docker pull ghcr.io/recognizeyourprivilege/comfyfleet:phase1
docker pull ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest
docker tag ghcr.io/recognizeyourprivilege/comfyfleet:phase1 comfyfleet:phase1
docker tag ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest comfyfleet-manager:latest

docker run -d --name comfyfleet-manager \
  --restart unless-stopped \
  --gpus all \
  -p 9100:9100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /home:/home \
  -e COMFYFLEET_PASSWORD=replace-with-a-long-secret \
  -e COMFYFLEET_PUBLIC_HOST=192.168.1.20 \
  -e COMFYFLEET_INSTANCE_IMAGE=ghcr.io/recognizeyourprivilege/comfyfleet:phase1 \
  ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest
```

[compose.yaml](compose.yaml) is the same service. It does not pull the instance image. Pull that tag first, or use `./install.sh --compose`:

```bash
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
docker pull ghcr.io/recognizeyourprivilege/comfyfleet:phase1
docker compose up -d
```

`docker compose` publishes `9100:9100`, mounts `/var/run/docker.sock` and `/home`, and requests `gpus: all`.

### What comes up by itself

The image entrypoint runs `comfyfleet ui` and binds **`0.0.0.0:9100`**. You do not start a second UI process and you do not install Python on the host.

- Web UI: `http://<host>:9100/`
- Health: `GET http://<host>:9100/api/health`

`COMFYFLEET_BIND_HOST` and `COMFYFLEET_BIND_PORT` change that bind (defaults `0.0.0.0` and `9100`). Publish the same port with `-p`.

`docker run` still requires `-e COMFYFLEET_PASSWORD=...` when you pass a subcommand such as `list`. The default, with no command, is the UI. `docker exec` into an already-running manager is a local process: it does not use the browser session. Anyone who can exec already has access to the Docker socket.

### Docker socket

`-v /var/run/docker.sock:/var/run/docker.sock` is how the manager creates, starts, and stops sibling containers. The default user in the image is root, which can use a host socket that is mode `660` and group `docker`.

If the socket is missing, or this process cannot read and write it, create, start, stop, and list fail with that reason. The UI still comes up so you can see the error. This image does not start its own Docker daemon.

### GPU probe

The probe is **`nvidia-smi` inside the manager process**. The manager image does not contain CUDA libraries. `--gpus all` (compose: `gpus: all`) tells the NVIDIA Container Toolkit to mount the **host** driver and `nvidia-smi` into the manager, so the list is the host GPU list. Instance containers still get `--gpus device=N` on the host engine.

Without `--gpus all`, or without the toolkit, `nvidia-smi` is missing or cannot talk to the driver. Create and start fail with that message. The UI shows it and does not invent a GPU. `list` and `stop` do not need a GPU.

### Mounts the manager must see

Create writes workflow files and directories on `/home` inside the manager, then passes those **same paths** to `docker create -v` on the host engine. Mount the host parent so both sides are the host directory:

```text
-v /home:/home
```

| Host path | Instance container |
|---|---|
| `/home/models` | `/opt/ComfyUI/models` (shared, read-write) |
| `/home/custom_nodes_<name>` | `/opt/ComfyUI/custom_nodes` |
| `/home/files/<name>/input` | `/opt/ComfyUI/input` |
| `/home/files/<name>/output` | `/opt/ComfyUI/output` |
| `/home/files/<name>/temp` | `/opt/ComfyUI/temp` |
| `/home/files/<name>` | `/opt/comfyfleet/instance` |

Create creates any of those directories that are missing, including the usual ComfyUI model subfolders under `/home/models`.

`/home/models` is shared by every instance and is read-write. Two running instances that write the same model file can corrupt it.

If `/home` is not a bind mount, the manager warns at startup. Directories created only inside the manager filesystem are not the directories sibling containers mount.

`COMFYFLEET_INSTANCE_IMAGE` overrides the instance tag when you do not pass another image. The manager default is `comfyfleet:phase1`. `install.sh` sets the variable to the GHCR ref it pulled (`ghcr.io/recognizeyourprivilege/comfyfleet:phase1`, or a digest ref when `COMFYFLEET_INSTANCE_DIGEST` is set) and also tags that image as `comfyfleet:phase1`.

## Use the web UI

Open the manager URL and sign in. The password is the `COMFYFLEET_PASSWORD` value. Wrong password shows invalid credentials. Log out clears the session. After that, fleet API calls fail until you sign in again.

The UI is the primary way to create an instance. Upload a workflow JSON, pick GPUs, then create. New instances stay **stopped**. Start the ones you want running and stop the others. Open is enabled only while an instance is running.

There is no baked default workflow. A missing or invalid workflow JSON does not create an instance.

On a host with more than one GPU, create asks which GPU or GPUs to attach. A single GPU still has to be selected (the UI sends `gpu` or `gpus`). The page does not pick a GPU for you.

### Open links

`COMFYFLEET_PUBLIC_HOST` is the hostname or IP baked into Open URLs (`http://<that-host>:<port>`). Set it to the LAN address browsers use.

When it is unset, Open uses the request `Host` header with the control port removed, if that value is a safe hostname or IP. `0.0.0.0`, empty values, and values with spaces or slashes are ignored. An invalid `COMFYFLEET_PUBLIC_HOST` stops the control server at startup.

From another machine, the ComfyUI page is `http://<public-host>:<port>`. Inside the instance, ComfyUI listens on `0.0.0.0` port `8188`. The manager publishes that as a host port starting at **8188**.

## Optional CLI inside the manager

The same lifecycle is available in the container. Upload in the UI remains the primary create path. These commands do not send the HTTP session cookie. The container itself will not start without `COMFYFLEET_PASSWORD`.

```bash
docker exec -it comfyfleet-manager comfyfleet create --workflow /home/files/incoming/portrait.json --gpu 0
docker exec -it comfyfleet-manager comfyfleet list
docker exec -it comfyfleet-manager comfyfleet start portrait
docker exec -it comfyfleet-manager comfyfleet stop portrait
```

`start`, `stop`, and `restart` do not rebuild the instance image. Create leaves the container stopped. Pass `--start` only when that one instance should start immediately.

On a host with more than one GPU, non-interactive create must pass `--gpu 0` or `--gpus 0,1` (or `--gpus all`). `--gpu` and `--gpus` together are rejected.

`list` prints the instance name, Docker status, host port, Open URL, GPU list, and workflow path.

## Naming

The container name is the workflow filename stem, sanitized:

1. Lowercase.
2. Characters outside `[a-z0-9_-]` become `_`.
3. Repeated underscores collapse. Leading and trailing `_` and `-` are removed.
4. An empty result, or one that does not start with a letter or digit, is prefixed with `wf`.
5. Names longer than 63 characters are truncated and given a stable suffix: `-` plus the first 8 hex digits of SHA-256 of the original stem (UTF-8).

`My Flow.json` becomes `my_flow`. A second create that sanitizes to an existing name is refused. `--force` replaces a **stopped** instance (this is also how you change the GPU set). `--force` refuses while the container is running.

## Instance image

The instance image is Debian bookworm-slim plus the NVIDIA CUDA 12.4 runtime (not the devel toolkit). The GHCR tag `ghcr.io/recognizeyourprivilege/comfyfleet:phase1` is built from this Dockerfile. The kitchen annotation rewrite and the `gcc` and `python3-dev` packages are image layers. The host does not repeat those steps.

| Package | Pin |
|---|---|
| `cuda-libraries-12-4` | `12.4.1-1` |
| `cuda-cudart-12-4` | `12.4.127-1` |
| `libcudnn9-cuda-12` | `9.1.0.70-1` (cuDNN runtime for CUDA 12.4) |

Repository: `https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/` (keyring `cuda-keyring_1.1-1_all.deb`).

PyTorch is the CUDA 12.4 wheels from `https://download.pytorch.org/whl/cu124`, not the PyPI default (that wheel is CUDA 13):

| Wheel | Exact spec |
|---|---|
| torch | `torch==2.6.0+cu124` |
| torchvision | `torchvision==0.21.0+cu124` |

The image Python is Debian bookworm CPython 3.11. A constraints file at `/opt/comfyfleet/torch-constraints.txt` keeps later `pip install -r requirements.txt` from replacing those wheels. `comfy-kitchen==0.2.36` is the pure-Python wheel (`py3-none-any`) so the eager backend runs on CUDA 12.4. The manylinux wheel targets CUDA 13 and is not installed. Torch 2.6.0+cu124 `infer_schema` rejects that release's PEP 585 `list[int]` and `list[bool]` custom-op annotations, so `import comfy_kitchen` dies in `backends/eager/conv3d.py` (`stride: list[int]`). There is no stable torch 2.7+ cu124 wheel. The image keeps this torch pin and, after install, rewrites those custom-op annotations to `typing.List` via `docker/patch_comfy_kitchen_torch26.py` (also at `/opt/comfyfleet/patch_comfy_kitchen_torch26.py`).

That import also loads the Triton backend. Torch 2.6.0+cu124 depends on `triton==3.2.0`. `comfy_kitchen/backends/triton/quantization.py` decorates kernels with `@triton.autotune`, and the decorator initializes Triton's NVIDIA driver, which JIT-compiles `cuda_utils` (`driver.c`) through `triton/runtime/build.py`. `driver.c` includes `Python.h`. bookworm-slim has neither `gcc` nor Python headers, so on a GPU host that compile raises `Failed to find C compiler. Please specify via CC environment variable.` The image installs Debian bookworm `gcc` and `python3-dev` for that step. It does not install `g++`, `build-essential`, or `cuda-nvcc`: Triton 3.2's import-time compile is C, and the wheel ships `cuda.h`. The image build still does not `import comfy_kitchen`. With no NVIDIA driver mounted, Triton raises `0 active drivers` before it looks for a compiler. The link step uses `-lcuda` against the host driver the NVIDIA Container Toolkit mounts at container start (`libcuda.so`).

Baked custom nodes (also in `docker/PINS.txt`):

| Component | Upstream | Pin |
|---|---|---|
| ComfyUI | `https://github.com/Comfy-Org/ComfyUI` | tag `v0.38.0`, commit `6b747c0428c343e1417219641db93a4fb7cb69ae` |
| ComfyUI-Manager | `https://github.com/Comfy-Org/ComfyUI-Manager` | `14b5aaab711ad1f1306d420732a923fb058c44d7` |
| pixaroma (full repo) | `https://github.com/pixaroma/ComfyUI-Pixaroma` | tag `v1.4.181`, commit `9259bc49557a92e3fc14796999468c723bd1ecdd` |
| ComfyUI-ComfyDock | `https://github.com/RecognizeYourPrivilege/ComfyUI-ComfyDock` | `3a9ff9eba897bf2388d6c1943b01d819ba05a0c6` |

`git` is installed in the instance image so Manager and in-container updates can use it. The pixaroma checkout includes that pack's workflow-browser examples. Those files are not the instance default. Omitting the workflow fails create.

`examples/workflow.example.json` shows the shape of a UI-format workflow. It is documentation only. `.dockerignore` excludes `examples/`, and neither Dockerfile copies it.

The host `custom_nodes` directory hides the image's `custom_nodes` folder. On every start the instance entrypoint symlinks the baked nodes into that mount if those names are not already present:

- `ComfyUI-Manager`
- `ComfyUI-Pixaroma`
- `ComfyUI-ComfyDock`
- `comfyfleet_default_workflow` (loader only; it is not a workflow)

A real directory you place at one of those names is left alone. The links point at `/opt/comfyfleet/baked_custom_nodes/...` inside the container, so on the host they look dangling; ComfyUI follows them in the container.

## Manager git URL install

Install via Git URL is on for an instance that starts from this image. The entrypoint writes Manager's `config.ini` and exports `COMFYFLEET_TRUSTED_INSTALL=1` before it execs ComfyUI.

Pinned ComfyUI v0.38.0 has `folder_paths.get_system_user_directory`, so Manager 14b5aaab reads `<user directory>/__manager/config.ini`. The entrypoint does not pass `--user-directory`. That directory is not a bind mount. The file is `/opt/ComfyUI/user/__manager/config.ini`. On every start the entrypoint creates it when it is missing, and otherwise sets these keys in `[default]`. Other keys and other sections stay:

```ini
[default]
allow_git_url_install = true
allow_pip_install = true
security_level = normal
```

Manager's flag parser accepts the string `true` in any case. `security_level` is lowercased on read. With no file, both install flags are false.

That same Manager commit still requires a loopback `--listen` (`127.0.0.1` or `::1`) inside `is_dedicated_install_allowed` before `POST /customnode/install/git_url`, `POST /customnode/install/pip`, or the unknown-git-URL arm of the install queue will run. ComfyFleet keeps `--listen 0.0.0.0` so Docker can publish the instance port. `docker/patch_manager_trusted_install.py` rewrites that one helper in the baked checkout. The flag stays required. When the entrypoint has exported `COMFYFLEET_TRUSTED_INSTALL=1`, the loopback term is skipped. With the variable unset, the stock check remains.

`0.0.0.0` is what lets Docker publish the instance port onto the docker.sock LAN. That operator network is the ComfyFleet case. A Manager process on the public internet, with git-URL install open and no operator gate, is a different threat model. The env var is the gate this image sets.

The instance log records the seed, the env var, and the values Manager loaded:

```text
comfyfleet: Manager config /opt/ComfyUI/user/__manager/config.ini allow_git_url_install=true allow_pip_install=true security_level=normal
comfyfleet: COMFYFLEET_TRUSTED_INSTALL=1
[ComfyUI-Manager] ComfyFleet dedicated install flags: allow_git_url_install=True allow_pip_install=True security_level=normal COMFYFLEET_TRUSTED_INSTALL=1
```

A directory you place at `custom_nodes/ComfyUI-Manager` is left alone, and that copy does not include this patch. After an in-container `git pull` of the baked Manager, run `python /opt/comfyfleet/patch_manager_trusted_install.py` again. The config seed runs on the next start either way.

To prove the gate opened, recreate the instance from this image and read the three log lines above. Then, from the host, against the published port:

```bash
curl -sS -D - -o /tmp/git-url-body.txt \
  -X POST "http://127.0.0.1:<host-port>/customnode/install/git_url" \
  -H 'Content-Type: application/json' \
  -d '{}'
```

An open gate returns **400** and a body that contains `expected JSON object with a 'url' field`. The handler checks the flag before it reads the body, so a closed gate returns **403** and `{"error": "allow_git_url_install"}`. 400 is the proof. A real install is the same request with `{"url":"https://github.com/ltdrdata/ComfyUI-Impact-Pack"}`, which returns **200** when the clone succeeds.

## Workflow load path

1. Create copies the operator workflow to `/home/files/<name>/default_workflow.json`.
2. Instance metadata is written to `/home/files/<name>/comfyfleet.json` (name, port, GPUs, image, workflow paths).
3. `/home/files/<name>` is bind-mounted at `/opt/comfyfleet/instance`. Edit the workflow file in place.
4. Every instance start runs `docker/entrypoint.sh`, which refuses to exec ComfyUI if that JSON object is missing or invalid. It then writes a new boot id to `/tmp/comfyfleet-boot-id`.
5. ComfyUI is executed as `python main.py --listen 0.0.0.0 --port 8188`. Before that exec, the entrypoint seeds Manager `config.ini` and exports `COMFYFLEET_TRUSTED_INSTALL=1` (see Manager git URL install).
6. The baked loader serves `GET /comfyfleet/default-workflow` and `GET /comfyfleet/boot`. Its frontend extension loads the operator graph after the UI comes up. A browser tab loads the file once per boot id and file mtime. If the fetch fails, the loader does not substitute another workflow.

GPU changes are a recreate: stop the instance, then create again with `--force` and the new GPU set. There is no in-place GPU edit.

## Ports

The first instance records host port **8188**. The next free port is 8189, then 8190, and so on. Ports recorded by other instances are reserved even while those containers are stopped.

On start, ComfyFleet keeps the recorded port when it is free. If it is taken, the container is recreated (same image, no rebuild) on the next free port and the metadata is updated.

Inside the manager, host ports published by other containers are read from the host engine (`docker ps`). A process on the host that is not a container and is bound to 8188 is not visible that way; fleet metadata still reserves ports already given to instances.

Starting an instance that would run more containers than the host has GPUs records a warning and continues. Set `COMFYFLEET_MAX_CONCURRENT` to a positive integer to refuse instead. The default does not hard-block.

Containers are created with `--restart no`, so a created-but-never-started instance stays stopped across a Docker daemon restart. `start` sets `--restart unless-stopped`. After `stop`, Docker leaves it stopped.

## Updates

There is no zero-downtime or rolling update.

**Pull a newer instance image.** `create` uses the tag already on the host engine. Pulling moves `:phase1`. Then recreate the instance. This does not rebuild torch on the host.

```bash
docker pull ghcr.io/recognizeyourprivilege/comfyfleet:phase1
docker tag ghcr.io/recognizeyourprivilege/comfyfleet:phase1 comfyfleet:phase1
docker exec comfyfleet-manager comfyfleet stop portrait
docker exec comfyfleet-manager comfyfleet create \
  --workflow /home/files/portrait/default_workflow.json --force --gpu 0
docker exec comfyfleet-manager comfyfleet start portrait
```

If the manager was installed with `COMFYFLEET_INSTANCE_IMAGE` set to a digest, pulling `:phase1` does not change that pin. Re-run `install.sh` with the new `COMFYFLEET_INSTANCE_DIGEST`, then recreate. Re-running `install.sh` replaces the manager container and pulls images. It leaves workflow containers in place until you stop and create them again.

A local image rebuild is documented under Development.

**In-container git (not pinned after you move HEAD):**

```bash
docker exec -it portrait bash
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
docker exec comfyfleet-manager comfyfleet restart portrait
```

The image checkouts start detached at the pins above, so a bare `git pull` will not move them until you check out a branch. Keep the torch constraints file in the pip command so a pull does not replace CUDA 12.4 torch with the PyPI CUDA 13 wheel. If that command reinstalls comfy-kitchen, install the pure-Python 0.2.36 wheel again and rerun `python /opt/comfyfleet/patch_comfy_kitchen_torch26.py`. A manylinux kitchen wheel targets CUDA 13, and an unpatched 0.2.36 tree crashes torch 2.6 at import. If the Manager pull replaces the baked tree, rerun `python /opt/comfyfleet/patch_manager_trusted_install.py`. A pulled Manager has the stock loopback gate again. The entrypoint still rewrites `config.ini` on the next start.

## API

The UI and the API are same-origin. This server does not send CORS headers. Do not add a cross-origin policy that drops or ignores the session cookie. Docker lifecycle stays in `comfyfleet.control`. The pages call `/api/...` with `credentials: "same-origin"` so the browser sends the HttpOnly session cookie.

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/api/health` | no | Liveness only. No fleet list and no password |
| `POST` | `/api/login` | no | Body `{"password":"..."}`. Sets the session cookie |
| `POST` | `/api/logout` | no | Clears the session cookie and the server session |
| `GET` | `/login` | no | Sign-in page |
| `GET` | `/api/gpus` | yes | Detected GPUs (`index`, `name`, `memory`). **503** when `nvidia-smi` is missing or fails |
| `GET` | `/api/instances` | yes | `name`, `status`, `port`, `gpus`, and `url` when `status` is `running` |
| `POST` | `/api/instances` | yes | Create. Requires an uploaded workflow JSON or `workflow_path`. Requires `gpu` or `gpus`. `start` defaults to false |
| `POST` | `/api/instances/{name}/start` | yes | Start without rebuilding the image |
| `POST` | `/api/instances/{name}/stop` | yes | Stop without destroying the container |

Protected routes accept the session cookie **or** `Authorization: Bearer` set to the same `COMFYFLEET_PASSWORD` value. A missing credential is **401** with `unauthorized` or `session expired`. That text is not a Docker or GPU failure.

Field names and error bodies are in [CONTROL_HTTP.md](CONTROL_HTTP.md).

## Errors you should expect

| Situation | What you see |
|---|---|
| `COMFYFLEET_PASSWORD` missing or empty | The manager exits non-zero and says it is refusing to start. The UI does not come up. |
| Wrong password, or no cookie / Bearer | Sign-in says invalid credentials. Fleet routes return **401**. |
| `/var/run/docker.sock` missing | Create, start, stop, and list say the socket is missing and that the manager does not start its own daemon. The UI still loads after you sign in. |
| Socket permission denied | The message says permission denied, and that the socket is root-equivalent. Run as root or as a uid in the host `docker` group. |
| `nvidia-smi` missing or no driver | Create and start fail. `/api/gpus` returns 503. Pass `--gpus all` and install the NVIDIA Container Toolkit. |
| Workflow missing or not a JSON object | Create is refused. There is no substitute graph. |

## Development / optional

Host `pip install` is for contributors and CI. It is not required to run a fleet.

```bash
python -m pip install .
COMFYFLEET_PASSWORD=replace-with-a-long-secret comfyfleet ui --host 127.0.0.1 --port 9100
python -m unittest discover -s tests
```

`comfyfleet ui` on the host is the same control server the manager starts, and it also refuses to start without `COMFYFLEET_PASSWORD`. Use `--host 127.0.0.1` to keep a dev server on one machine. Host commands such as `comfyfleet create` do not go through HTTP Auth. They are a local process on that machine.

`scripts/manager-smoke.sh` builds `comfyfleet-manager:latest`, starts it with a placeholder password, and requests `/api/health` and `/login`. It skips when `docker` is not installed. A full create/start/stop against a real ComfyUI container needs the instance image and a GPU. `tests/test_manager.py` walks health, UI, create (workflow upload), start, and stop with a fake engine.

The tests cover naming, port reservation, workflow rejection, `nvidia-smi` failures, GPU prompts, create-without-start, collision, start/stop without an image rebuild, the HTTP API, and the manager socket and public-host errors. `tests/test_ui.py` checks that the pages call that API and do not implement Docker themselves. They do not build the CUDA image and they do not need a GPU.

### Local image rebuild

Optional. Use this when you are changing the Dockerfiles, or when GHCR does not have a public image yet. No GPU is required: the instance Dockerfile does not import `comfy_kitchen` during the build (that import loads Triton, which errors when no driver is mounted). Torch wheels are large. Expect a long build and several gigabytes of disk.

```bash
docker build -t comfyfleet:phase1 .
docker build -f Dockerfile.manager -t comfyfleet-manager:latest .
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
export COMFYFLEET_INSTANCE_IMAGE=comfyfleet:phase1
export COMFYFLEET_MANAGER_IMAGE=comfyfleet-manager:latest
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

`compose.build.yaml` points the manager at the local tags. The instance image is still the separate `docker build -t comfyfleet:phase1 .` above. Recreate running instances after that rebuild the same way as a pulled update: stop, `create --force`, start.

### Publish to GHCR

[`.github/workflows/publish-images.yml`](.github/workflows/publish-images.yml) builds both Dockerfiles on `ubuntu-24.04` (`linux/amd64`) and pushes to GHCR. It runs on pushes to `main` that change image inputs, and from the Actions tab (`workflow_dispatch`). It does not run on pull requests. The runner has no GPU. The instance job deletes preinstalled runner toolchains before the build because torch and CUDA layers are large, and the job timeout is 180 minutes.

| Image | Tags |
|---|---|
| `ghcr.io/recognizeyourprivilege/comfyfleet` | `phase1`, `latest`, `<git sha>` |
| `ghcr.io/recognizeyourprivilege/comfyfleet-manager` | `latest`, `<git sha>` |

The workflow logs in with `GITHUB_TOKEN` (`packages: write`). A personal access token is not required for that job. The job summary prints:

```text
instance ghcr.io/recognizeyourprivilege/comfyfleet@sha256:<digest>
manager ghcr.io/recognizeyourprivilege/comfyfleet-manager@sha256:<digest>
```

Those digests are not committed automatically. Copy them into the Install section above.

What a human still has to do before operators can pull:

1. Merge the workflow to `main`. A push to `main` that touches the workflow file starts it. `workflow_dispatch` is offered from the Actions tab only after the workflow file is on the default branch. This pull request does not publish images.
2. Wait until both jobs succeed. A failed instance job is often disk or time on the hosted runner. The fallback is `scripts/publish-images.sh` on a machine with more free disk. No GPU is required there either.
3. Open the `comfyfleet` and `comfyfleet-manager` packages on the account. A personal-account package is private on first publish. Set each package to public, or anonymous `docker pull` is denied. Making a package public cannot be undone. `GITHUB_TOKEN` can push the image and does not change visibility.
4. Copy the digests from the job summary into this README if installs should be pinned.

GHCR limits each layer to 10 GB and each upload to about 10 minutes. These Dockerfiles are the same ones a local build uses. The workflow does not split layers.

Manual push, same tags. Log in first. The password is a PAT, not your GitHub password: classic `write:packages`, or a fine-grained token with Packages read and write.

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
scripts/publish-images.sh
```

`scripts/publish-images.sh` runs `docker buildx build --platform linux/amd64 --push` for `Dockerfile` and `Dockerfile.manager`, then prints digests. It sets `org.opencontainers.image.source` so a CLI push can be linked to this repo. An Actions push with `GITHUB_TOKEN` links the package to the repo by itself. A CLI push does not, unless that label or a later UI link is present.
