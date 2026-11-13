"""macOS helpers that never trigger a permission (TCC) prompt.

* Running apps come from `lsappinfo` (LaunchServices), which needs no
  Automation permission, unlike asking System Events.
* Automation permission is checked with AEDeterminePermissionToAutomateTarget
  and askUserIfNeeded=false, which only reports the current state and never
  prompts. The same call with askUserIfNeeded=true is used by `setup` to show
  the prompts up front, once.
"""

from __future__ import annotations

import ctypes
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# OSStatus values from AppleEvents.h / MacErrors.h
NO_ERR = 0
ERR_AE_EVENT_NOT_PERMITTED = -1743       # user said "Don't Allow"
ERR_AE_EVENT_WOULD_REQUIRE_CONSENT = -1744  # never asked yet -> would prompt
PROC_NOT_FOUND = -600                    # target not running

PERMISSION_TEXT = {
    NO_ERR: "granted",
    ERR_AE_EVENT_NOT_PERMITTED: "denied",
    ERR_AE_EVENT_WOULD_REQUIRE_CONSENT: "not asked yet",
    PROC_NOT_FOUND: "app not running",
}

# osascript `display notification` is posted on behalf of Script Editor.
NOTIFY_BUNDLE_ID = "com.apple.ScriptEditor2"


@dataclass
class App:
    pid: int
    bundle_id: str
    name: str
    app_type: str  # Foreground / UIElement / BackgroundOnly


_BLOCK_RE = re.compile(r'^\s*\d+\)\s+"(?P<name>.*)"\s+ASN:', re.M)


def parse_lsappinfo(text: str) -> List[App]:
    apps = []
    starts = [m for m in _BLOCK_RE.finditer(text)]
    for i, m in enumerate(starts):
        block = text[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        bid = re.search(r'bundleID="([^"]*)"', block)
        pid = re.search(r"\bpid = (\d+)", block)
        typ = re.search(r'\btype="([^"]*)"', block)
        if not pid:
            continue
        apps.append(App(int(pid.group(1)), bid.group(1) if bid else "", m.group("name"),
                        typ.group(1) if typ else ""))
    return apps


def running_apps(foreground_only: bool = True) -> List[App]:
    try:
        p = subprocess.run(["/usr/bin/lsappinfo", "list"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    apps = parse_lsappinfo(p.stdout)
    if foreground_only:
        apps = [a for a in apps if a.app_type == "Foreground"]
    return apps


# ---------------------------------------------------------------- Apple Events


class _AEDesc(ctypes.Structure):
    _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]


def _fourcc(s: str) -> int:
    b = s.encode("ascii")
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


_cs = None


def _coreservices():
    global _cs
    if _cs is None:
        _cs = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        _cs.AECreateDesc.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(_AEDesc)]
        _cs.AECreateDesc.restype = ctypes.c_int16
        _cs.AEDisposeDesc.argtypes = [ctypes.POINTER(_AEDesc)]
        _cs.AEDisposeDesc.restype = ctypes.c_int16
        _cs.AEDeterminePermissionToAutomateTarget.argtypes = [
            ctypes.POINTER(_AEDesc), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
        _cs.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
    return _cs


def automation_permission(bundle_id: str, ask: bool = False) -> int:
    """OSStatus for "may the host app send `quit` Apple Events to bundle_id?".

    ask=False never shows UI. ask=True shows the macOS consent prompt if the
    user hasn't decided yet (blocks until answered); used only by `setup`.
    Attribution goes to the *responsible* app, i.e. the terminal you run in.
    """
    cs = _coreservices()
    data = bundle_id.encode("utf-8")
    desc = _AEDesc()
    err = cs.AECreateDesc(_fourcc("bund"), data, len(data), ctypes.byref(desc))
    if err != 0:
        return int(err)
    try:
        return int(cs.AEDeterminePermissionToAutomateTarget(
            ctypes.byref(desc), _fourcc("aevt"), _fourcc("quit"), bool(ask)))
    finally:
        cs.AEDisposeDesc(ctypes.byref(desc))


# ---------------------------------------------------------------- host app


def host_app(apps: Optional[List[App]] = None) -> Optional[App]:
    """The GUI app this process runs under (Terminal, iTerm2, Cursor, ...).
    macOS attributes privacy permissions to this app."""
    if apps is None:
        apps = running_apps(foreground_only=False)
    by_pid: Dict[int, App] = {a.pid: a for a in apps}
    pid = os.getpid()
    for _ in range(64):
        if pid in by_pid and by_pid[pid].app_type == "Foreground":
            return by_pid[pid]
        try:
            out = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
            pid = int(out.stdout.strip() or 0)
        except (OSError, ValueError):
            return None
        if pid <= 1:
            return None
    return None


# ---------------------------------------------------------------- notifications


def notifications_decided(state_dir: Path) -> bool:
    """True if Script Editor (osascript notifications) is already known to
    Notification Center, i.e. the one-time notification permission question
    has been dealt with, or `setup` has already triggered it."""
    if (state_dir / "notifications-initialized").exists():
        return True
    try:
        with open(Path.home() / "Library/Preferences/com.apple.ncprefs.plist", "rb") as fh:
            d = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return any(isinstance(a, dict) and a.get("bundle-id") == NOTIFY_BUNDLE_ID for a in d.get("apps", []))
