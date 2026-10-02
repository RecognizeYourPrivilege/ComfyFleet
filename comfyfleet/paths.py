"""Host paths locked to ``/home/...`` for Phase 1 (OPEN O-04)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Subdirectories ComfyUI v0.37.4 looks for under models/.
MODEL_SUBDIRS = (
    "checkpoints",
    "configs",
    "loras",
    "vae",
    "text_encoders",
    "clip",
    "diffusion_models",
    "unet",
    "clip_vision",
    "style_models",
    "embeddings",
    "diffusers",
    "vae_approx",
    "controlnet",
    "t2i_adapter",
    "gligen",
    "upscale_models",
    "latent_upscale_models",
    "hypernetworks",
    "photomaker",
    "classifiers",
    "model_patches",
    "audio_encoders",
    "background_removal",
    "frame_interpolation",
    "geometry_estimation",
    "optical_flow",
    "detection",
)

CONTAINER_PORT = 8188
WORKFLOW_CONTAINER_PATH = "/opt/comfyfleet/instance/default_workflow.json"
DEFAULT_IMAGE = "comfyfleet:phase1"


@dataclass(frozen=True)
class FleetLayout:
    """Phase 1 mount root. The CLI always uses ``/home``."""

    root: Path = Path("/home")

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def files(self) -> Path:
        return self.root / "files"

    def custom_nodes(self, name: str) -> Path:
        return self.root / f"custom_nodes_{name}"

    def instance_dir(self, name: str) -> Path:
        return self.files / name

    def workflow_file(self, name: str) -> Path:
        return self.instance_dir(name) / "default_workflow.json"

    def metadata_file(self, name: str) -> Path:
        return self.instance_dir(name) / "comfyfleet.json"

    def input_dir(self, name: str) -> Path:
        return self.instance_dir(name) / "input"

    def output_dir(self, name: str) -> Path:
        return self.instance_dir(name) / "output"

    def temp_dir(self, name: str) -> Path:
        return self.instance_dir(name) / "temp"
