/* ComfyFleet control UI. Calls the same-origin HTTP API only.
   Docker lifecycle stays in comfyfleet.control. */

const state = {
  busy: false,
  gpus: [],
  gpuError: "",
  selected: new Set(),
  drafts: new Map(),
  openEditors: new Set(),
  listSignature: "",
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
const instanceNameInput = document.querySelector("#instance-name");
const forceInput = document.querySelector("#force");
const instanceImageInput = document.querySelector("#instance-image");
const reserveInput = document.querySelector("#reserve-vram");
const headroomInput = document.querySelector("#vram-headroom");
const previewMethodInput = document.querySelector("#preview-method");
const previewSizeInput = document.querySelector("#preview-size");
const extraArgsInput = document.querySelector("#extra-args");
const gitUrlsInput = document.querySelector("#custom-node-git-urls");
const zipInput = document.querySelector("#custom-nodes-zip");
const zipName = document.querySelector("#zip-name");
const zipPacks = document.querySelector("#zip-packs");
const installMissingInput = document.querySelector("#install-missing-from-workflow");
const flagsDisclosure = document.querySelector("#comfy-flags");

const hostButton = document.querySelector("#host-menu-button");
const hostMenu = document.querySelector("#host-menu");

function setHostMenu(open) {
  hostMenu.hidden = !open;
  hostButton.setAttribute("aria-expanded", open ? "true" : "false");
}

hostButton.addEventListener("click", (event) => {
  event.stopPropagation();
  setHostMenu(hostMenu.hidden);
});
document.addEventListener("click", (event) => {
  if (!hostMenu.hidden && !event.target.closest(".host-menu")) setHostMenu(false);
});
document.querySelector("#fix-owner").addEventListener("click", () => {
  setHostMenu(false);
  fixOwnership();
});
document.querySelector("#prune-dangling").addEventListener("click", () => {
  setHostMenu(false);
  pruneDangling();
});

document.querySelector("#refresh").addEventListener("click", () => refresh());
document.querySelector("#logout").addEventListener("click", () => logout());
document.querySelector("#open-create").addEventListener("click", openSheet);
document.querySelector("#create-stopped").addEventListener("click", () => submitCreate(false));
document.querySelector("#create-start").addEventListener("click", () => submitCreate(true));
fileInput.addEventListener("change", () => {
  const file = fileInput.files && fileInput.files[0];
  fileName.textContent = file ? file.name : "No file chosen";
});
zipInput.addEventListener("change", () => {
  renderZipPacks();
});
sheet.addEventListener("click", (event) => {
  if (event.target.closest("[data-close]")) closeSheet();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!hostMenu.hidden) setHostMenu(false);
  if (!sheet.hidden) closeSheet();
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
  const rows = instances || [];
  const signature = JSON.stringify(rows);
  // Polling hits this every 10s. Rebuilding the cards remounts the flags
  // panel (so it collapses) and resets window scroll. Skip that when the
  // fleet snapshot is unchanged, and restore scroll if a real change lands.
  if (signature === state.listSignature && list.childElementCount === rows.length) return;
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  const editorScroll = new Map();
  for (const editor of list.querySelectorAll(".flag-editor-body")) {
    const panel = editor.closest(".flag-editor");
    if (panel && panel.id) editorScroll.set(panel.id, editor.scrollTop);
  }
  const openDetails = new Set();
  for (const node of list.querySelectorAll("article")) {
    const heading = node.querySelector("h2");
    const details = node.querySelector("details");
    if (heading && details && details.open) openDetails.add(heading.textContent);
  }
  state.listSignature = signature;
  list.replaceChildren();
  empty.hidden = rows.length !== 0;
  for (const instance of rows) {
    list.append(instanceCard(instance));
  }
  for (const node of list.querySelectorAll("article")) {
    const heading = node.querySelector("h2");
    const details = node.querySelector("details");
    if (heading && details && openDetails.has(heading.textContent)) details.open = true;
  }
  window.scrollTo(scrollX, scrollY);
  for (const [id, top] of editorScroll) {
    const panel = document.getElementById(id);
    const body = panel && panel.querySelector(".flag-editor-body");
    if (body) body.scrollTop = top;
  }
}

function isRunning(instance) {
  return instance.status === "running";
}

function selectedCudaTag() {
  const picked = document.querySelector('input[name="cuda-tag"]:checked');
  return picked ? picked.value : "cu130";
}

function openTarget(instance) {
  if (!isRunning(instance)) return null;
  const port = Number(instance.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  const hostname = window.location.hostname;
  if (!hostname) return null;
  const host = hostname.indexOf(":") === -1 ? hostname : "[" + hostname + "]";
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  return protocol + "//" + host + ":" + String(port) + "/";
}

function instanceCard(instance) {
  const running = isRunning(instance);
  const url = openTarget(instance);
  const card = el("article", { className: "card glass" });
  const top = el("div", { className: "card-top" });
  top.append(el("h2", { text: instance.name }));
  const trailing = el("div", { className: "card-trailing" });
  const pill = el("span", {
    className: `pill ${running ? "running" : "stopped"}`,
    text: running ? "Running" : "Stopped",
  });
  trailing.append(pill);
  top.append(trailing);
  card.append(top);
  const gpuText = (instance.gpus || []).join(", ") || "none";
  const cudaText = instance.cuda_tag === "cu130" || instance.cuda_tag === "cu124"
    ? instance.cuda_tag
    : (instance.image || "unknown");
  card.append(el("p", {
    className: "meta",
    text: `Port ${instance.port} · GPU ${gpuText} · CUDA ${cudaText} · ${instance.status}`,
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
  const actions = el("div", { className: "icon-actions" });
  actions.setAttribute("role", "group");
  actions.setAttribute("aria-label", `Actions for ${instance.name}`);
  const start = actionButton("Start", "Start", playIcon(), "green");
  start.disabled = Boolean(running);
  start.addEventListener("click", () => mutate(instance.name, "start", start, "Starting…"));
  const stop = actionButton("Stop", "Stop", stopIcon(), "gray");
  stop.disabled = !running;
  stop.addEventListener("click", () => mutate(instance.name, "stop", stop, "Stopping…"));
  const kill = actionButton("Force stop", "Force", forceStopIcon(), "orange");
  kill.disabled = !running;
  kill.addEventListener("click", () => mutate(instance.name, "force-stop", kill, "Killing…"));
  const open = actionButton("Open", "Shell", terminalIcon(), "blue");
  open.disabled = !running;
  open.addEventListener("click", () => openTerminal(instance.name));
  const comfy = actionButton("Open Comfy", "Comfy", comfyIcon(), "blue");
  comfy.disabled = !url;
  comfy.addEventListener("click", () => openInstance(url));
  const editor = instanceFlagEditor(instance);
  editor.id = `flags-${instance.name}`;
  const editing = state.openEditors.has(instance.name);
  editor.hidden = !editing;
  const edit = actionButton("Edit flags", "Flags", pencilIcon(), "yellow");
  edit.setAttribute("aria-expanded", editing ? "true" : "false");
  edit.setAttribute("aria-controls", editor.id);
  if (editing) edit.classList.add("on");
  edit.addEventListener("click", () => toggleFlagEditor(instance.name, edit, editor));
  const remove = actionButton(`Delete ${instance.name}`, "Delete", trashIcon(), "red");
  remove.addEventListener("click", () => confirmDelete(instance.name, remove));
  actions.append(start, stop, kill, open, comfy, edit, remove);
  card.append(actions);
  card.append(editor);
  const details = el("details");
  details.append(el("summary", { text: "Details" }));
  details.append(el("pre", { text: JSON.stringify(instance, null, 2) }));
  card.append(details);
  return card;
}

async function mutate(name, action, button, pending) {
  const icon = button.querySelector("svg");
  const caption = button.querySelector(".action-caption");
  const previous = icon ? button.getAttribute("aria-label") : button.textContent;
  const previousCaption = caption ? caption.textContent : "";
  state.busy = true;
  button.disabled = true;
  if (icon) button.setAttribute("aria-label", pending);
  else button.textContent = pending;
  if (caption) caption.textContent = pending;
  const result = await call(`/api/instances/${encodeURIComponent(name)}/${action}`, { method: "POST" });
  state.busy = false;
  if (icon) button.setAttribute("aria-label", previous);
  else button.textContent = previous;
  if (caption) caption.textContent = previousCaption;
  if (!result.ok) {
    showBanner(result.error);
    await refresh();
    return;
  }
  if (action === "delete") {
    showToast(`Deleted ${name}.`);
    hide(banner);
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
  if (action === "force-stop") return `Force-stopped ${instance.name}.`;
  return `Stopped ${instance.name}.`;
}

function openTerminal(name) {
  const page = `/terminal.html?name=${encodeURIComponent(name)}`;
  const opened = window.open(page, "_blank", "noopener");
  if (!opened) showToast(page);
}

async function confirmDelete(name, button) {
  const yes = await askConfirm(
    `Delete ${name}? This force-stops and removes only that instance container, and drops its fleet record. Other containers are not touched. Host files for this instance are kept.`
  );
  if (!yes) return;
  await mutate(name, "delete", button, "Deleting…");
}

function askConfirm(text, options) {
  const sheet = document.querySelector("#confirm");
  const message = document.querySelector("#confirm-text");
  const title = document.querySelector("#confirm-title");
  const yes = document.querySelector("#confirm-yes");
  const fields = document.querySelector("#confirm-fields");
  const userInput = document.querySelector("#confirm-user");
  const groupInput = document.querySelector("#confirm-group");
  const opts = options || {};
  title.textContent = opts.title || "Delete instance";
  yes.textContent = opts.yes || "Delete";
  message.textContent = text;
  const showFields = Boolean(opts.fields);
  fields.hidden = !showFields;
  if (showFields) {
    userInput.value = "";
    groupInput.value = "";
  }
  sheet.hidden = false;
  return new Promise((resolve) => {
    function finish(value) {
      sheet.hidden = true;
      fields.hidden = true;
      sheet.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
      if (value && showFields) {
        resolve({ user: userInput.value.trim(), group: groupInput.value.trim() });
        return;
      }
      resolve(value);
    }
    function onClick(event) {
      if (event.target.closest("#confirm-yes")) finish(true);
      else if (event.target.closest("#confirm-no") || event.target.closest("[data-confirm-no]")) finish(false);
    }
    function onKey(event) {
      if (event.key === "Escape") finish(false);
    }
    sheet.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
  });
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
  body.append("name", instanceNameInput.value.trim());
  body.append("gpus", chosen.join(","));
  body.append("cuda_tag", selectedCudaTag());
  const imageOverride = instanceImageInput.value.trim();
  if (imageOverride) body.append("instance_image", imageOverride);
  body.append("start", start ? "true" : "false");
  body.append("force", forceInput.checked ? "true" : "false");
  body.append("vram", launch.vram);
  body.append("attention", launch.attention);
  body.append("flags", launch.flags.join(","));
  body.append("reserve_vram", reserveInput.value.trim());
  body.append("vram_headroom", headroomInput.value.trim());
  body.append("preview_method", previewMethodInput.value);
  body.append("preview_size", previewSizeInput.value.trim());
  body.append("extra_args", "");
  body.append("comfy_extra_args", extraArgsInput.value.trim());
  for (const url of gitUrlLines(gitUrlsInput.value)) {
    body.append("custom_node_git_urls", url);
  }
  const zips = [...(zipInput.files || [])];
  const zipNameInputs = [...document.querySelectorAll("#zip-packs input")];
  zips.forEach((zip, index) => {
    body.append("custom_nodes_zip", zip, zip.name);
    const typed = zipNameInputs[index] ? zipNameInputs[index].value.trim() : "";
    body.append("custom_nodes_zip_name", typed);
  });
  body.append("install_missing_from_workflow", installMissingInput.checked ? "true" : "false");
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
  const notices = responseNotices(result.payload);
  fileInput.value = "";
  fileName.textContent = "No file chosen";
  pathInput.value = "";
  instanceNameInput.value = "";
  forceInput.checked = false;
  instanceImageInput.value = "";
  gitUrlsInput.value = "";
  zipInput.value = "";
  renderZipPacks();
  installMissingInput.checked = true;
  resetLaunch();
  if (flagsDisclosure) flagsDisclosure.open = false;
  closeSheet();
  const noticeText = notices.length ? ` ${notices.join(" ")}` : "";
  showToast(`${mode}: ${instance.name} · port ${instance.port}.${noticeText}`);
  if (notices.length) showBanner(notices.join(" "));
  else hide(banner);
  await refresh();
  if (notices.length) showBanner(notices.join(" "));
}

function renderZipPacks() {
  const files = [...(zipInput.files || [])];
  zipPacks.replaceChildren();
  if (!files.length) {
    zipName.textContent = "No zip chosen";
    return;
  }
  zipName.textContent = files.length === 1 ? files[0].name : `${files.length} zips chosen`;
  files.forEach((file) => {
    const row = document.createElement("label");
    row.className = "zip-pack";
    const title = document.createElement("span");
    title.className = "zip-pack-file";
    title.textContent = file.name;
    const input = document.createElement("input");
    input.className = "text-input";
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "Name from the zip";
    input.setAttribute("aria-label", `Folder name for ${file.name}`);
    row.append(title, input);
    zipPacks.append(row);
  });
}

function gitUrlLines(value) {
  return String(value || "")
    .split(/[\r\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function responseNotices(payload) {
  const items = [];
  if (payload && payload.warning) items.push(String(payload.warning));
  if (payload && Array.isArray(payload.warnings)) {
    for (const item of payload.warnings) {
      if (item) items.push(String(item));
    }
  }
  return items;
}

function toggleFlagEditor(name, button, editor) {
  const open = editor.hidden;
  editor.hidden = !open;
  button.setAttribute("aria-expanded", open ? "true" : "false");
  button.classList.toggle("on", open);
  if (open) state.openEditors.add(name);
  else state.openEditors.delete(name);
}

function actionButton(label, caption, icon, tone) {
  const button = el("button", {
    className: `action-icon tone-${tone}`,
    type: "button",
  });
  const glyph = el("span", { className: "action-glyph" });
  glyph.append(icon);
  button.setAttribute("aria-label", label);
  button.append(glyph, el("span", { className: "action-caption", text: caption }));
  return button;
}

function strokeIcon(paths, filled) {
  const svgNs = "http:" + "//www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of paths) {
    const path = document.createElementNS(svgNs, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", filled ? "currentColor" : "none");
    path.setAttribute("stroke", filled ? "none" : "currentColor");
    path.setAttribute("stroke-width", filled ? "0" : "1.65");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
  }
  return svg;
}

function playIcon() {
  return strokeIcon(["M8.2 5.6c-.7 0-1.2.5-1.2 1.2v10.4c0 .9 1 1.5 1.8 1l8.6-5.2c.7-.4.7-1.5 0-1.9L8.8 5.9c-.2-.1-.4-.3-.6-.3z"], true);
}

function stopIcon() {
  return strokeIcon(["M8.2 6.8h7.6a1.6 1.6 0 0 1 1.6 1.6v7.2a1.6 1.6 0 0 1-1.6 1.6H8.2a1.6 1.6 0 0 1-1.6-1.6V8.4a1.6 1.6 0 0 1 1.6-1.6z"], true);
}

function forceStopIcon() {
  return strokeIcon([
    "M12 4.2a7.8 7.8 0 1 0 0 15.6 7.8 7.8 0 0 0 0-15.6z",
    "M9 9l6 6",
    "M15 9l-6 6",
  ]);
}

function terminalIcon() {
  return strokeIcon([
    "M6.2 7.2h11.6a1.6 1.6 0 0 1 1.6 1.6v6.4a1.6 1.6 0 0 1-1.6 1.6H6.2a1.6 1.6 0 0 1-1.6-1.6V8.8a1.6 1.6 0 0 1 1.6-1.6z",
    "M8 11.1l2.2 1.6L8 14.3",
    "M11.6 14.3h3.4",
  ]);
}

function comfyIcon() {
  return strokeIcon([
    "M10 7H8.2A1.7 1.7 0 0 0 6.5 8.7v7.1A1.7 1.7 0 0 0 8.2 17.5h7.1a1.7 1.7 0 0 0 1.7-1.7V14",
    "M13.2 6.2H18v4.8",
    "M17.6 6.6l-7.2 7.2",
  ]);
}

function pencilIcon() {
  return strokeIcon([
    "M14.2 5.1a1.7 1.7 0 0 1 2.4 0l2.3 2.3a1.7 1.7 0 0 1 0 2.4L9.2 19.5 4.6 20.4l.9-4.6 8.7-10.7z",
    "M13 7.4l3.6 3.6",
  ]);
}

function trashIcon() {
  return strokeIcon([
    "M5 7.5h14",
    "M9.2 7.5V6a1.4 1.4 0 0 1 1.4-1.4h2.8A1.4 1.4 0 0 1 14.8 6v1.5",
    "M7.6 7.5l.7 11.1a1.4 1.4 0 0 0 1.4 1.3h4.6a1.4 1.4 0 0 0 1.4-1.3l.7-11.1",
  ]);
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
  renderFlagChips();
}

const FLAG_SECTIONS = [
  ["precision", "Precision"],
  ["caching", "Caching"],
  ["preview", "Preview"],
  ["vram", "VRAM"],
  ["misc", "Misc"],
];

function instanceFlagEditor(instance) {
  const draft = draftFor(instance);
  const box = el("div", { className: "flag-editor" });
  const body = el("div", { className: "flag-editor-body" });
  body.append(el("p", { className: "flag-sub", text: "ComfyUI flags" }));
  body.append(el("p", {
    className: "hint",
    text: "Click a flag to add it. × removes it. Apply stops this instance if it is running and recreates the same name, port, mounts, and workflow. Only the Comfy arguments change. The CUDA line stays. Changing cu130 versus cu124 requires a recreate from the create sheet.",
  }));
  const applied = el("div", { className: "chip-row" });
  const catalog = el("div");
  body.append(el("p", { className: "flag-sub", text: "VRAM" }));
  body.append(draftRadios(draft, "vram", `vram-${instance.name}`, radioValues("vram"), applied, catalog));
  body.append(draftNumber(draft, "reserve", "Reserve VRAM (GB)", "--reserve-vram"));
  body.append(draftNumber(draft, "headroom", "VRAM headroom (GB)", "--vram-headroom"));
  body.append(el("p", { className: "flag-sub", text: "Attention" }));
  body.append(draftRadios(draft, "attention", `attention-${instance.name}`, radioValues("attention"), applied, catalog));
  paintInstanceFlags(draft, applied, catalog);
  body.append(applied, catalog);
  const apply = el("button", { className: "btn secondary flag-apply", type: "button", text: "Apply" });
  apply.addEventListener("click", () => applyLaunch(instance.name, apply));
  box.append(body, apply);
  return box;
}

function draftNumber(draft, field, label, flag) {
  const block = el("div", { className: "flag-number" });
  block.append(el("p", { className: "field-label", text: label }));
  const input = document.createElement("input");
  input.className = "text-input";
  input.type = "number";
  input.min = "0";
  input.step = "any";
  input.inputMode = "decimal";
  input.placeholder = "empty = omit";
  input.setAttribute("aria-label", `${label} ${flag}`);
  input.value = draft[field] || "";
  input.addEventListener("input", () => {
    draft[field] = input.value.trim();
    draft.dirty = true;
  });
  block.append(input);
  return block;
}

function radioValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]`)].map((node) => node.value);
}

function draftRadios(draft, field, groupName, values, applied, catalog) {
  const group = el("div", { className: "choice-col" });
  for (const value of values) {
    const label = el("label", { className: "choice" });
    const input = document.createElement("input");
    input.type = "radio";
    input.name = groupName;
    input.value = value;
    input.checked = (draft[field] || "") === value;
    input.addEventListener("change", () => {
      if (!input.checked) return;
      draft[field] = value;
      if (field === "vram" && value) {
        draft.flags.delete("--cpu");
        draft.flags.delete("--gpu-only");
      }
      draft.dirty = true;
      paintInstanceFlags(draft, applied, catalog);
    });
    label.append(input, el("span", { text: value || "Default" }));
    group.append(label);
  }
  return group;
}

function draftFor(instance) {
  const existing = state.drafts.get(instance.name);
  if (existing && existing.dirty) return existing;
  const launch = instance.launch || {};
  const fresh = {
    vram: launch.vram || "",
    attention: launch.attention || "",
    flags: new Set(Array.isArray(launch.flags) ? launch.flags : []),
    preview: launch.preview_method || "",
    reserve: launch.reserve_vram == null ? "" : String(launch.reserve_vram),
    headroom: launch.vram_headroom == null ? "" : String(launch.vram_headroom),
    previewSize: launch.preview_size == null ? "" : String(launch.preview_size),
    extra: launch.extra_args || "",
    dirty: false,
  };
  state.drafts.set(instance.name, fresh);
  return fresh;
}

function paintInstanceFlags(draft, applied, catalog) {
  applied.replaceChildren();
  if (draft.vram) {
    applied.append(appliedChip(draft.vram, () => {
      draft.vram = "";
      draft.dirty = true;
      paintInstanceFlags(draft, applied, catalog);
    }));
  }
  if (draft.attention) {
    applied.append(appliedChip(draft.attention, () => {
      draft.attention = "";
      draft.dirty = true;
      paintInstanceFlags(draft, applied, catalog);
    }));
  }
  for (const flag of draft.flags) {
    applied.append(appliedChip(flag, () => {
      draft.flags.delete(flag);
      draft.dirty = true;
      paintInstanceFlags(draft, applied, catalog);
    }));
  }
  if (draft.preview) {
    applied.append(appliedChip(`--preview-method ${draft.preview}`, () => {
      draft.preview = "";
      draft.dirty = true;
      paintInstanceFlags(draft, applied, catalog);
    }));
  }
  catalog.replaceChildren();
  for (const [section, label] of FLAG_SECTIONS) {
    const row = el("div", { className: "chip-row" });
    if (section === "preview") {
      for (const value of ["auto", "latent2rgb", "taesd", "none"]) {
        row.append(catalogChip(`--preview-method ${value}`, draft.preview === value, () => {
          draft.preview = value;
          draft.dirty = true;
          paintInstanceFlags(draft, applied, catalog);
        }));
      }
    }
    for (const node of document.querySelectorAll(`input[name="flag"][data-section="${section}"]`)) {
      const flag = node.value;
      row.append(catalogChip(flag, draft.flags.has(flag) || draft.vram === flag || draft.attention === flag, () => {
        addDraftFlag(draft, node);
        paintInstanceFlags(draft, applied, catalog);
      }));
    }
    if (!row.childNodes.length) continue;
    catalog.append(el("p", { className: "flag-sub", text: label }));
    catalog.append(row);
  }
}

function addDraftFlag(draft, node) {
  const flag = node.value;
  const group = node.dataset.exclusive || "";
  if (group) {
    for (const other of document.querySelectorAll(`input[name="flag"][data-exclusive="${group}"]`)) {
      draft.flags.delete(other.value);
    }
    if (group === "vram") draft.vram = "";
  }
  if (flag === "--cpu" || flag === "--gpu-only") draft.vram = "";
  draft.flags.add(flag);
  draft.dirty = true;
}

async function applyLaunch(name, button) {
  const draft = state.drafts.get(name);
  if (!draft || state.busy) return;
  const previous = button.textContent;
  state.busy = true;
  button.disabled = true;
  button.textContent = "Applying…";
  const result = await call(`/api/instances/${encodeURIComponent(name)}/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      vram: draft.vram,
      attention: draft.attention,
      flags: [...draft.flags],
      reserve_vram: draft.reserve,
      vram_headroom: draft.headroom,
      preview_method: draft.preview,
      preview_size: draft.previewSize,
      extra_args: draft.extra,
    }),
  });
  state.busy = false;
  button.textContent = previous;
  if (!result.ok) {
    showBanner(result.error);
    await refresh();
    return;
  }
  draft.dirty = false;
  const instance = result.payload.instance;
  showToast(`Updated ${instance.name} on port ${instance.port}. Same name, mounts, and workflow.`);
  hide(banner);
  await refresh();
}

function renderFlagChips() {
  const applied = document.querySelector("#applied-flags");
  const catalog = document.querySelector("#flag-catalog");
  if (!applied || !catalog) return;
  applied.replaceChildren();
  for (const node of document.querySelectorAll('input[name="flag"]:checked')) {
    applied.append(appliedChip(node.value, () => {
      node.checked = false;
      renderFlagChips();
    }));
  }
  if (previewMethodInput.value) {
    applied.append(appliedChip(`--preview-method ${previewMethodInput.value}`, () => {
      previewMethodInput.value = "";
      renderFlagChips();
    }));
  }
  catalog.replaceChildren();
  for (const [section, label] of FLAG_SECTIONS) {
    const row = el("div", { className: "chip-row" });
    if (section === "preview") {
      for (const value of ["auto", "latent2rgb", "taesd", "none"]) {
        row.append(catalogChip(`--preview-method ${value}`, previewMethodInput.value === value, () => {
          previewMethodInput.value = value;
          renderFlagChips();
        }));
      }
    }
    for (const node of document.querySelectorAll(`input[name="flag"][data-section="${section}"]`)) {
      row.append(catalogChip(node.value, node.checked, () => addFlag(node)));
    }
    if (!row.childNodes.length) continue;
    catalog.append(el("p", { className: "flag-sub", text: label }));
    catalog.append(row);
  }
}

function addFlag(node) {
  const group = node.dataset.exclusive || "";
  if (group) {
    for (const other of document.querySelectorAll(`input[name="flag"][data-exclusive="${group}"]`)) {
      if (other !== node) other.checked = false;
    }
  }
  node.checked = true;
  renderFlagChips();
}

function appliedChip(label, onRemove) {
  const chip = el("span", { className: "chip on" });
  chip.append(document.createTextNode(label));
  const remove = el("button", { className: "chip-x", type: "button", text: "×" });
  remove.setAttribute("aria-label", `Remove ${label}`);
  remove.addEventListener("click", onRemove);
  chip.append(remove);
  return chip;
}

function catalogChip(label, selected, onAdd) {
  const button = el("button", { className: selected ? "chip on" : "chip", type: "button", text: label });
  button.addEventListener("click", onAdd);
  return button;
}

renderFlagChips();

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

async function fixOwnership() {
  const answer = await askConfirm(
    "Change ownership of /home/ComfyFleet/wildcards, /home/ComfyFleet/models, every /home/ComfyFleet/custom_nodes_* directory, and /home/ComfyFleet/files? Only those directories are walked. Symlinks into the image baked custom nodes are not followed.",
    { title: "Fix ownership", yes: "Fix ownership", fields: true }
  );
  if (!answer) return;
  state.busy = true;
  const result = await call("/api/host/fix-owner", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: answer.user || "", group: answer.group || "" }),
  });
  state.busy = false;
  if (result.sessionExpired || isAuthFailure(result)) return;
  if (!result.ok) {
    showBanner(result.error || "Fix ownership failed.");
    return;
  }
  const paths = (result.payload && result.payload.paths) || [];
  const owner = (result.payload && result.payload.user) || "comfyuser";
  const group = (result.payload && result.payload.group) || "comfyuser";
  showToast(
    paths.length
      ? `Ownership updated on ${paths.length} paths for ${owner}:${group}.`
      : "No allowlisted directories were present."
  );
  hide(banner);
}

async function pruneDangling() {
  const yes = await askConfirm(
    "Remove stopped containers that are not ComfyFleet instances? Containers labeled comfyfleet.managed=true are kept, including stopped instances.",
    { title: "Prune dangling containers", yes: "Prune" }
  );
  if (!yes) return;
  state.busy = true;
  const result = await call("/api/host/prune-dangling", { method: "POST" });
  state.busy = false;
  if (result.sessionExpired || isAuthFailure(result)) return;
  if (!result.ok) {
    showBanner(result.error || "Prune failed.");
    return;
  }
  const removed = (result.payload && result.payload.removed) || [];
  showToast(
    removed.length
      ? `Removed ${removed.length} dangling container${removed.length === 1 ? "" : "s"}. Fleet instances were kept.`
      : "No dangling containers to remove. Fleet instances were kept."
  );
  hide(banner);
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
      headers: Object.assign({ Accept: "application/json" }, (options && options.headers) || {}),
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
