![ComfyFleet](comfyfleet-logo-ships.jpg)

# ComfyFleet

Two Docker images. The **manager** serves the control UI and talks to the host Docker engine. The **instance** image is ComfyUI. The manager starts sibling instance containers. It does not run a Docker daemon of its own.

The Docker socket mounted into the manager is **root-equivalent** on the host. Run the manager only on a machine you trust. `COMFYFLEET_PASSWORD` is required. If it is missing or empty, the manager refuses to start. Sign in in the browser, or send `Authorization: Bearer` with the same value. A trusted LAN is still recommended. Do not publish port **9100** or the ComfyUI ports (8188 and up) on the public internet. ComfyUI is not behind this login.

HTTP field names: [CONTROL_HTTP.md](CONTROL_HTTP.md).

## Images

[`.github/workflows/publish-images.yml`](.github/workflows/publish-images.yml) publishes both to GHCR from `main`. Both are `linux/amd64` Debian bookworm-slim.

| Image | Pull | Local tag from `install.sh` | Role |
|---|---|---|---|
| Manager | `ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:a4204564c60cf3afc40db17b34a268cf2c1c2f8e685b4601bc1c6e4dedbc713f` | `comfyfleet-manager:latest` | Control HTTP and web UI. No CUDA stack. Includes `git` and `unzip` for create-time node seeding. |
| Instance | `ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba` | `comfyfleet:phase1` | ComfyUI. CUDA 12.4 runtime, torch `2.6.0+cu124`. |

Each publish also tags the git SHA. `ghcr.io/recognizeyourprivilege/comfyfleet:latest` is the same instance build as `:phase1`.

Override a pin without editing the script:

```bash
export COMFYFLEET_INSTANCE_DIGEST=sha256:<instance-digest>
export COMFYFLEET_MANAGER_DIGEST=sha256:<manager-digest>
```

`COMFYFLEET_INSTANCE_IMAGE` and `COMFYFLEET_MANAGER_IMAGE` replace the full ref. The manager default, when `COMFYFLEET_INSTANCE_IMAGE` is unset, is `comfyfleet:phase1`. `install.sh` pulls the digest above and tags that local name so a manager started without the variable still finds the image. The instance tag has to exist in the **host** engine before create, because sibling containers are started by that engine.

### Instance image contents

Debian bookworm-slim plus the NVIDIA CUDA 12.4 runtime (not the devel toolkit). The kitchen annotation rewrite and the `gcc` and `python3-dev` packages are image layers. The host does not repeat those steps.

| Package | Pin |
|---|---|
| `cuda-libraries-12-4` | `12.4.1-1` |
| `cuda-cudart-12-4` | `12.4.127-1` |
| `libcudnn9-cuda-12` | `9.1.0.70-1` |

PyTorch wheels come from `https://download.pytorch.org/whl/cu124` (not the PyPI CUDA 13 default): `torch==2.6.0+cu124`, `torchvision==0.21.0+cu124`. Python is bookworm CPython 3.11. `/opt/comfyfleet/torch-constraints.txt` pins those wheels and `numpy==2.2.6`. `PIP_CONSTRAINT` points at that file, so a later `pip install -r` or Manager's installer cannot replace torch or numpy.

`comfy-kitchen==0.2.36` is the pure-Python wheel (`py3-none-any`). Torch 2.6.0+cu124 `infer_schema` rejects that release's PEP 585 annotations, so the image rewrites them to `typing.List` with `docker/patch_comfy_kitchen_torch26.py` (also `/opt/comfyfleet/patch_comfy_kitchen_torch26.py`). The Triton backend in torch 2.6.0+cu124 (`triton==3.2.0`) JIT-compiles `driver.c`, which includes `Python.h`. bookworm-slim has neither `gcc` nor Python headers, so that compile raises `Failed to find C compiler. Please specify via CC environment variable.` The image installs `gcc` and `python3-dev` for that step. It does not install `g++`, `build-essential`, or `cuda-nvcc`. The image build does not `import comfy_kitchen`.

Baked custom nodes (also `docker/PINS.txt`):

