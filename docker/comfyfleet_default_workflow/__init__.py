"""Serve the operator workflow to the ComfyUI frontend.

This custom node is a loader. It does not embed a workflow. The only file it
serves is the operator JSON bind-mounted at
``/opt/comfyfleet/instance/default_workflow.json``. If that file is missing the
route returns 404 and nothing is substituted.
"""

from __future__ import annotations

import os

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

WORKFLOW_PATH = os.environ.get(
    "COMFYFLEET_WORKFLOW_PATH",
    "/opt/comfyfleet/instance/default_workflow.json",
)
BOOT_ID_FILE = "/tmp/comfyfleet-boot-id"

try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/comfyfleet/boot")
    async def comfyfleet_boot(_request):
        boot_id = ""
        try:
            with open(BOOT_ID_FILE, "r", encoding="utf-8") as handle:
                boot_id = handle.read().strip()
        except OSError:
            boot_id = ""
        mtime_ns = 0
        if os.path.isfile(WORKFLOW_PATH):
            try:
                mtime_ns = os.stat(WORKFLOW_PATH).st_mtime_ns
            except OSError:
                mtime_ns = 0
        return web.json_response(
            {
                "boot_id": boot_id,
                "mtime_ns": mtime_ns,
                "path": WORKFLOW_PATH,
            },
            headers={"Cache-Control": "no-store"},
        )

    @PromptServer.instance.routes.get("/comfyfleet/default-workflow")
    async def comfyfleet_default_workflow(_request):
        if not os.path.isfile(WORKFLOW_PATH):
            return web.json_response(
                {
                    "error": "operator workflow missing",
                    "path": WORKFLOW_PATH,
                },
                status=404,
                headers={"Cache-Control": "no-store"},
            )
        return web.FileResponse(
            WORKFLOW_PATH,
            headers={"Cache-Control": "no-store"},
        )
except Exception as exc:  # ComfyUI imports this module; surface loader failures.
    print(f"comfyfleet: default-workflow routes were not registered: {exc}")
