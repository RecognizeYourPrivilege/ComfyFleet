"""ComfyFleet host control.

The CLI and the Phase 2 control HTTP API call ``comfyfleet.control``.
Phase 3 Auth should wrap ``authorize`` rather than growing a second
implementation of mounts, ports, or workflow copy.
"""

__version__ = "0.1.0"