| Component | Pin |
|---|---|
| ComfyUI `v0.38.0` | `6b747c0428c343e1417219641db93a4fb7cb69ae` |
| ComfyUI-Manager | `14b5aaab711ad1f1306d420732a923fb058c44d7` |
| ComfyUI-Pixaroma `v1.4.181` | `9259bc49557a92e3fc14796999468c723bd1ecdd` |
| ComfyUI-ComfyDock | `3a9ff9eba897bf2388d6c1943b01d819ba05a0c6` |
| `RES4LYF` | `3d1d69da69ee47f7647d59e1bd0967e472fccc41` |
| numpy | `2.2.6` |

`git` is in the instance image so Manager can clone. The host `custom_nodes` mount hides the image folder. On every start the entrypoint symlinks baked nodes into that mount when the name is absent: `ComfyUI-Manager`, `ComfyUI-Pixaroma`, `ComfyUI-ComfyDock`, `RES4LYF`, and `comfyfleet_default_workflow` (loader only; it is not a workflow). A real directory at one of those names is left alone.

`RES4LYF` imports `cv2`. Its `opencv-python` wheel needs `libxcb.so.1` on bookworm-slim. The image installs `libxcb1`, `libx11-6`, `libxext6`, `libice6`, `libsm6`, `libglib2.0-0`, and `libgl1`, and the build imports `cv2` so a missing library fails the build.

There is no baked default workflow. Omitting the workflow fails create. `examples/workflow.example.json` is documentation only. `.dockerignore` excludes `examples/`.

### Manager git URL install

The instance entrypoint writes `/opt/ComfyUI/user/__manager/config.ini` and exports `COMFYFLEET_TRUSTED_INSTALL=1` before it execs ComfyUI:

```ini
[default]
allow_git_url_install = true
allow_pip_install = true
security_level = normal
```

`--listen` stays `0.0.0.0` so Docker can publish the instance port. `docker/patch_manager_trusted_install.py` skips Manager's loopback check only when `COMFYFLEET_TRUSTED_INSTALL=1`. `0.0.0.0` is what lets Docker publish the instance port onto the docker.sock LAN. A directory you place at `custom_nodes/ComfyUI-Manager` is left alone and does not include this patch. After an in-container `git pull` of the baked Manager, run `python /opt/comfyfleet/patch_manager_trusted_install.py` again.

An open gate answers `POST /customnode/install/git_url` with **400** and `expected JSON object with a 'url' field` when the body is `{}`. A closed gate returns **403**.

## Host

- Linux with Docker.
- A working NVIDIA driver. `nvidia-smi` must succeed on the host.
- The [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), so `docker create --gpus device=N` works.
- Permission to create `/home/models`, `/home/custom_nodes_<name>`, and `/home/files/<name>/...`.

The host does not need Debian, a CUDA toolkit, or a local image rebuild.

## Install

From a checkout, replace `192.168.1.20` with the address browsers on your LAN use:

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

Or Compose (the script still pulls the instance image, which is not a compose service):

```bash
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
./install.sh --compose
```

`replace-with-a-long-secret` is a placeholder. Open `http://192.168.1.20:9100/`. The script pulls both images, tags the local names, removes an existing `comfyfleet-manager` container, and starts the manager with `--gpus all`, `-p 9100:9100`, the Docker socket, `/home`, `COMFYFLEET_PASSWORD`, `COMFYFLEET_PUBLIC_HOST`, and `COMFYFLEET_INSTANCE_IMAGE`. Re-running it updates the manager. It does not delete workflow instances.

### Manual run

Same start without the script. Pull both digests, tag the local names, then run the manager:

```bash
docker pull ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba
docker pull ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:a4204564c60cf3afc40db17b34a268cf2c1c2f8e685b4601bc1c6e4dedbc713f
docker tag ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba comfyfleet:phase1
docker tag ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:a4204564c60cf3afc40db17b34a268cf2c1c2f8e685b4601bc1c6e4dedbc713f comfyfleet-manager:latest

docker run -d --name comfyfleet-manager \
  --restart unless-stopped \
  --gpus all \
  -p 9100:9100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /home:/home \
  -e COMFYFLEET_PASSWORD=replace-with-a-long-secret \
  -e COMFYFLEET_PUBLIC_HOST=192.168.1.20 \
  -e COMFYFLEET_INSTANCE_IMAGE=ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba \
  ghcr.io/recognizeyourprivilege/comfyfleet-manager:latest@sha256:a4204564c60cf3afc40db17b34a268cf2c1c2f8e685b4601bc1c6e4dedbc713f
```

