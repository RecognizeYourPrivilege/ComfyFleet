# ComfyFleet — Product SPEC (LOCKED) — Phase 1

**Status:** LOCKED — Phase 1 + **FR-W amend** (2026-09-30): operator-supplied workflow JSON required at create; **no** baked stock/QualitySafe default in image  
**Product:** ComfyFleet — create **many** ComfyUI workflow Docker containers; **run few** so GPUs are not overloaded  
**Repo:** `RecognizeYourPrivilege/ComfyFleet` (public; create / SCM grant in flight at lock)  
**Logo:** ships fleet — `/workspace/comfyfleet-logo-ships.jpg` (ship in repo when accessible)  
**Author:** SPEC  
**Date:** 2026-09-30  
**Scope of this lock:** Phase 1 container fleet runtime + create/start/stop CLI (or equivalent host tool). Auth + iOS control UI = Phase 2–3 (stub interfaces only here).

This SPEC is **requirements only**. SPEC does not build, open PRs, or decide product direction beyond the locked brief.

---

## CHANGE LOG — FR-W amend (2026-09-30)

| ID | Change | Reason |
|----|--------|--------|
| CL-W1 | **FR-W***: operator **must** supply workflow JSON at **create** (required arg/path) | Human / SeraVale RELEASE |
| CL-W2 | That supplied file is the instance **default** on every start/reboot | Same |
| CL-W3 | Image must **not** bake a stock / QualitySafe / other default workflow JSON | Same |

---

## 1. Purpose

Ship a host-side tool + Docker image so an operator can:

1. **Create** many named ComfyUI **workflow containers** (operator supplies the workflow JSON at create; that file becomes the instance default — **not** a baked stock workflow in the image).
2. **Start only a few** at a time (GPU budget), **stop** others without rebuild — “create many / run few.”
3. Mount shared models and per-instance custom_nodes + files from predictable host paths.
4. Bind the next free host port from **8188** upward; Comfy listens on **`0.0.0.0`** inside the container.
5. Detect GPUs via **`nvidia-smi`** and ask which GPU(s) to attach at create (multi-GPU prompt).

Positioning: not a cloud SaaS; local/LAN Docker fleet for multi-workflow Comfy hosts.

---

## 2. Surfaces / roles

| Surface | Role | Phase |
|---------|------|-------|
| **S-1 Image** | Slim Debian + CUDA **12.4** runtime; Torch wheel matched to 12.4; ComfyUI + baked nodes | P1 |
| **S-2 Host control** | Create / start / stop / list / port / GPU assign — CLI or thin host tool | P1 |
| **S-3 Host mounts** | `/home/ComfyFleet/models`, `/home/ComfyFleet/custom_nodes_<name>`, `/home/ComfyFleet/files/<name>/{input,output,temp}` | P1 |
| **S-4 Auth gate** | Login / token before control actions | P2 (stub only in P1) |
| **S-5 iOS control UI** | Phone UI to create/start/stop fleet | P3 (stub only in P1) |

**Roles**

- **Operator:** Creates many instances, starts/stops few, picks GPU(s), opens `http://<host>:<port>`.
- **Host owner:** Prepares `/home/ComfyFleet/models` and per-name dirs under `/home/ComfyFleet`; installs Docker + NVIDIA Container Toolkit.

---

## 3. Functional requirements — Phase 1

### 3.1 Image

**FR-I1** Base = **slim Debian** + **NVIDIA CUDA 12.4 runtime** (not full devel unless build stage needs it; runtime image stays slim).

**FR-I2** **PyTorch** wheel installed must be the build that matches **CUDA 12.4** (document exact wheel/index in README).

**FR-I3** Image includes a current mainstream **ComfyUI** install usable with GPU.

**FR-I4 Bake into image (required):**

| Component | Requirement |
|-----------|-------------|
| **ComfyUI-Manager** | Present + **git** available for Manager/update flows |
| **pixaroma** | **Full** pixaroma custom-node set (as published for ComfyUI; document upstream pin/commit or tag) |
| **ComfyUI-ComfyDock** | `RecognizeYourPrivilege/ComfyUI-ComfyDock` installed as custom node / served per that package’s install shape |

**FR-I5** Image build is reproducible enough to document: Dockerfile (+ optional compose) in repo; pin or document ComfyUI / node refs.

### 3.2 Host mounts

**FR-M1** On create/start, bind-mount:

| Host path | Container path (Comfy convention) |
|-----------|-----------------------------------|
| `/home/ComfyFleet/models` | Comfy **models** root (shared across instances) |
| `/home/ComfyFleet/custom_nodes_<name>` | Comfy **custom_nodes** for that instance |
| `/home/ComfyFleet/files/<name>/input` | Comfy **input** |
| `/home/ComfyFleet/files/<name>/output` | Comfy **output** |
| `/home/ComfyFleet/files/<name>/temp` | Comfy **temp** |

**FR-M2** `<name>` = instance name derived from workflow stem (see FR-N*). Tool creates missing host dirs on create (or fails with a clear message listing required paths).

