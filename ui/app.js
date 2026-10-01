/* ComfyFleet control UI. Calls the same-origin HTTP API only.
   Docker lifecycle stays in comfyfleet.control. */

const state = {
  busy: false,
  gpus: [],
  gpuError: "",
  selected: new Set(),
  timer: 0,
};

const banner = document.querySelector("#banner");
const toast = document.querySelector("#toast");
const list = document.querySelector("#list");
const empty = document.querySelector("#empty");
const updated = document.querySelector("#updated");
const sheet = document.querySelector("#sheet");
const sheetBanner = document.querySelector("#sheet-banner");
const gpuRow = document.querySelector("#gpus");
const gpuNote = document.querySelector("#gpu-note");
const fileInput = document.querySelector("#workflow-file");
const fileName = document.querySelector("#file-name");
const pathInput = document.querySelector("#workflow-path");
const forceInput = document.querySelector("#force");
const reserveInput = document.querySelector("#reserve-vram");
const headroomInput = document.querySelector("#vram-headroom");
const previewMethodInput = document.querySelector("#preview-method");
const previewSizeInput = document.querySelector("#preview-size");
const extraArgsInput = document.querySelector("#extra-args");

document.querySelector("#refresh").addEventListener("click", () => refresh());
document.querySelector("#logout").addEventListener("click", () => logout());
document.querySelector("#open-create").addEventListener("click", openSheet);
document.querySelector("#create-stopped").addEventListener("click", () => submitCreate(false));
document.querySelector("#create-start").addEventListener("click", () => submitCreate(true));
fileInput.addEventListener("change", () => {
  const file = fileInput.files && fileInput.files[0];
  fileName.textContent = file ? file.name : "No file chosen";
});
sheet.addEventListener("click", (event) => {
  if (event.target.closest("[data-close]")) closeSheet();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !sheet.hidden) closeSheet();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

refresh();
state.timer = window.setInterval(() => {
  if (!state.busy && sheet.hidden && !document.hidden) refresh();
}, 10000);

async function refresh() {
  if (state.busy) return;
  const health = await call("/api/health");
  if (health.unreachable) {
    showBanner("Control backend unreachable. This page only talks to the ComfyFleet API on this host.");
    updated.textContent = "Not connected";
    return;
  }
  const gpus = await call("/api/gpus");
  if (gpus.sessionExpired) return;
  const instances = await call("/api/instances");
  if (instances.sessionExpired) return;
  if (isAuthFailure(gpus) || isAuthFailure(instances)) return;
  if (gpus.ok && Array.isArray(gpus.payload.gpus)) {
    state.gpus = gpus.payload.gpus;
    state.gpuError = "";
  } else {
    state.gpus = [];
    state.selected.clear();
    state.gpuError = gpus.error || "GPU probe failed.";
  }
  const messages = [];
  if (!gpus.ok) messages.push(`GPU probe failed. ${gpus.error}`);
  if (!instances.ok) messages.push(instances.error);
  if (messages.length) showBanner(messages.join(" "));
  else hide(banner);
  renderGpus();
  renderList(instances.ok ? instances.payload.instances : []);
  const clock = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  updated.textContent = `Updated ${clock}`;
}

function renderList(instances) {
  list.replaceChildren();
  const rows = instances || [];
  empty.hidden = rows.length !== 0;
  for (const instance of rows) {
    list.append(instanceCard(instance));
  }
}

function isRunning(instance) {
  return instance.status === "running";
}

function openTarget(instance) {
  if (!isRunning(instance) || typeof instance.url !== "string" || !instance.url) return null;
  return instance.url;
}

function instanceCard(instance) {
  const running = isRunning(instance);
  const url = openTarget(instance);
  const card = el("article", { className: "card glass" });
  const top = el("div", { className: "card-top" });
  top.append(el("h2", { text: instance.name }));
  const pill = el("span", {
    className: `pill ${running ? "running" : "stopped"}`,
    text: running ? "Running" : "Stopped",
  });
  top.append(pill);
  card.append(top);
  const gpuText = (instance.gpus || []).join(", ") || "none";
  card.append(el("p", {
    className: "meta",
    text: `Port ${instance.port} · GPU ${gpuText} · ${instance.status}`,
  }));
  const launchArgv = instance.launch && instance.launch.argv;
  if (Array.isArray(launchArgv) && launchArgv.length) {
    card.append(el("p", {
      className: "meta",
      text: `Comfy --listen 0.0.0.0 --port 8188 ${launchArgv.join(" ")}`,
    }));
  }
  if (url) {
    card.append(el("p", { className: "url-line", text: url }));
  }
  const actions = el("div", { className: "actions" });
  const start = el("button", { className: "btn secondary", type: "button", text: "Start" });
  start.disabled = Boolean(running);
  start.addEventListener("click", () => mutate(instance.name, "start", start, "Starting…"));
  const stop = el("button", { className: "btn secondary", type: "button", text: "Stop" });
  stop.disabled = !running;
  stop.addEventListener("click", () => mutate(instance.name, "stop", stop, "Stopping…"));
  const open = el("button", { className: "btn primary", type: "button", text: "Open" });
  open.disabled = !url;
  open.addEventListener("click", () => openInstance(url));
  const copy = el("button", { className: "btn secondary", type: "button", text: "Copy URL" });
  copy.disabled = !url;
  copy.addEventListener("click", () => copyUrl(url));
  actions.append(start, stop, open, copy);
  card.append(actions);
  const details = el("details");
  details.append(el("summary", { text: "Details" }));
  details.append(el("pre", { text: JSON.stringify(instance, null, 2) }));
  card.append(details);
  return card;
}

async function mutate(name, action, button, pending) {
  const previous = button.textContent;
  state.busy = true;
  button.disabled = true;
  button.textContent = pending;
  const result = await call(`/api/instances/${encodeURIComponent(name)}/${action}`, { method: "POST" });
  state.busy = false;
  button.textContent = previous;
  if (!result.ok) {
    showBanner(result.error);
    await refresh();
    return;
  }
  const warning = result.payload.warning;
  const instance = result.payload.instance;
  showToast(warning ? `${actionLabel(action, instance)} ${warning}` : actionLabel(action, instance));
  hide(banner);
  await refresh();
}

function actionLabel(action, instance) {
  if (!instance) return action;
  if (action === "start") return `Started ${instance.name} on port ${instance.port}.`;
  return `Stopped ${instance.name}.`;
}

function openInstance(url) {
  if (!url) return;
  const opened = window.open(url, "_blank", "noopener");
  if (!opened) copyUrl(url);
}

async function copyUrl(url) {
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    showToast(`Copied ${url}`);
  } catch {
    showToast(url);
  }
}

