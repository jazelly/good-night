"""The "good night" action: what happens once every agent is idle.

    action        macOS                     Windows                           Linux (minimal)
    ----------    ----------------------    ------------------------------    ------------------
    sleep         pmset sleepnow            SetSuspendState(FALSE, ...)       systemctl suspend
    hibernate     (rejected, see below)     shutdown /h                       systemctl hibernate
    display-off   pmset displaysleepnow     SC_MONITORPOWER broadcast         (unsupported)
    shutdown      quit apps, then           close windows, then               systemctl poweroff
                  sudo -n shutdown -h now   shutdown /s /t 0 /f

Defaults: sleep on macOS (and Linux), hibernate on Windows.

This module only decides *what* to run. It never runs anything itself, so it
is safe to import and test anywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .system import LINUX, MACOS, WINDOWS

SHUTDOWN, SLEEP, HIBERNATE, DISPLAY_OFF = "shutdown", "sleep", "hibernate", "display-off"
ACTIONS = (SLEEP, HIBERNATE, DISPLAY_OFF, SHUTDOWN)

ALIASES = {
    "poweroff": SHUTDOWN, "power-off": SHUTDOWN, "shut-down": SHUTDOWN, "halt": SHUTDOWN, "off": SHUTDOWN,
    "suspend": SLEEP, "standby": SLEEP,
    "hibernation": HIBERNATE,
    "displayoff": DISPLAY_OFF, "display_off": DISPLAY_OFF, "display": DISPLAY_OFF,
    "screen-off": DISPLAY_OFF, "screenoff": DISPLAY_OFF, "screen_off": DISPLAY_OFF,
    "monitor-off": DISPLAY_OFF, "monitoroff": DISPLAY_OFF,
}

DEFAULTS = {MACOS: SLEEP, WINDOWS: HIBERNATE, LINUX: SLEEP}

SUPPORTED = {
    MACOS: (SLEEP, DISPLAY_OFF, SHUTDOWN),
    WINDOWS: (SLEEP, HIBERNATE, DISPLAY_OFF, SHUTDOWN),
    LINUX: (SLEEP, HIBERNATE, SHUTDOWN),
}

WHY_NOT = {
    (MACOS, HIBERNATE): (
        "macOS has no command to hibernate on demand. `pmset sleepnow` sleeps; whether the Mac also "
        "writes memory to disk depends on `pmset -g | grep hibernatemode` (3 = safe sleep, the laptop "
        "default; 25 = true hibernate, which needs `sudo pmset -a hibernatemode 25` and changes every "
        "sleep). good-night won't change that setting for you. Use GOOD_NIGHT_ACTION=sleep (default) "
        "or GOOD_NIGHT_ACTION=shutdown."
    ),
    (LINUX, DISPLAY_OFF): "display-off is not implemented on Linux (it depends on X11/Wayland/console).",
}

# Words used in logs, notifications and the countdown dialog.
WORDS = {
    #             infinitive              button                    -ing
    SHUTDOWN:    ("shut down",            "Shut down now",          "shutting down"),
    SLEEP:       ("go to sleep",          "Sleep now",              "going to sleep"),
    HIBERNATE:   ("hibernate",            "Hibernate now",          "hibernating"),
    DISPLAY_OFF: ("turn its display off", "Turn display off now",   "turning the display off"),
}


class ActionError(ValueError):
    pass


def normalize(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "-")
    key = ALIASES.get(key, key)
    if key not in ACTIONS:
        raise ActionError(f"unknown action {raw!r}; choose one of: {', '.join(ACTIONS)}")
    return key


def resolve(raw: Optional[str], os_name: str) -> str:
    """Turn GOOD_NIGHT_ACTION / --action into a supported action for os_name.
    Empty means the platform default. Raises ActionError with a readable
    explanation for anything unknown or unsupported; never guesses."""
    if raw is None or not raw.strip():
        return DEFAULTS[os_name]
    action = normalize(raw)
    if action not in SUPPORTED[os_name]:
        why = WHY_NOT.get((os_name, action), "")
        raise ActionError(
            f"action {action!r} is not supported on {os_name}"
            + (f": {why}" if why else "")
            + f" (supported here: {', '.join(SUPPORTED[os_name])})"
        )
    return action


def needs_sudo(action: str, os_name: str) -> bool:
    """Only macOS shutdown needs root (via the sudoers drop-in)."""
    return os_name == MACOS and action == SHUTDOWN


def closes_apps(action: str) -> bool:
    """Only shutdown closes apps first. Sleep / hibernate / display-off keep
    everything open exactly as you left it."""
    return action == SHUTDOWN


def ends_session(action: str) -> bool:
    """Whether the machine stops running (so `watch` exits afterwards)."""
    return action in (SHUTDOWN, SLEEP, HIBERNATE)


# ---------------------------------------------------------------- commands

MAC_SHUTDOWN = ["/usr/bin/sudo", "-n", "/sbin/shutdown", "-h", "now"]
MAC_SUDO_CHECK = ["/usr/bin/sudo", "-n", "-l", "/sbin/shutdown", "-h", "now"]


def windows_system32() -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return root.rstrip("\\/") + "\\System32"


def windows_exe(name: str) -> str:
    return windows_system32() + "\\" + name


def windows_powershell() -> str:
    return windows_system32() + "\\WindowsPowerShell\\v1.0\\powershell.exe"


@dataclass
class Step:
    """One concrete way to perform an action.

    kind="exec":  run argv as a subprocess.
    kind="win32": call a Win32 API in-process via ctypes (`call` names it);
                  argv is empty and `display` documents the call.
    """
    kind: str
    argv: List[str]
    display: str
    call: str = ""


# Win32 constants for the in-process steps (see windows.py)
WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF, HWND_BROADCAST = 0x0112, 0xF170, 2, 0xFFFF


def plan(action: str, os_name: str) -> Step:
    """The command that performs `action` on `os_name` (after the countdown
    and, for shutdown, after apps were asked to close)."""
    if action not in SUPPORTED.get(os_name, ()):
        raise ActionError(f"action {action!r} is not supported on {os_name}")
    if os_name == MACOS:
        argv = {
            SHUTDOWN: MAC_SHUTDOWN,
            SLEEP: ["/usr/bin/pmset", "sleepnow"],
            DISPLAY_OFF: ["/usr/bin/pmset", "displaysleepnow"],
        }[action]
        return Step("exec", list(argv), " ".join(argv))
    if os_name == WINDOWS:
        if action == SHUTDOWN:
            # /f: without it, any app with unsaved work parks Windows on the
            # "These apps are preventing shutdown" screen forever. Apps were
            # already asked to close politely (CloseMainWindow) beforehand.
            argv = [windows_exe("shutdown.exe"), "/s", "/t", "0", "/f"]
            return Step("exec", argv, " ".join(argv))
        if action == HIBERNATE:
            argv = [windows_exe("shutdown.exe"), "/h"]
            return Step("exec", argv, " ".join(argv))
        if action == SLEEP:
            # NOT `rundll32 powrprof.dll,SetSuspendState 0,1,0`: rundll32 passes
            # garbage arguments, so that one hibernates whenever hibernation is
            # enabled. Calling the API directly with Hibernate=FALSE really sleeps.
            return Step("win32", [], "powrprof.SetSuspendState(Hibernate=FALSE, ForceCritical=FALSE, "
                        "DisableWakeEvent=FALSE)", call="suspend")
        if action == DISPLAY_OFF:
            return Step("win32", [], "user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)",
                        call="monitor_off")
    if os_name == LINUX:
        argv = {SHUTDOWN: ["systemctl", "poweroff"], SLEEP: ["systemctl", "suspend"],
                HIBERNATE: ["systemctl", "hibernate"]}[action]
        return Step("exec", list(argv), " ".join(argv))
    raise ActionError(f"no command for {action!r} on {os_name}")


def matrix() -> Dict[str, Dict[str, str]]:
    """{action: {os: command or 'unsupported'}} for docs / `doctor`."""
    out: Dict[str, Dict[str, str]] = {}
    for a in ACTIONS:
        out[a] = {}
        for os_name in (MACOS, WINDOWS, LINUX):
            try:
                out[a][os_name] = plan(a, os_name).display
            except ActionError:
                out[a][os_name] = "unsupported"
    return out


def words(action: str) -> Tuple[str, str, str]:
    return WORDS[action]
