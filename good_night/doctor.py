"""Non-interactive permission / setup checks (`doctor`, and `watch` preflight).

Nothing here can show a macOS prompt: Automation state is read with
askUserIfNeeded=false, notification state from Notification Center's prefs,
and sudo with `sudo -n -l`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from . import macos, shutdown
from .config import Config

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"

AUTOMATION_PANE = "System Settings > Privacy & Security > Automation"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def quit_targets(cfg: Config, apps=None) -> List[macos.App]:
    keep = {k.lower() for k in cfg.keep_apps}
    mine = shutdown._ancestor_pids()
    apps = macos.running_apps() if apps is None else apps
    return [a for a in apps if a.bundle_id and a.bundle_id.lower() not in keep and a.pid not in mine]


def run_checks(cfg: Config, dry_run: bool = False) -> List[Check]:
    checks: List[Check] = []
    all_apps = macos.running_apps(foreground_only=False)

    host = macos.host_app(all_apps)
    if host:
        checks.append(Check("host app", INFO,
                            f"permissions are granted to {host.name} ({host.bundle_id}); "
                            "run `setup` and `watch` from this same app"))
    else:
        checks.append(Check("host app", WARN,
                            "could not identify the GUI app running this (ssh, tmux server or launchd?); "
                            "permissions are attributed to whatever app started it"))

    ok, msg = shutdown.sudo_ok()
    if ok:
        checks.append(Check("sudo shutdown rule", PASS, "`sudo -n /sbin/shutdown -h now` needs no password"))
    else:
        checks.append(Check("sudo shutdown rule", WARN if dry_run else FAIL,
                            f"passwordless shutdown not allowed ({msg or 'sudo -n failed'})",
                            "run `./bin/good-night setup` (or ./scripts/setup-sudo.sh)"))

    if cfg.notify:
        if macos.notifications_decided(cfg.state_dir):
            checks.append(Check("notifications", PASS, "osascript notifications already set up (no prompt)"))
        else:
            checks.append(Check("notifications", FAIL,
                                "the first osascript notification may ask for permission",
                                "run `./bin/good-night setup` once, or set GOOD_NIGHT_NOTIFY=0"))
    else:
        checks.append(Check("notifications", INFO, "disabled (GOOD_NIGHT_NOTIFY=0)"))

    checks.append(Check("countdown dialog", PASS if cfg.dialog else INFO,
                        "osascript `display dialog` needs no permission" if cfg.dialog
                        else "disabled (GOOD_NIGHT_DIALOG=0)"))

    checks.append(Check("caffeinate", PASS if Path("/usr/bin/caffeinate").exists() else WARN,
                        "keeps the Mac awake while watching; needs no permission"))

    for name, path in (("claude", cfg.claude_dir), ("cursor", cfg.cursor_dir)):
        if name not in cfg.detectors:
            continue
        if not path.exists():
            checks.append(Check(f"{name} data", INFO, f"{path} not found (agent not installed?)"))
            continue
        try:
            os.listdir(path)
            checks.append(Check(f"{name} data", PASS, f"{path} readable"))
        except OSError as e:
            checks.append(Check(f"{name} data", FAIL, f"cannot read {path}: {e}",
                                "give the host app access in System Settings > Privacy & Security > Files & Folders"))

    if cfg.quit_apps:
        graceful, sigterm = [], []
        for a in quit_targets(cfg, [x for x in all_apps if x.app_type == "Foreground"]):
            st = macos.automation_permission(a.bundle_id, ask=False)
            (graceful if st == macos.NO_ERR else sigterm).append(
                a.name if st == macos.NO_ERR else f"{a.name} ({macos.PERMISSION_TEXT.get(st, st)})")
        detail = (f"{len(graceful)} app(s) quit gracefully via AppleScript, "
                  f"{len(sigterm)} via SIGTERM; this step never prompts")
        if graceful:
            detail += "\n      graceful: " + ", ".join(graceful)
        if sigterm:
            detail += "\n      SIGTERM:  " + ", ".join(sigterm)
        checks.append(Check("quit apps", PASS, detail,
                            "optional: `setup` asks once to allow graceful quit for the apps open now; "
                            f"change later in {AUTOMATION_PANE} > {host.name if host else 'your terminal'}"
                            if sigterm else ""))
    else:
        checks.append(Check("quit apps", INFO, "disabled (GOOD_NIGHT_QUIT_APPS=0); shutdown stops apps itself"))
    return checks


def print_checks(checks: List[Check]) -> None:
    for c in checks:
        print(f"{c.status:4}  {c.name:18}  {c.detail}")
        if c.fix and c.status in (FAIL, WARN, PASS):
            print(f"      fix: {c.fix}" if c.status != PASS else f"      note: {c.fix}")


def failed(checks: List[Check]) -> List[Check]:
    return [c for c in checks if c.status == FAIL]
