"""Non-interactive permission / setup checks (`doctor`, and `watch` preflight).

Nothing here can show a prompt: on macOS, Automation state is read with
askUserIfNeeded=false, notification state from Notification Center's prefs,
and sudo with `sudo -n -l`. On Windows, `powercfg /a` and a registry read
need no elevation. Checks depend on the action: e.g. the sudo rule and the
graceful-quit grants only matter for `shutdown` on macOS.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import actions, macos, shutdown, windows
from .config import Config
from .system import LINUX, MACOS, PRETTY, WINDOWS

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


def resolve_action(cfg: Config) -> "tuple[Optional[str], Optional[Check]]":
    try:
        action = actions.resolve(cfg.action, cfg.platform)
    except actions.ActionError as e:
        return None, Check("action", FAIL, str(e), "set GOOD_NIGHT_ACTION (or --action) to a supported action")
    default = actions.DEFAULTS[cfg.platform]
    src = "default" if not cfg.action else "GOOD_NIGHT_ACTION/--action"
    step = actions.plan(action, cfg.platform)
    return action, Check("action", PASS, f"{action} ({src}; {PRETTY[cfg.platform]} default is {default}) "
                                         f"-> {step.display}")


def run_checks(cfg: Config, dry_run: bool = False, action: Optional[str] = None) -> List[Check]:
    checks: List[Check] = []
    if action is None:
        action, c = resolve_action(cfg)
        checks.append(c)
    else:
        checks.append(Check("action", PASS, f"{action} -> {actions.plan(action, cfg.platform).display}"))
    if cfg.platform == MACOS:
        checks += _macos_checks(cfg, dry_run, action)
    elif cfg.platform == WINDOWS:
        checks += _windows_checks(cfg, dry_run, action)
    else:
        checks += _linux_checks(cfg, dry_run, action)
    checks += _data_checks(cfg)
    return checks


# ---------------------------------------------------------------- macOS


def _macos_checks(cfg: Config, dry_run: bool, action: Optional[str]) -> List[Check]:
    checks: List[Check] = []
    shutting_down = action == actions.SHUTDOWN
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

    if action is not None and actions.needs_sudo(action, MACOS):
        ok, msg = shutdown.sudo_ok()
        if ok:
            checks.append(Check("sudo shutdown rule", PASS, "`sudo -n /sbin/shutdown -h now` needs no password"))
        else:
            checks.append(Check("sudo shutdown rule", WARN if dry_run else FAIL,
                                f"passwordless shutdown not allowed ({msg or 'sudo -n failed'})",
                                "run `./bin/good-night setup --action shutdown` (or ./scripts/setup-sudo.sh)"))
    elif action is not None:
        checks.append(Check("sudo shutdown rule", INFO,
                            f"not needed: `{actions.plan(action, MACOS).display}` runs without sudo"))

    if action is not None and action != actions.SHUTDOWN and not Path("/usr/bin/pmset").exists():
        checks.append(Check("pmset", FAIL, "/usr/bin/pmset not found"))

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
                        "keeps the Mac awake while watching (stopped before the action); needs no permission"))

    if not shutting_down:
        checks.append(Check("quit apps", INFO, f"not needed: {action or 'this action'} keeps your apps open"))
    elif cfg.quit_apps:
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
                            "optional: `setup --action shutdown` asks once to allow graceful quit for the apps "
                            f"open now; change later in {AUTOMATION_PANE} > {host.name if host else 'your terminal'}"
                            if sigterm else ""))
    else:
        checks.append(Check("quit apps", INFO, "disabled (GOOD_NIGHT_QUIT_APPS=0); shutdown stops apps itself"))
    return checks


# ---------------------------------------------------------------- Windows


def _windows_checks(cfg: Config, dry_run: bool, action: Optional[str],
                    states: Optional[windows.PowerStates] = None) -> List[Check]:
    checks: List[Check] = []
    need_states = action in (actions.HIBERNATE, actions.SLEEP)
    if need_states and states is None:
        states = windows.power_states()

    if action == actions.HIBERNATE:
        hib, why = windows.hibernation(states)
        if hib:
            checks.append(Check("hibernation", PASS, why))
        elif hib is False:
            checks.append(Check("hibernation", WARN if dry_run else FAIL,
                                f"hibernation is not available: {why}", windows.HIBERNATE_FIX))
        else:
            checks.append(Check("hibernation", WARN, why,
                                "check `powercfg /a`; if Hibernate is missing: " + windows.HIBERNATE_FIX))

    if action == actions.SLEEP:
        if states is not None and states.parsed:
            if states.s3:
                checks.append(Check("sleep", PASS, "Standby (S3) is available"))
            elif states.modern_standby:
                checks.append(Check("sleep", WARN,
                                    "this PC uses Modern Standby (S0); SetSuspendState may not sleep it, "
                                    "or it may keep running network tasks while 'asleep'",
                                    "consider GOOD_NIGHT_ACTION=hibernate (if `powercfg /a` lists it)"))
            else:
                checks.append(Check("sleep", WARN if dry_run else FAIL, "powercfg /a lists no standby state",
                                    "use GOOD_NIGHT_ACTION=hibernate or shutdown"))
            hib, _ = windows.hibernation(states, registry=lambda: None)
            if hib:
                checks.append(Check("sleep vs hibernate", INFO,
                                    "hibernation is on; good-night calls SetSuspendState(Hibernate=FALSE) "
                                    "directly, so this really sleeps (unlike `rundll32 powrprof.dll,SetSuspendState`)"))
        else:
            checks.append(Check("sleep", WARN, "could not read `powercfg /a`; can't confirm standby is available"))

    if action in (actions.SHUTDOWN, actions.HIBERNATE):
        exe = actions.windows_exe("shutdown.exe")
        checks.append(Check("shutdown.exe", PASS if Path(exe).exists() else (WARN if dry_run else FAIL), exe))

    ps = windows.powershell_available()
    if action == actions.SHUTDOWN:
        if cfg.quit_apps:
            checks.append(Check("close apps", PASS if ps else WARN,
                                "app windows get CloseMainWindow (like clicking X), then `shutdown /f` "
                                "closes the rest; unsaved work in those apps is lost" if ps
                                else "PowerShell not found; skipping the polite close, `shutdown /f` closes apps"))
        else:
            checks.append(Check("close apps", INFO, "disabled (GOOD_NIGHT_QUIT_APPS=0); `shutdown /f` closes apps"))
    elif action is not None:
        checks.append(Check("close apps", INFO, f"not needed: {action} keeps your apps open"))

    if cfg.dialog:
        checks.append(Check("countdown dialog", PASS if ps else WARN,
                            "WScript.Shell popup with OK/Cancel; needs no permission" if ps
                            else "PowerShell not found; console countdown only"))
    else:
        checks.append(Check("countdown dialog", INFO, "disabled (GOOD_NIGHT_DIALOG=0); console countdown only"))
    checks.append(Check("notifications", INFO, "no toast on Windows; the countdown popup and console log instead"))
    checks.append(Check("keep awake", PASS if cfg.caffeinate else INFO,
                        "SetThreadExecutionState keeps the PC awake while watching" if cfg.caffeinate
                        else "disabled (GOOD_NIGHT_CAFFEINATE=0)"))
    checks.append(Check("admin rights", INFO, "not needed: no step uses UAC/elevation"))
    return checks


# ---------------------------------------------------------------- Linux


def _linux_checks(cfg: Config, dry_run: bool, action: Optional[str]) -> List[Check]:
    checks = [Check("platform", INFO, "Linux support is minimal: systemctl, console countdown, no app closing")]
    sc = shutil.which("systemctl")
    checks.append(Check("systemctl", PASS if sc else (WARN if dry_run else FAIL),
                        sc or "systemctl not found (systemd required)"))
    return checks


# ---------------------------------------------------------------- common


def _data_checks(cfg: Config) -> List[Check]:
    checks: List[Check] = []
    for name, path in (("claude", cfg.claude_dir), ("cursor", cfg.cursor_dir), ("codex", cfg.codex_dir)):
        if name not in cfg.detectors:
            continue
        if not path.exists():
            checks.append(Check(f"{name} data", INFO, f"{path} not found (agent not installed?)"))
            continue
        try:
            os.listdir(path)
            checks.append(Check(f"{name} data", PASS, f"{path} readable"))
        except OSError as e:
            fix = ("give the host app access in System Settings > Privacy & Security > Files & Folders"
                   if cfg.platform == MACOS else "check the folder's permissions")
            checks.append(Check(f"{name} data", FAIL, f"cannot read {path}: {e}", fix))
    return checks


def print_checks(checks: List[Check]) -> None:
    for c in checks:
        print(f"{c.status:4}  {c.name:18}  {c.detail}")
        if c.fix and c.status in (FAIL, WARN, PASS):
            print(f"      fix: {c.fix}" if c.status != PASS else f"      note: {c.fix}")


def failed(checks: List[Check]) -> List[Check]:
    return [c for c in checks if c.status == FAIL]