[compose.yaml](compose.yaml) is the same service. It does not pull the instance image:

```bash
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
docker pull ghcr.io/recognizeyourprivilege/comfyfleet:phase1@sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba
docker compose up -d
```

The entrypoint runs `comfyfleet ui` on `0.0.0.0:9100`. Health is `GET /api/health`. `COMFYFLEET_BIND_HOST` and `COMFYFLEET_BIND_PORT` change the bind. `docker run` still requires `-e COMFYFLEET_PASSWORD=...` when you pass a subcommand such as `list`.

`-v /var/run/docker.sock:/var/run/docker.sock` is how the manager creates sibling containers. The image user is root, which can use a host socket mode `660` group `docker`. If the socket is missing, create and start fail and the UI still comes up so you can see the error.

The GPU probe is `nvidia-smi` inside the manager. The manager image has no CUDA libraries. `--gpus all` (compose: `gpus: all`) mounts the host driver into the manager. Instance containers get `--gpus device=N`. Without the toolkit, `/api/gpus` returns 503 and create fails.

### Mounts

Create writes under `/home` in the manager, then passes those same paths to `docker create -v`. Mount the host parent:

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

`/home/models` is shared and read-write. If `/home` is not a bind mount, the manager warns at startup.

### Create-time custom nodes

Optional. Leave the git URL list and the zip blank to skip them. Create still requires a workflow and does not fail because those fields were empty. `install_missing_from_workflow` defaults to true and installs only nodes referenced by **that** workflow JSON that are not already in the baked image or on the volume, via Manager's git-URL install (`COMFYFLEET_TRUSTED_INSTALL`). It does not install the Manager registry. A failed clone, extract, or install is a `warnings` entry. The instance is still created. No new instance image is published for this. Delete is `POST /api/instances/{name}/delete` (container and fleet record only). Host mounts, including `custom_nodes`, stay.

## Use

Open the manager URL and sign in with `COMFYFLEET_PASSWORD`. Upload a workflow JSON, pick GPUs, then create. New instances stay stopped. **Open** is a shell proxied by the manager. **Open Comfy** uses `window.location.hostname` plus the instance's published port and is enabled only while the instance is running. **Force stop** is `docker kill`. **Delete** is the trash icon. It asks for confirmation, then removes that container and its fleet record. Host files stay. Comfy args live under **Advanced / ComfyUI flags**, collapsed until you open them.

On a multi-GPU host, create asks which GPUs to attach. A single GPU still has to be selected.

CLI inside the manager (local process, not the browser session):

```bash
docker exec -it comfyfleet-manager comfyfleet create --workflow /home/files/incoming/portrait.json --gpu 0
docker exec -it comfyfleet-manager comfyfleet list
docker exec -it comfyfleet-manager comfyfleet start portrait
docker exec -it comfyfleet-manager comfyfleet stop portrait
```

`start`, `stop`, and `restart` do not rebuild the instance image. On more than one GPU, pass `--gpu 0` or `--gpus 0,1`. `--force` replaces a **stopped** instance of the same sanitized name.

The container name is the workflow filename stem, lowercased, with characters outside `[a-z0-9_-]` turned into `_`, truncated at 63 characters. Ports start at **8188**. Changing GPUs or launch flags is a recreate (`--force` on a stopped instance). ComfyUI runs as `python main.py --listen 0.0.0.0 --port 8188` plus the saved flags. Pasted `--listen` or `--port` in extra args are removed. On NVIDIA, `--lowvram` does nothing while dynamic VRAM is enabled, so a 12GB GPU also needs `--disable-dynamic-vram`.

`COMFYFLEET_MAX_CONCURRENT` set to a positive integer refuses a start that would exceed the GPU count. The default records a warning and continues. Containers are created with `--restart no`. `start` sets `--restart unless-stopped`.

To move an instance onto a newer digest, re-run `install.sh` with a new `COMFYFLEET_INSTANCE_DIGEST`, then stop, `create --force`, and start. Pulling the floating `:phase1` tag does not move a digest-pinned install.

## API

