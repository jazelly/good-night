"""Which operating system we are on, in the three buckets good-night cares about."""

from __future__ import annotations

import os
import sys

MACOS, WINDOWS, LINUX = "macos", "windows", "linux"


def current() -> str:
    if sys.platform == "darwin":
        return MACOS
    if os.name == "nt" or sys.platform.startswith(("win", "cygwin", "msys")):
        return WINDOWS
    return LINUX


PRETTY = {MACOS: "macOS", WINDOWS: "Windows", LINUX: "Linux"}
MACHINE = {MACOS: "Mac", WINDOWS: "PC", LINUX: "computer"}
