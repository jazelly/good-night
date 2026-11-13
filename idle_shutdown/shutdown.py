"""Safe shutdown for macOS.

A plain "Shut Down" (System Events / AppleScript) is cancelled by any app that
shows a "save changes?" sheet or refuses to quit. So we:

  1. politely ask every regular GUI app to quit (lets them save state),
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


def gui_apps() -> List[Tuple[int, str, str]]:
    """(pid, bundle id, name) of every foreground (Dock) app."""
    script = (
        'set out to ""\n'
        'tell application "System Events"\n'
        '  repeat with p in (every process whose background only is false)\n'
        '    set bid to ""\n'
        '    try\n'
        '      set bid to bundle identifier of p\n'
        '    end try\n'
        '    set out to out & (unix id of p) & tab & bid & tab & (name of p) & linefeed\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    try:
        p = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("could not list apps: %s", e)
        return []
    if p.returncode != 0:
        log.warning("could not list apps (grant Automation permission for System Events?): %s", p.stderr.strip())
        return []
    apps = []
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                apps.append((int(parts[0]), parts[1], parts[2]))
            except ValueError:
                continue
    return apps


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def quit_apps(keep: List[str], wait: int, dry_run: bool) -> None:
    keep_set = {k.lower() for k in keep}
    mine = _ancestor_pids()
    targets = [a for a in gui_apps() if a[1].lower() not in keep_set and a[0] not in mine]
    if not targets:
        log.info("no apps to quit")
        return
    for pid, bid, name in targets:
        log.info("%s quit %s (%s, pid %d)", "would" if dry_run else "asking to", name, bid or "no bundle id", pid)
    if dry_run:
        return
    procs = []
    for pid, bid, name in targets:
        target = f"application id {_as(bid)}" if bid else f"application {_as(name)}"
        script = f"with timeout of {max(5, wait)} seconds\n tell {target} to quit\nend timeout"
        procs.append(subprocess.Popen(["/usr/bin/osascript", "-e", script],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    deadline = time.time() + wait
    while time.time() < deadline and any(_alive(pid) for pid, _, _ in targets):
        time.sleep(1)
    for p in procs:
        if p.poll() is None:
            p.kill()
    leftovers = [(pid, name) for pid, _, name in targets if _alive(pid)]
    for pid, name in leftovers:
        log.info("%s did not quit in %ds, sending SIGTERM (pid %d)", name, wait, pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    if leftovers:
        time.sleep(5)


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
