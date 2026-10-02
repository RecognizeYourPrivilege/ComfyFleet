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
    # Impact Pack SAM weights. The image does not bake them. First-run
    # download into this shared directory is the operator path.
    "sams",
)

CONTAINER_PORT = 8188
WORKFLOW_CONTAINER_PATH = "/opt/comfyfleet/instance/default_workflow.json"
# Host and container use the same path. Impact's impact-pack.ini is seeded
# with this value and no quotes.
WILDCARDS_CONTAINER = "/home/wildcards"
# Primary instance tags. cu130 is the default when the operator does not choose.
CUDA_TAGS = ("cu130", "cu124")
DEFAULT_CUDA_TAG = "cu130"
DEFAULT_IMAGE = "comfyfleet:cu130"
# Published cu130 digest (GHCR run that built the CUDA 13.0 line). A ref that
# still says :phase1 but carries this digest is that cu130 image, not the
# later :phase1 alias of cu124.
CU130_PUBLISHED_DIGEST = (
    "sha256:cfa4afde856b909a8d3878688cb22eb3c65d17fe4e20efb22a959a3ce9890e75"
)


@dataclass(frozen=True)
class FleetLayout:
    """Phase 1 mount root. The CLI always uses ``/home``."""

    root: Path = Path("/home")

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def wildcards(self) -> Path:
        return self.root / "wildcards"

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
