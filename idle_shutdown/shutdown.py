"""Safe shutdown for macOS.

A plain "Shut Down" (System Events / AppleScript) is cancelled by any app that
shows a "save changes?" sheet or refuses to quit. So we:

  1. ask every regular GUI app to quit: AppleScript `quit` for apps the
     terminal is already allowed to automate, SIGTERM for the rest, so this
     step can never show a permission prompt,
  2. SIGTERM whatever is still running after a short wait,
  3. run `sudo -n /sbin/shutdown -h now`, which does not ask apps for
     permission. `-n` means sudo never prompts; if the sudoers rule is missing
     it fails immediately and we say so loudly.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from typing import List, Tuple

from . import macos

SHUTDOWN_CMD = ["/usr/bin/sudo", "-n", "/sbin/shutdown", "-h", "now"]
SUDO_CHECK_CMD = ["/usr/bin/sudo", "-n", "-l", "/sbin/shutdown", "-h", "now"]

log = logging.getLogger("idle-shutdown")


def sudo_ok() -> Tuple[bool, str]:
    """True if `sudo -n /sbin/shutdown -h now` would run without a password."""
    try:
        p = subprocess.run(SUDO_CHECK_CMD, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out


def notify(title: str, message: str, sound: str = "Glass") -> None:
    script = f'display notification {_as(message)} with title {_as(title)} sound name {_as(sound)}'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _as(s: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def countdown_dialog(seconds: int) -> subprocess.Popen:
    """Show a dialog that auto-confirms after `seconds`. Returns the process;
    stdout contains 'Cancel' if the user cancelled."""
    script = (
        f'display dialog {_as("All AI agents look idle. Your Mac will shut down in " + str(seconds) + " seconds.")} '
        f'with title "idle-shutdown" buttons {{"Cancel shutdown", "Shut down now"}} '
        f'default button "Shut down now" cancel button "Cancel shutdown" '
        f'with icon caution giving up after {seconds}'
    )
    return subprocess.Popen(
        ["/usr/bin/osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _ancestor_pids() -> set:
    pids = set()
    pid = os.getpid()
    for _ in range(64):
        pids.add(pid)
        try:
            out = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
            ppid = int(out.stdout.strip() or 0)
        except (OSError, ValueError):
            break
        if ppid <= 1:
            break
        pid = ppid
    return pids


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def quit_apps(keep: List[str], wait: int, dry_run: bool) -> None:
    """Quit foreground apps without ever triggering a permission prompt.

    Apps the host terminal is already allowed to automate get a normal
    AppleScript `quit` (they can save state / run their quit handlers). Every
    other app gets SIGTERM, which needs no permission at all. Anything still
    alive after `wait` seconds gets SIGTERM too.
    """
    keep_set = {k.lower() for k in keep}
    mine = _ancestor_pids()
    targets = [a for a in macos.running_apps() if a.bundle_id.lower() not in keep_set and a.pid not in mine]
    if not targets:
        log.info("no apps to quit")
        return
    procs = []
    for app in targets:
        perm = macos.automation_permission(app.bundle_id, ask=False) if app.bundle_id else -1
        how = "AppleScript quit" if perm == macos.NO_ERR else "SIGTERM (no Automation grant, so no prompt)"
        log.info("%s %s (%s, pid %d) via %s", "would quit" if dry_run else "quitting",
                 app.name, app.bundle_id or "no bundle id", app.pid, how)
        if dry_run:
            continue
        if perm == macos.NO_ERR:
            script = f"with timeout of {max(5, wait)} seconds\n tell application id {_as(app.bundle_id)} to quit\nend timeout"
            procs.append(subprocess.Popen(["/usr/bin/osascript", "-e", script],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        else:
            _term(app.pid)
    if dry_run:
        return
    deadline = time.time() + wait
    while time.time() < deadline and any(_alive(a.pid) for a in targets):
        time.sleep(1)
    for p in procs:
        if p.poll() is None:
            p.kill()
    leftovers = [a for a in targets if _alive(a.pid)]
    for a in leftovers:
        log.info("%s still running after %ds, sending SIGTERM (pid %d)", a.name, wait, a.pid)
        _term(a.pid)
    if leftovers:
        time.sleep(5)


def _term(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def shutdown_now(dry_run: bool) -> bool:
    cmd = " ".join(SHUTDOWN_CMD)
    if dry_run:
        log.info("DRY RUN: would run: %s", cmd)
        return True
    log.info("running: %s", cmd)
    p = subprocess.run(SHUTDOWN_CMD, capture_output=True, text=True)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        log.error("SHUTDOWN FAILED (exit %d): %s", p.returncode, msg)
        log.error("Is the sudoers rule installed? Run scripts/setup-sudo.sh, then `idle-shutdown check-sudo`.")
        notify("idle-shutdown: SHUTDOWN FAILED", msg or "sudo -n /sbin/shutdown failed", sound="Basso")
        return False
    return True
