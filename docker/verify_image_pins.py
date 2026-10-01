#!/usr/bin/env python3
"""Fail the instance image build when numpy drifted or OpenCV cannot load.

RES4LYF's images.py imports cv2 while the custom node loads. The
opencv-python wheel (the name in that repo's requirements.txt) ships a Qt
xcb platform plugin, cv2/qt/plugins/platforms/libqxcb.so, whose DT_NEEDED
entry is libxcb.so.1. bookworm-slim does not ship it. Importing cv2 also
loads libGL.so.1 and libglib-2.0.so.0 through the bundled Qt libraries.

This runs after requirements are installed. It does not need a GPU or a
display: dlopen resolves NEEDED libraries and returns before any X server
is contacted.
"""

import ctypes
import glob
import os
import sys

NUMPY_PIN = "2.2.6"


def main() -> None:
    import numpy

    if numpy.__version__ != NUMPY_PIN:
        sys.exit(f"numpy {numpy.__version__} != {NUMPY_PIN}")

    # The historical crash, before any Qt plugin work. Apt package: libxcb1.
    ctypes.CDLL("libxcb.so.1")

    import cv2

    plugins = glob.glob(
        os.path.join(
            os.path.dirname(cv2.__file__),
            "qt",
            "plugins",
            "platforms",
            "libqxcb.so",
        )
    )
    if not plugins:
        sys.exit(
            "opencv-python Qt xcb plugin missing. RES4LYF requires "
            "opencv-python, and that plugin is what needs libxcb.so.1."
        )
    # Also pulls libX11.so.6, libXext.so.6, libSM.so.6, libICE.so.6, and
    # libGL.so.1. A missing soname raises OSError here.
    ctypes.CDLL(plugins[0], mode=ctypes.RTLD_GLOBAL)
    print(
        f"comfyfleet: numpy {numpy.__version__} cv2 {cv2.__version__} libxcb ok",
        flush=True,
    )


if __name__ == "__main__":
    main()