function openSheet() {
  hide(sheetBanner);
  renderGpus();
  sheet.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeSheet() {
  sheet.hidden = true;
  document.body.style.overflow = "";
}

function renderGpus() {
  gpuRow.replaceChildren();
  if (state.gpuError) {
    gpuNote.textContent = state.gpuError;
    return;
  }
  if (!state.gpus.length) {
    gpuNote.textContent = "No GPUs reported yet.";
    return;
  }
  if (state.gpus.length === 1) state.selected.add(String(state.gpus[0].index));
  const known = new Set(state.gpus.map((gpu) => String(gpu.index)));
  for (const index of [...state.selected]) {
    if (!known.has(index)) state.selected.delete(index);
  }
  for (const gpu of state.gpus) {
    const index = String(gpu.index);
    const button = el("button", {
      className: "gpu",
      type: "button",
      role: "checkbox",
    });
    button.setAttribute("aria-checked", state.selected.has(index) ? "true" : "false");
    button.dataset.index = index;
    button.append(document.createTextNode(`GPU ${gpu.index}`));
    const memory = [gpu.name, gpu.memory].filter(Boolean).join(" · ");
    if (memory) button.append(el("small", { text: memory }));
    button.addEventListener("click", () => {
      if (state.selected.has(index)) state.selected.delete(index);
      else state.selected.add(index);
      button.setAttribute("aria-checked", state.selected.has(index) ? "true" : "false");
    });
    gpuRow.append(button);
  }
  gpuNote.textContent = state.gpus.length > 1
    ? "Choose one or more GPUs. Nothing is assumed on a multi-GPU host."
    : "Confirm the GPU for this instance.";
}

async function submitCreate(start) {
  if (state.busy) return;
  const file = fileInput.files && fileInput.files[0];
  const workflowPath = pathInput.value.trim();
  if (!file && !workflowPath) {
    showSheetError("A workflow JSON file is required. There is no built-in default.");
    return;
  }
  if (file && workflowPath) {
    showSheetError("Upload a workflow file or enter a host path, not both.");
    return;
  }
  if (file && !file.name.toLowerCase().endsWith(".json")) {
    showSheetError("Workflow must be a .json file.");
    return;
  }
  const chosen = [...state.selected];
  if (!chosen.length) {
    showSheetError(state.gpuError || "Select at least one GPU.");
    return;
  }
  const launch = readLaunch();
  const conflict = launchConflict(launch);
  if (conflict) {
    showSheetError(conflict);
    return;
  }
  const body = new FormData();
  if (file) body.append("workflow", file, file.name);
  if (workflowPath) body.append("workflow_path", workflowPath);
  body.append("gpus", chosen.join(","));
  body.append("start", start ? "true" : "false");
  body.append("force", forceInput.checked ? "true" : "false");
  body.append("vram", launch.vram);
  body.append("attention", launch.attention);
  body.append("flags", launch.flags.join(","));
  body.append("reserve_vram", reserveInput.value.trim());
  body.append("vram_headroom", headroomInput.value.trim());
  body.append("preview_method", previewMethodInput.value);
  body.append("preview_size", previewSizeInput.value.trim());
  body.append("extra_args", extraArgsInput.value.trim());
  state.busy = true;
  setCreatePending(true, start);
  const result = await call("/api/instances", { method: "POST", body });
  state.busy = false;
  setCreatePending(false, start);
  if (!result.ok) {
    showSheetError(result.error);
    return;
  }
  const instance = result.payload.instance;
  const mode = result.payload.started ? "Created and started" : "Created (not started)";
  const warning = result.payload.warning ? ` ${result.payload.warning}` : "";
  fileInput.value = "";
  fileName.textContent = "No file chosen";
  pathInput.value = "";
  forceInput.checked = false;
  resetLaunch();
  closeSheet();
  showToast(`${mode}: ${instance.name} · port ${instance.port}.${warning}`);
  hide(banner);
  await refresh();
}

function readLaunch() {
  const vram = document.querySelector('input[name="vram"]:checked');
  const attention = document.querySelector('input[name="attention"]:checked');
  const flags = [...document.querySelectorAll('input[name="flag"]:checked')];
  return {
    vram: vram ? vram.value : "",
    attention: attention ? attention.value : "",
    flags: flags.map((node) => node.value),
    nodes: flags,
  };
}

function launchConflict(launch) {
  const buckets = new Map();
  function add(group, flag) {
    if (!group || !flag) return;
    const list = buckets.get(group) || [];
    list.push(flag);
    buckets.set(group, list);
  }
  add("vram", launch.vram);
  for (const node of launch.nodes) add(node.dataset.exclusive || "", node.value);
  for (const flags of buckets.values()) {
    if (flags.length > 1) {
      return `${flags.join(", ")} cannot be combined. ComfyUI accepts only one of that group.`;
    }
  }
  return "";
}

function resetLaunch() {
  const vram = document.querySelector('input[name="vram"][value=""]');
  const attention = document.querySelector('input[name="attention"][value=""]');
  if (vram) vram.checked = true;
  if (attention) attention.checked = true;
  for (const node of document.querySelectorAll('input[name="flag"]')) node.checked = false;
  reserveInput.value = "";
  headroomInput.value = "";
  previewMethodInput.value = "";
  previewSizeInput.value = "";
  extraArgsInput.value = "";
}

function setCreatePending(pending, start) {
  const stopped = document.querySelector("#create-stopped");
  const started = document.querySelector("#create-start");
  stopped.disabled = pending;
  started.disabled = pending;
  if (!pending) {
    stopped.textContent = "Create";
    started.textContent = "Create & start";
    return;
  }
  if (start) started.textContent = "Creating…";
  else stopped.textContent = "Creating…";
}

function isAuthFailure(result) {
  if (!result || result.sessionExpired || result.status === 401) return true;
  const error = (result.error || "").toLowerCase();
  return error === "unauthorized" || error === "session expired";
}

async function logout() {
  state.busy = true;
  try {
    await fetch("/api/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  } catch {
    /* still leave the fleet page */
  }
  window.location.assign("/login");
}

async function call(path, options) {
  try {
    const response = await fetch(path, {
      method: (options && options.method) || "GET",
      body: options && options.body,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    let payload = null;
    try { payload = await response.json(); } catch { payload = null; }
    if (response.status === 401) {
      const expired = payload && payload.error === "session expired";
      window.location.assign(expired ? "/login?expired=1" : "/login");
      return {
        ok: false,
        status: 401,
        sessionExpired: true,
        error: expired ? "session expired" : "unauthorized",
      };
    }
    if (!response.ok || !payload || payload.ok === false) {
      return {
        ok: false,
        status: response.status,
        error: (payload && payload.error) || `Request failed (${response.status}).`,
      };
    }
    return { ok: true, status: response.status, payload };
  } catch {
    return { ok: false, unreachable: true, error: "Control backend unreachable." };
  }
}

function showBanner(text) {
  banner.hidden = false;
  banner.textContent = text;
}

function showSheetError(text) {
  sheetBanner.hidden = false;
  sheetBanner.textContent = text;
}

function showToast(text) {
  toast.hidden = false;
  toast.textContent = text;
}

function hide(node) {
  node.hidden = true;
  node.textContent = "";
}

function el(tag, attrs) {
  const node = document.createElement(tag);
  if (!attrs) return node;
  if (attrs.className) node.className = attrs.className;
  if (attrs.type) node.type = attrs.type;
  if (attrs.role) node.setAttribute("role", attrs.role);
  if (attrs.text) node.textContent = attrs.text;
  return node;
}
