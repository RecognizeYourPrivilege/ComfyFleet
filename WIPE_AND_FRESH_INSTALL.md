# Wipe and fresh install

Short path for clearing a fleet and installing again. Delete and `install.sh` leave host directories under `/home/ComfyFleet` in place.

## What stays

**Delete** removes one instance container and its fleet record (`/home/ComfyFleet/files/<name>/comfyfleet.json`). The UI **Delete** button does this after confirmation. The same call is `POST /api/instances/{name}/delete` (session cookie or `Authorization: Bearer $COMFYFLEET_PASSWORD`). A running container is force-stopped first. `DELETE` on that path returns **405** and removes nothing.

These host paths stay. They all live under `/home/ComfyFleet`:

| Path | What it is |
|---|---|
| `/home/ComfyFleet/custom_nodes_<name>` | That instance's custom nodes |
| `/home/ComfyFleet/files/<name>` | Workflow, input, output, temp, fleet record (record is removed on delete) |
| `/home/ComfyFleet/models` | Shared model library. SAM weights live in `/home/ComfyFleet/models/sams` and are not in the image. |
| `/home/ComfyFleet/wildcards` | Shared Impact wildcards. Create makes this directory only when it is missing and does not wipe it. |

`install.sh` replaces the `comfyfleet-manager` container only. Workflow instances stay on whatever image they were created with.

## Path A — containers and fleet records

1. List instances in the UI, or `GET /api/instances`.
2. Delete each one (UI **Delete**, or the POST above). Stop first if you want a graceful stop; delete force-stops a container that is still running.
3. Re-run install with the password and the LAN address browsers use. The script asks **cu130** (host driver CUDA 13.0), **cu124** (host driver CUDA 12.4), or **both** (pull each instance image). `--cuda-tag` or `COMFYFLEET_CUDA_TAG` skips the prompt (`cu130`, `cu124`, or `both`); the flag wins. With neither set, a non-interactive run (no TTY, including curl-to-bash, or an empty answer) pulls cu130 only and does not pull cu124.

   ```bash
   export COMFYFLEET_PASSWORD=replace-with-a-long-secret
   export COMFYFLEET_PUBLIC_HOST=192.168.1.20
   ./install.sh
   # CUDA 12.4 host: ./install.sh --cuda-tag cu124
   # Both lines: ./install.sh --cuda-tag both
   ```

4. In the UI, create each instance and start it. The create sheet has the same **cu130** / **cu124** picker. Match the host driver's CUDA major (`nvidia-smi`). A mismatched line can fail when the instance starts. New instances are created stopped unless you use **Create & start**. Every create sets `--shm-size 8g`.

The other line has to be on the host engine before create can use it. `./install.sh --cuda-tag both` pulls both instance images and tags `comfyfleet:cu130` (`comfyfleet:latest`) and `comfyfleet:cu124` (`comfyfleet:phase1`). The manager default stays cu130 unless `COMFYFLEET_INSTANCE_IMAGE` is already a cu124 ref and `COMFYFLEET_INSTANCE_DIGEST` is unset, so create can pick either line. A one-line install can be repeated with the other `--cuda-tag`, or pull that image yourself. See [README.md](README.md) for the curl-to-bash install and the current GHCR digest pins.

## Path B — also remove host files

After the containers are gone:

```bash
rm -rf /home/ComfyFleet/custom_nodes_<name> /home/ComfyFleet/files/<name>
```

`/home/ComfyFleet/wildcards` is shared. Remove it only when the wildcard library should go too:

```bash
rm -rf /home/ComfyFleet/wildcards
```

`comfyfleet fix-owner` (and the Host menu **Fix ownership** action) recursively chowns `/home/ComfyFleet/wildcards`, `/home/ComfyFleet/models`, every `/home/ComfyFleet/custom_nodes_*` directory, and `/home/ComfyFleet/files`. Blank `--user` and `--group` mean `comfyuser`. The Host dialog leaves those fields blank for the same default, or the operator can type another user and group. It creates a missing user or group, with home `/home/ComfyFleet`, and it does not create `/home/<name>`. It refuses every other path. A symlink from `custom_nodes_*` into `/opt/comfyfleet/baked_custom_nodes` does not fail that command and is not followed. Create's one-shot chown of a newly created `/home/ComfyFleet/wildcards` uses `comfyuser:comfyuser` and is not that recursive walk.

`/home/ComfyFleet/models` is shared. Remove it only when the model library should go too:

```bash
rm -rf /home/ComfyFleet/models
```

The manager creates these directories as root. Use the same user that owns them.

## Path C — optional image prune, then pull again

After instances are deleted, drop unused images and pull the current pins:

```bash
docker image prune -a
./install.sh
```

`docker image prune -a` removes every unused image on the host, including images that are not ComfyFleet. Images still used by a container stay. `install.sh` then pulls the manager and the CUDA line you select, or both instance images when the choice is **both**.

## Recreate after a digest change

A newer digest on the same instance does not require a wipe. Re-run `install.sh` with the same CUDA line (pass `COMFYFLEET_INSTANCE_DIGEST` when you are moving a pin), then for each instance:

1. **Stop** (the instance must be stopped).
2. **Create** again with the same name: UI checkbox **Replace a stopped instance with the same name**, or CLI `comfyfleet create --force` with the same workflow, GPUs, launch flags, and CUDA line.
3. **Start**.

Create always passes `--shm-size 8g`. `start` and `restart` keep the existing container layers and the CUDA line stored at create. Changing cu130 versus cu124 is this recreate with the other tag. Host model, custom-node, and output directories stay.