Same-origin. No CORS headers. Pages call `/api/...` with `credentials: "same-origin"`.

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/api/health` | no | Liveness. No fleet list and no password |
| `POST` | `/api/login` | no | Body `{"password":"..."}`. Sets the session cookie |
| `POST` | `/api/logout` | no | Clears the session |
| `GET` | `/api/gpus` | yes | **503** when `nvidia-smi` fails |
| `GET` | `/api/instances` | yes | `name`, `status`, `port`, `gpus` |
| `POST` | `/api/instances` | yes | Create. Workflow required. `start` defaults to false |
| `POST` | `/api/instances/{name}/start` | yes | Start |
| `POST` | `/api/instances/{name}/stop` | yes | Stop |
| `POST` | `/api/instances/{name}/delete` | yes | Remove that container. Host mounts stay |

Protected routes accept the session cookie or `Authorization: Bearer`. A missing credential is **401**.

| Situation | What you see |
|---|---|
| `COMFYFLEET_PASSWORD` missing or empty | Manager exits. The UI does not come up. |
| Wrong password | **401** |
| `/var/run/docker.sock` missing | Create and start fail. The UI still loads. |
| `nvidia-smi` missing | Create fails. `/api/gpus` returns 503. Pass `--gpus all`. |
| Workflow missing | Create is refused. |

## Development / optional

Host `pip install` is for contributors and CI. It is not required to run a fleet.

```bash
python -m pip install .
COMFYFLEET_PASSWORD=replace-with-a-long-secret comfyfleet ui --host 127.0.0.1 --port 9100
python -m unittest discover -s tests
```

`comfyfleet ui` on the host also refuses to start without `COMFYFLEET_PASSWORD`. Host commands such as `comfyfleet create` do not go through HTTP Auth.

`scripts/manager-smoke.sh` builds `comfyfleet-manager:latest`, starts it with a placeholder password, and requests `/api/health` and `/login`.

### Local image rebuild

Optional. Use this when you are changing the Dockerfiles. No GPU is required for the build: the instance Dockerfile does not import `comfy_kitchen` during the build. Torch wheels are large.

```bash
docker build -t comfyfleet:phase1 .
docker build -f Dockerfile.manager -t comfyfleet-manager:latest .
export COMFYFLEET_PASSWORD=replace-with-a-long-secret
export COMFYFLEET_PUBLIC_HOST=192.168.1.20
export COMFYFLEET_INSTANCE_IMAGE=comfyfleet:phase1
export COMFYFLEET_MANAGER_IMAGE=comfyfleet-manager:latest
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

`compose.build.yaml` points the manager at the local tags. The instance image is still the separate `docker build -t comfyfleet:phase1 .` above. Recreate running instances after that rebuild: stop, `create --force`, start.

### Publish to GHCR

[`.github/workflows/publish-images.yml`](.github/workflows/publish-images.yml) builds both Dockerfiles on `ubuntu-24.04` (`linux/amd64`) and pushes to GHCR. It runs on pushes to `main` that change image inputs, and from `workflow_dispatch`. It does not run on pull requests. The runner has no GPU. The instance job timeout is 180 minutes.

| Image | Tags |
|---|---|
| `ghcr.io/recognizeyourprivilege/comfyfleet` | `phase1`, `latest`, `<git sha>` |
| `ghcr.io/recognizeyourprivilege/comfyfleet-manager` | `latest`, `<git sha>` |

The workflow logs in with `GITHUB_TOKEN` (`packages: write`). The job summary prints both digests. Those digests are not committed automatically. When a publish should move the install pin, copy them into this file, `install.sh`, and `compose.yaml`.

Before operators can pull:

1. Merge the workflow to `main`. This pull request does not publish images.
2. Wait until both jobs succeed. The fallback is `scripts/publish-images.sh` on a machine with more free disk. No GPU is required there either.
3. A personal-account package is private on first publish. Set each package to public, or anonymous `docker pull` is denied. Making a package public cannot be undone.

Manual push. The password is a PAT, not your GitHub password: classic `write:packages`, or a fine-grained token with Packages read and write.

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
scripts/publish-images.sh
```

`scripts/publish-images.sh` runs `docker buildx build --platform linux/amd64 --push` for `Dockerfile` and `Dockerfile.manager`, then prints digests.
