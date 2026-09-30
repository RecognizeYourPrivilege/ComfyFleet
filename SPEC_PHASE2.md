# ComfyFleet — Product SPEC (LOCKED) — Phase 2

**Status:** LOCKED — Phase 2 (2026-09-30): **iOS-like control web UI**  
**Product:** ComfyFleet — create many ComfyUI workflow containers; run few  
**Repo:** `RecognizeYourPrivilege/ComfyFleet`  
**Phase 1 base:** `main` @ `e9b95f6b` (image + `comfyfleet` CLI / `comfyfleet.control`)  
**Phase 1 SPEC:** `/workspace/comfyfleet/SPEC.md` (FR-W amend: operator workflow JSON required; no baked default)  
**Logo:** `/workspace/comfyfleet/comfyfleet-logo-ships.jpg`  
**Author:** SPEC  
**Date:** 2026-09-30  

**Phase remap (locked):** Phase **2** = control **web UI**. **Auth** = Phase **3** (stub only in this lock). This supersedes Phase 1 table labels that listed Auth as P2 and iOS as P3.

This SPEC is **requirements only**. SPEC does not build or open PRs.

---

## 1. Purpose

Ship an **iOS-like** control **web UI** on the fleet host so an operator can, from a phone or desktop browser:

1. **List** fleet instances (name, state, port, GPU, URL).  
2. **Create** an instance (upload/select **operator workflow JSON** — required; FR-W).  
3. **Start** / **Stop** instances without rebuild.  
4. **Open** a running instance (link to `http://<host>:<port>` / Comfy UI).  

All mutating/list actions **call existing `comfyfleet.control`** (same behavior as Phase 1 CLI) — UI is a client over that control surface, not a second lifecycle implementation.

**Not in Phase 2:** login / token Auth (Phase 3 stub only).

---

## 2. Surfaces / roles

| Surface | Role | Phase |
|---------|------|-------|
| **S-1 Image + CLI** | Docker image + `comfyfleet` CLI | P1 (shipped @ `e9b95f6b`) |
| **S-2 `comfyfleet.control`** | Programmatic create/start/stop/list (+ authorize stub) | P1; **required backend for P2** |
| **S-6 Control web UI** | iOS-like SPA/pages: list, create, start, stop, open | **P2 (this lock)** |
| **S-4 Auth gate** | Login / token before control actions | **P3** (stub only in P2) |

**Operator:** Uses phone/desktop browser on LAN to manage the fleet.  
**Host owner:** Serves the control UI (documented bind — localhost or LAN); Docker + NVIDIA stack from P1.

---

## 3. Functional requirements — Phase 2

### 3.1 Control backend contract

**FR-P2-01** UI **must** invoke **`comfyfleet.control`** (or a thin HTTP adapter that **only** wraps those same functions) for:

| Action | Behavior (parity with P1 CLI) |
|--------|-------------------------------|
| **list** | Instances with name, running/stopped, host port, GPU ids, open URL when running |
| **create** | Requires operator **workflow JSON** (file upload or host path the control layer can read); GPU selection; no stock/QualitySafe bake |
| **start** | Start without image rebuild |
| **stop** | Stop without destroy (unless separate delete is already in control — do not invent destroy if P1 has none) |
| **open** | Provide/copy/navigate to `http://<reachable-host>:<port>` for a running instance |

**FR-P2-02** Do **not** reimplement Docker lifecycle in FRONT JS. No duplicate mount/port/GPU logic in the browser beyond presenting control results/errors.

**FR-P2-03** Errors from control (missing workflow, no nvidia-smi, name collision, port fail) surface as clear UI messages.

**FR-P2-04** `authorize()` / Auth remains a **no-op stub** (or equivalent) in P2 — document that real Auth is Phase 3. Do not ship a fake “logged in” security story.

### 3.2 Create flow (UI)

**FR-P2-10** Create form **requires** workflow JSON (file picker and/or path field that control can consume). Submit without workflow **fails** in UI before/at control (same as CLI).

**FR-P2-11** Create includes **GPU ask**: show detected GPUs (from control/host probe); operator picks one or more when multi-GPU. Non-interactive defaults only if control already documents flags and UI exposes the same choices.

**FR-P2-12** On success, show new instance name (stem-derived), assigned port (when started or reserved per P1 metadata), and refresh list.

**FR-P2-13** Create may leave instance **stopped** (create many / run few) unless operator explicitly chooses “create & start” — if both modes exist, label them clearly.

### 3.3 List / start / stop / open

**FR-P2-20** List view: all known instances; running vs stopped visually distinct; show port + GPU summary.

**FR-P2-21** **Start** / **Stop** controls per instance; disabled/hidden when invalid (e.g. Start while running).

**FR-P2-22** **Open** opens the Comfy UI URL for a **running** instance in a new tab (or copies URL if popup blocked); disabled when stopped.

**FR-P2-23** Manual refresh and/or light auto-refresh of list while page open (NFR — do not hammer Docker).

### 3.4 Visual / UX

**FR-P2-30** **iOS-like** control chrome: dark + glass (or dark HIG-adjacent), large touch targets, portrait-first phone usable on LAN Safari.