**FR-M3** Shared `/home/ComfyFleet/models` is read-write unless operator configures otherwise; document that concurrent writers can conflict. Host fleet data lives only under `/home/ComfyFleet`.

### 3.3 Naming

**FR-N1** **Container name** = stem of the default workflow JSON filename (e.g. `my_flow.json` → `my_flow`).

**FR-N2** If stem is longer than **~63** Docker-safe chars, or contains characters illegal/ugly for Docker names, **shorten/sanitize** deterministically (document algorithm: lowercase, `[a-z0-9_-]`, truncate with stable hash suffix if needed).

**FR-N3** Name collision on create → refuse with clear error (or documented `--force` rebuild path); do not silently overwrite a running container.

### 3.4 Default workflow (operator-supplied — no baked default)

**FR-W1** **Create requires** an operator-supplied **workflow JSON** path (required CLI arg / flag, e.g. `--workflow /path/to/flow.json`). Create **fails** if missing, unreadable, or not JSON.

**FR-W2** That supplied file is copied into the instance and becomes the **only** default workflow for that container. It **loads on every start/reboot** of that instance (entrypoint or Comfy flag / drop-in path — implementation choice; behavior is mandatory).

**FR-W3** **Container / instance name** derives from that file’s stem (FR-N*).

**FR-W4** Workflow file remains editable on the host under the instance’s documented mount after create.

**FR-W5** The **image must not** bake a stock, QualitySafe, sample, or other default workflow JSON used as a fallback when the operator omits `--workflow`. No silent substitute workflow.

### 3.5 Networking

**FR-P1** Host port = **auto next free** starting at **8188**, then 8189, 8190, … (skip in-use).

**FR-P2** Comfy process runs with **`--listen 0.0.0.0`** (reachable on LAN via published port).

**FR-P3** `list` / status shows instance → host port mapping.

### 3.6 GPU

**FR-G1** Host must have working **`nvidia-smi`**. If missing/failing, create/start **aborts** with a clear message (do not pretend CPU-only is Phase 1 success unless human unlocks later).

**FR-G2** At **create**, detect GPUs via `nvidia-smi` and **ask which GPU(s)** to attach (interactive prompt; non-interactive flag allowed if documented, e.g. `--gpu 0` / `--gpus 0,1`).

**FR-G3** **Multi-GPU:** if more than one GPU exists, create flow **must ask** which to use (single or multiple) before finishing create.

**FR-G4** Start attaches the instance to the GPUs chosen at create (stored in instance metadata). Changing GPU set may require recreate or an explicit documented update command (OPEN O-02).

### 3.7 Create many / run few

**FR-C1** Operator can **create** N instances without starting all of them.

**FR-C2** **Start** / **stop** (and optionally restart) without **rebuilding** the image.

**FR-C3** Stopped containers keep mounts + default workflow; start resumes same name/port policy (port: prefer recorded port if free, else next free — document).

**FR-C4** Running count is operator-controlled; tool may **warn** when starting would exceed a configured max concurrent (optional NFR); must not auto-start all on create.

### 3.8 Updates (document)

**FR-U1** README documents both update paths:

1. **Rebuild image** and recreate/replace containers, and/or  
2. **In-container `git pull`** (ComfyUI / custom nodes) where safe.

**FR-U2** Do not claim zero-downtime rolling updates in Phase 1.

### 3.9 Phase 2–3 stubs only

**FR-S1** SPEC / README may name future **Auth** (Phase 2) and **iOS control UI** (Phase 3) interfaces (e.g. “control API shape TBD”) — **no** implementation required in Phase 1 DoD.

**FR-S2** Phase 1 must not block later Auth by hardcoding irreversible “open LAN with no upgrade path” — prefer a single control entrypoint that Auth can wrap later (assumption A-07).

---

## 4. Non-functional

**NFR-01** Image stays **slim** relative to full CUDA devel + desktop stacks; no unnecessary GUI packages.

**NFR-02** Create/start/stop feedback is clear on failure (GPU, port, mount, name).

**NFR-03** Concurrent running instances must not bind the same host port.

**NFR-04** Document NVIDIA Container Toolkit / `--gpus` (or equivalent) requirements.

**NFR-05** Logo asset ships in repo (`comfyfleet-logo-ships.jpg` or branded derivative) for README/UI later.

**NFR-06** Public repo README: install host prereqs, build image, create/start/stop examples, mount layout, update paths, honest Phase 2–3 deferrals.

---

## 5. Out of scope (Phase 1)

| ID | Out of scope |
|----|----------------|
| **OS-01** | Auth / multi-user accounts (Phase 2) |
| **OS-02** | iOS / mobile control UI (Phase 3) |
| **OS-03** | Kubernetes / swarm orchestration |
| **OS-04** | Automatic load-balancing across GPUs or auto-stop on idle (unless later RELEASE) |
| **OS-05** | Baking full model weights into the image (models live on `/home/ComfyFleet/models`) |
| **OS-06** | Guaranteeing every custom node works offline without host network on first Manager use |
| **OS-07** | Windows-native non-Docker host path (Docker Desktop may work; not primary DoD) |
| **OS-08** | Replacing ComfyUI’s own graph editor UX |
| **OS-09** | Baking a stock / QualitySafe / sample default workflow into the image as create fallback |

