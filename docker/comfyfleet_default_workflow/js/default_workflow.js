/**
 * Load the operator workflow after each container start.
 * The file is fetched from /comfyfleet/default-workflow (the bind-mounted
 * operator JSON). This script does not embed a workflow and does not fall
 * back to a stock graph if the fetch fails.
 */
import { app } from "../../scripts/app.js";

const STORAGE_KEY = "ComfyFleet.appliedToken";

function isApiWorkflow(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return false;
  }
  if (Array.isArray(data.nodes)) {
    return false;
  }
  const values = Object.values(data);
  if (!values.length) {
    return false;
  }
  return values.every(
    (node) =>
      node &&
      typeof node === "object" &&
      !Array.isArray(node) &&
      typeof node.class_type === "string"
  );
}

let applied = false;
let loading = false;

async function bootToken() {
  const response = await fetch("/comfyfleet/boot", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("boot metadata unavailable");
  }
  const meta = await response.json();
  return `${meta.boot_id || ""}:${meta.mtime_ns || 0}`;
}

async function applyOperatorWorkflow() {
  if (applied || loading) {
    return;
  }
  loading = true;
  try {
    const token = await bootToken();
    if (sessionStorage.getItem(STORAGE_KEY) === token) {
      applied = true;
      return;
    }
    const response = await fetch("/comfyfleet/default-workflow", { cache: "no-store" });
    if (!response.ok) {
      console.error(
        "ComfyFleet: operator workflow unavailable. No stock workflow will be substituted."
      );
      return;
    }
    const workflow = await response.json();
    if (isApiWorkflow(workflow) && typeof app.loadApiJson === "function") {
      await app.loadApiJson(workflow, "default_workflow.json");
    } else if (typeof app.loadGraphData === "function") {
      await app.loadGraphData(workflow);
    } else {
      console.error("ComfyFleet: this ComfyUI frontend cannot load a workflow JSON");
      return;
    }
    sessionStorage.setItem(STORAGE_KEY, token);
    applied = true;
  } catch (err) {
    console.error("ComfyFleet: failed to load the operator workflow", err);
  } finally {
    loading = false;
  }
}

app.registerExtension({
  name: "ComfyFleet.DefaultWorkflow",
  async afterConfigureGraph() {
    if (applied || loading) {
      return;
    }
    setTimeout(() => {
      applyOperatorWorkflow();
    }, 0);
  },
  async setup() {
    setTimeout(() => {
      applyOperatorWorkflow();
    }, 400);
  },
});
