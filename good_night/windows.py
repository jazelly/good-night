"""Windows helpers. Importable on any OS (everything Windows-only is loaded
lazily), so tests can run on macOS/Linux with the Win32 bits mocked.

Nothing here needs Administrator rights or shows a UAC prompt:
* sleep / display-off are plain Win32 calls (powrprof / user32),
* hibernate / shutdown use shutdown.exe, which any interactive user may run,
* hibernation state is read from `powercfg /a` and the registry (read-only),
* the countdown is a WScript.Shell popup (no permission needed).
Only *enabling* hibernation (`powercfg /hibernate on`) needs an elevated
terminal, and good-night never does that itself.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import actions

log = logging.getLogger("good-night")

CREATE_NO_WINDOW = 0x08000000

# Processes the shutdown "close windows" step leaves alone: the shell, terminals
# (one may be hosting good-night) and Python itself. Ancestors of this process
# are always skipped too. Names are matched case-insensitively, without .exe.
DEFAULT_KEEP_PROCESSES = [
    "explorer", "WindowsTerminal", "OpenConsole", "conhost", "cmd", "powershell", "pwsh",
    "python", "pythonw", "py", "wezterm-gui", "alacritty", "mintty", "ShellExperienceHost",
    "StartMenuExperienceHost", "SearchHost", "SearchApp", "TextInputHost", "LockApp",
]


def _windll(name: str):
    return getattr(ctypes, "windll").LoadLibrary(name)  # AttributeError off Windows


def _run(argv: List[str], timeout: int = 30) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def powershell_argv(script: str) -> List[str]:
    return [actions.windows_powershell(), "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script]


def powershell_available() -> bool:
    return Path(actions.windows_powershell()).exists()


# ---------------------------------------------------------------- processes


def pid_alive(pid: int) -> bool:
    """os.kill(pid, 0) must NOT be used on Windows: signal 0 is CTRL_C_EVENT."""
    PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE, ERROR_ACCESS_DENIED = 0x1000, 259, 5
    k32 = _windll("kernel32")
    k32.OpenProcess.restype = ctypes.c_void_p  # HANDLE is pointer-sized on 64-bit
    k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return k32.GetLastError() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong(0)
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


# ---------------------------------------------------------------- keep awake

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake(on: bool) -> bool:
    """SetThreadExecutionState: stop idle sleep while watching (the Windows
    caffeinate). Cleared again right before the action runs."""
    try:
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        fn = _windll("kernel32").SetThreadExecutionState
        fn.argtypes, fn.restype = [ctypes.c_uint], ctypes.c_uint
        return bool(fn(flags))
    except (AttributeError, OSError):
        return False


# ---------------------------------------------------------------- power states


@dataclass
class PowerStates:
    available: List[str] = field(default_factory=list)
    unavailable: Dict[str, str] = field(default_factory=dict)  # state -> reason
    raw: str = ""
    parsed: bool = False

    def has(self, token: str) -> bool:
        return any(token.lower() in s.lower() for s in self.available)

    @property
    def s3(self) -> bool:
        return self.has("(S3)")

    @property
    def modern_standby(self) -> bool:
        return self.has("(S0")


def parse_powercfg_a(text: str) -> PowerStates:
    """Parse `powercfg /a`. The output is localized, so we rely on its
    layout: the first unindented heading lists available states, the second
    lists unavailable ones; states are indented, reasons indented deeper."""
    ps = PowerStates(raw=text)
    section = -1
    last_state = None
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        stripped = line.strip()
        if indent == 0:
            section += 1
            last_state = None
            continue
        if section < 0:
            continue
        if section == 0:
            if indent <= 4:
                ps.available.append(stripped)
        elif section == 1:
            if indent <= 4:
                last_state = stripped
                ps.unavailable.setdefault(stripped, "")
            elif last_state is not None:
                ps.unavailable[last_state] = (ps.unavailable[last_state] + " " + stripped).strip()
    ps.parsed = section >= 0 and bool(ps.available or ps.unavailable)
    return ps


def power_states() -> PowerStates:
    rc, out = _run([actions.windows_exe("powercfg.exe"), "/a"], timeout=30)
    return parse_powercfg_a(out) if rc == 0 or out else PowerStates()


def hibernate_registry() -> Optional[bool]:
    """HKLM\\SYSTEM\\CurrentControlSet\\Control\\Power\\HibernateEnabled (read-only)."""
    try:
        import winreg  # type: ignore
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power") as k:
            val, _ = winreg.QueryValueEx(k, "HibernateEnabled")
            return bool(val)
    except OSError:
        return None


_HIB_RE = re.compile(r"^hibernat", re.I)


def hibernation(states: Optional[PowerStates] = None,
                registry: Optional[Callable[[], Optional[bool]]] = None) -> Tuple[Optional[bool], str]:
    """(True/False/None=unknown, explanation)."""
    states = power_states() if states is None else states
    reg = (registry or hibernate_registry)()
    if states.parsed:
        if any(_HIB_RE.match(s) for s in states.available):
            return True, "hibernation is available (powercfg /a)"
        for s, why in states.unavailable.items():
            if _HIB_RE.match(s):
                return False, f"powercfg /a: {s}: {why or 'not available'}"
    if reg is True:
        return True, "HibernateEnabled=1 in the registry"
    if reg is False:
        return False, "HibernateEnabled=0 in the registry (hibernation is turned off)"
    return None, "could not tell whether hibernation is enabled (non-English powercfg output?)"


HIBERNATE_FIX = ("enable it once in an Administrator terminal: `powercfg /hibernate on`, "
                 "or use GOOD_NIGHT_ACTION=sleep")


# ---------------------------------------------------------------- actions


def suspend(api=None) -> bool:
    """Sleep (S3 or Modern Standby). Blocks until the PC wakes up again."""
    if api is None:
        api = _windll("powrprof")
        api.SetSuspendState.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]  # BOOLEAN x3
        api.SetSuspendState.restype = ctypes.c_ubyte
    return bool(api.SetSuspendState(False, False, False))


def monitor_off(api=None) -> bool:
    if api is None:
        api = _windll("user32")
        api.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        api.PostMessageW.restype = ctypes.c_int
    # PostMessage, not SendMessage: SendMessage to HWND_BROADCAST can hang on
    # a window that isn't pumping messages.
    return bool(api.PostMessageW(actions.HWND_BROADCAST, actions.WM_SYSCOMMAND,
                                 actions.SC_MONITORPOWER, actions.MONITOR_OFF))


WIN32_CALLS = {"suspend": suspend, "monitor_off": monitor_off}


def close_windows_script(my_pid: int, keep: List[str], dry_run: bool) -> str:
    keep_list = ",".join("'" + k.replace("'", "''").lower() + "'" for k in keep)
    return (
        f"$keep=@({keep_list}); $mine=@(); $p={int(my_pid)}; "
        "for($i=0; $i -lt 64 -and $p; $i++){ $mine+=$p; "
        "$w=Get-CimInstance Win32_Process -Filter \"ProcessId=$p\" -ErrorAction SilentlyContinue; "
        "if(-not $w){break}; $p=[int]$w.ParentProcessId }; "
        "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
        "($keep -notcontains $_.ProcessName.ToLower()) -and ($mine -notcontains $_.Id) } | "
        "ForEach-Object { Write-Output (\"{0}`t{1}`t{2}\" -f $_.Id,$_.ProcessName,$_.MainWindowTitle); "
        + ("" if dry_run else "[void]$_.CloseMainWindow(); ")
        + "}"
    )


def close_apps(keep: List[str], wait: int, dry_run: bool) -> None:
    """Politely close every app window (like clicking X), wait, then let
    `shutdown /f` handle the rest. Needs no permission."""
    if not powershell_available():
        log.warning("PowerShell not found; skipping the polite close step (shutdown /f closes apps)")
        return
    rc, out = _run(powershell_argv(close_windows_script(os.getpid(), keep, dry_run)), timeout=60)
    pids = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            pids.append(int(parts[0]))
            log.info("%s %s (pid %s) %s", "would close" if dry_run else "closing", parts[1],
                     parts[0], f"'{parts[2]}'" if len(parts) > 2 and parts[2] else "")
    if not pids:
        log.info("no app windows to close")
        return
    if dry_run:
        return
    deadline = time.time() + wait
    while time.time() < deadline and any(pid_alive(p) for p in pids):
        time.sleep(1)
    left = [p for p in pids if pid_alive(p)]
    if left:
        log.info("%d app(s) still open after %ds (unsaved work?); shutdown /f will close them", len(left), wait)


# ---------------------------------------------------------------- countdown


def popup_script(message: str, seconds: int) -> str:
    msg = message.replace("'", "''")
    # 1 = OK/Cancel, 48 = warning icon, 4096 = system modal (stays on top)
    return (f"$r=(New-Object -ComObject WScript.Shell).Popup('{msg}', {int(seconds)}, "
            f"'good-night', 1 + 48 + 4096); Write-Output $r")


def countdown_popup(message: str, seconds: int) -> Optional[subprocess.Popen]:
    if not powershell_available():
        return None
    try:
        return subprocess.Popen(powershell_argv(popup_script(message, seconds)), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        log.warning("could not show the countdown popup: %s", e)
        return None


def parse_popup(returncode: int, out: str) -> str:
    """'now' (OK), 'cancel' (Cancel / closed), 'timeout', or 'error'."""
    if returncode != 0:
        return "error"
    val = (out or "").strip().splitlines()[-1:] or [""]
    return {"1": "now", "2": "cancel", "-1": "timeout"}.get(val[0].strip(), "error")