**Honest claim:** Phase 1 is a **GPU-aware Docker fleet** for many workflow instances with shared models and few concurrent runners — not a finished phone app or auth product.

---

## 6. Definition of Done — Phase 1

| # | Check |
|---|--------|
| **D1.1** | Image builds from repo Dockerfile: slim Debian + **CUDA 12.4** runtime; Torch matches 12.4. |
| **D1.2** | Image contains ComfyUI-Manager (+ git), **full pixaroma**, and **ComfyUI-ComfyDock**. |
| **D1.3** | Create **requires** operator workflow JSON; produces a container named from that stem (sanitized); mounts FR-M1 present. Create without workflow arg **fails**. |
| **D1.4** | The **operator-supplied** default workflow loads on every container start/reboot. Image has **no** baked stock/QualitySafe default workflow. |
| **D1.5** | Published port is next free from **8188**; Comfy listens **`0.0.0.0`**; reachable on host port. |
| **D1.6** | Without `nvidia-smi`, create/start fails clearly. With GPUs, create asks which GPU(s); multi-GPU hosts get an explicit prompt. |
| **D1.7** | Create ≥2 instances; start one; stop it; start the other — **no image rebuild** between start/stop. |
| **D1.8** | README documents rebuild-image **and** in-container git pull update paths; Phase 2–3 deferred honestly. |
| **D1.9** | Logo asset present in repo (or linked from lock path until first commit). |
| **D1.10** | POLISH gate (when RELEASED) verifies D1.1–D1.9 against PR/main; findings only. |

---

## 7. Assumptions

| ID | Assumption |
|----|------------|
| **A-01** | Host is Linux with Docker + NVIDIA Container Toolkit; `nvidia-smi` works on the host. |
| **A-02** | Operator can write under `/home/ComfyFleet/models`, `/home/ComfyFleet/custom_nodes_*`, `/home/ComfyFleet/files/*` (or configures equivalent documented overrides later). |
| **A-03** | “Full pixaroma” means the complete public pixaroma ComfyUI node distribution known to the fleet; pin in Dockerfile when BACK/COMFY implement. |
| **A-04** | ComfyUI-ComfyDock install follows that repo’s documented custom_node / route shape. |
| **A-05** | Repo `RecognizeYourPrivilege/ComfyFleet` is created by human/SeraVale/SCM — SPEC writes workspace SPEC first; pushes when credentials exist. |
| **A-06** | Phase 1 control surface may be CLI-first; a local HTTP control stub is optional, not required. |
| **A-07** | Future Auth (P2) can wrap the same create/start/stop operations without redesigning mounts. |

---

## 8. OPEN (escalate to SeraVale / human)

| ID | Question | Default if silent |
|----|----------|-------------------|
| **O-01** | Exact ComfyUI git tag/commit + pixaroma pin | “Current stable at implement time”; pin in PR |
| **O-02** | Change GPU assignment without full recreate | Recreate instance; optional later command |
| **O-03** | Default max concurrent running warning threshold | Warn at > number of GPUs; never hard-block unless configured |
| **O-04** | Override base mount root instead of `/home/ComfyFleet/...` | `/home/ComfyFleet/...` is the host root; override flag later |
| **O-05** | Compose vs plain `docker run` wrapper | Either OK if DoD met; document one primary path |

---

## 9. Lane recommendation

| Order | Lane | Why |
|-------|------|-----|
| **1** | **BACK** | Dockerfile, mounts, GPU flags, port allocator, create/start/stop, entrypoint default-workflow load |
| **2** | **FRONT** | Optional thin status UI later; P1 may be CLI-only — wait for RELEASE |
| **3** | **COMFY** (on demand) | Comfy flags, Manager/pixaroma/ComfyDock bake verify, workflow default load path |
| **4** | **POLISH** | Gate D1.* when SeraVale RELEASES |
| **—** | **SPEC** | HARD IDLE after this LOCKED post unless amended |

**Suggested sequence:** SPEC LOCKED (this doc) → SeraVale RELEASE BACK (image + control) → optional COMFY consult → POLISH → merge under standing process.

---

## 10. References

- Logo: `/workspace/comfyfleet-logo-ships.jpg`  
- Related: `RecognizeYourPrivilege/ComfyUI-ComfyDock`  
- Upstream: `comfyanonymous/ComfyUI`, ComfyUI-Manager, pixaroma  
- Workspace SPEC (authoritative until repo sync): `/workspace/comfyfleet/SPEC.md`

---

**CHECK-IN:** LOCKED SPEC path `/workspace/comfyfleet/SPEC.md` · **Phase 1 + FR-W amend LOCKED** · CLEAR · SPEC HARD IDLE · repo push when SCM accessible · no FRONT/BACK coding from SPEC.