**FR-P2-31** Ship / use **fleet logo** (`comfyfleet-logo-ships.jpg`) in header/branding.

**FR-P2-32** Not Material Design 3; not CueForge slate/cyan; not a raw unstyled dump of CLI JSON (JSON debug optional behind disclosure).

**FR-P2-33** Honest empty states: no instances; GPU probe failed; control backend unreachable.

### 3.5 Serving

**FR-P2-40** Document how the control UI is served (e.g. small host static + local API wrapping `comfyfleet.control`, or `comfyfleet ui` command). Same-origin or clearly documented CORS — prefer same host process.

**FR-P2-41** Phase 2 assumes **trusted LAN** (Auth Phase 3). README must say the UI is not an internet-exposed auth product yet.

---

## 4. Non-functional

**NFR-P2-01** Mobile Safari / iOS WebKit first-class for the control UI; desktop Chrome/Safari OK.  
**NFR-P2-02** Actions complete with visible pending state (create/start/stop can take seconds).  
**NFR-P2-03** No second source of truth for instance state — list reflects control/Docker.  
**NFR-P2-04** Phase 1 CLI remains fully usable without the UI.  
**NFR-P2-05** Logo and README Phase 2 section updated in repo when shipped.

---

## 5. Out of scope (Phase 2)

| ID | Out of scope |
|----|----------------|
| **OS-P2-01** | Real **Auth** / multi-user / tokens (Phase **3**) |
| **OS-P2-02** | Native Swift / TestFlight app (web UI only) |
| **OS-P2-03** | Editing Comfy graphs inside the control UI (Open → Comfy / ComfyDock) |
| **OS-P2-04** | Replacing Phase 1 image, mounts, or FR-W rules |
| **OS-P2-05** | Public internet hardening / reverse-proxy Auth |
| **OS-P2-06** | Auto-scaling / auto-stop on idle |

**Honest claim:** Phase 2 is an **iOS-like LAN control panel** over `comfyfleet.control` — create/start/stop/list/open — not a secured multi-user product.

---

## 6. Definition of Done — Phase 2

| # | Check |
|---|--------|
| **D2.1** | Control UI loads on phone-sized viewport with iOS-like dark(+glass) chrome + logo. |
| **D2.2** | **List** shows instances via `comfyfleet.control` (parity fields: name, state, port, GPU, URL when running). |
| **D2.3** | **Create** requires operator workflow JSON; refuses missing workflow; uses control create (no baked default). |
| **D2.4** | **Create** exposes GPU selection consistent with P1 multi-GPU ask. |
| **D2.5** | **Start** / **Stop** call control; list updates; no image rebuild. |
| **D2.6** | **Open** reaches running Comfy on published port. |
| **D2.7** | Auth is stub only; README states Phase 3 Auth and trusted-LAN warning. |
| **D2.8** | FRONT does not duplicate Docker lifecycle outside control wrapper. |
| **D2.9** | Tests or manual script covering control-wrapper happy path + missing-workflow fail; POLISH gate when RELEASED. |

---

## 7. Assumptions

| ID | Assumption |
|----|------------|
| **A-P2-01** | Phase 1 @ `e9b95f6b` (or later main) remains the lifecycle authority. |
| **A-P2-02** | `comfyfleet.control` (and `authorize` stub) stay the single mutation API Auth will wrap in P3. |
| **A-P2-03** | Operator can reach the host on LAN; file upload to host control process is acceptable for workflow JSON. |
| **A-P2-04** | FRONT owns UI; BACK owns any small HTTP adapter if control is Python-only today — coordinate contract in PR. |

---

## 8. OPEN

| ID | Question | Default if silent |
|----|----------|-------------------|
| **O-P2-01** | Exact URL path for UI (e.g. `:9100/` or `/fleet`) | Document one primary in README |
| **O-P2-02** | Host path browse vs upload-only for workflow | Upload required; optional path field if control runs as same user |
| **O-P2-03** | Delete/remove instance in P2 | Only if already in control; else later RELEASE |
| **O-P2-04** | Dark+glass vs dark-flat | Dark + glass preferred |

---

## 9. Lane recommendation

| Order | Lane | Why |
|-------|------|-----|
| **1** | **FRONT** | iOS-like UI: list/create/start/stop/open |
| **2** | **BACK** | HTTP adapter wrapping `comfyfleet.control` if needed; file upload endpoint |
| **3** | **POLISH** | Gate D2.* when SeraVale RELEASES |
| **—** | **SPEC** | HARD IDLE after this LOCKED post |

---

## 10. References

- Phase 1 SPEC: `/workspace/comfyfleet/SPEC.md`  
- Phase 1 main: `e9b95f6b`  
- Logo: `/workspace/comfyfleet/comfyfleet-logo-ships.jpg`  
- Repo: `RecognizeYourPrivilege/ComfyFleet`  

---

**CHECK-IN:** LOCKED SPEC path `/workspace/comfyfleet/SPEC_PHASE2.md` · **Phase 2 LOCKED** · CLEAR · SPEC HARD IDLE · Auth = Phase 3 stub.
