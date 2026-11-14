"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import List, Tuple

from . import __version__, actions, config, doctor, macos, night, shutdown, windows
from .detectors import BUSY, IDLE, INFO, Detector, Session, build
from .system import MACHINE, MACOS, PRETTY, WINDOWS
from .util import human_age

log = logging.getLogger("good-night")


def setup_logging(cfg: config.Config, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("good-night")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(fmt)
    root.addHandler(h)
    try:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(cfg.log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


def collect(detectors: List[Detector]) -> List[Tuple[Detector, List[Session], str]]:
    out = []
    for d in detectors:
        if not d.available():
            out.append((d, [], f"not found on this {MACHINE.get(d.cfg.platform, 'computer')}"))
            continue
        try:
            out.append((d, d.sessions(), ""))
        except Exception as e:  # a broken detector must not crash the watcher
            log.exception("detector %s failed", d.name)
            # Be conservative: an error counts as busy.
            out.append((d, [Session(d.name, "error", d.title, BUSY, f"detector error: {e}")], ""))
    return out


def busy_sessions(results) -> List[Session]:
    return [s for _, sessions, _ in results for s in sessions if s.state == BUSY]


# ---------------------------------------------------------------- status

def cmd_status(cfg: config.Config, args) -> int:
    results = collect(build(cfg))
    if args.json:
        data = []
        for d, sessions, note in results:
            data.append({
                "detector": d.name,
                "note": note,
                "sessions": [{
                    "id": s.session_id, "label": s.label, "state": s.state, "reason": s.reason,
                    "last_activity": s.last_activity.isoformat() if s.last_activity else None,
                    "age_seconds": s.age_seconds, "path": str(s.path) if s.path else None,
                } for s in sessions],
            })
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 1 if busy_sessions(results) else 0

    order = {BUSY: 0, IDLE: 1, INFO: 2}
    for d, sessions, note in results:
        n_busy = sum(1 for s in sessions if s.state == BUSY)
        n_idle = sum(1 for s in sessions if s.state == IDLE)
        head = f"{d.title} [{d.name}]"
        if note:
            print(f"{head}: {note}")
            continue
        print(f"{head}: {n_busy} busy, {n_idle} idle (sessions touched in last {cfg.lookback_hours}h)")
        shown = sorted(sessions, key=lambda s: (order.get(s.state, 9), s.age_seconds or 0))
        idle_shown = 0
        for s in shown:
            if s.state == IDLE:
                idle_shown += 1
                if idle_shown > 8 and not args.all:
                    continue
            mark = {BUSY: "BUSY", IDLE: "idle", INFO: "info"}.get(s.state, s.state)
            print(f"  {mark:4}  {human_age(s.age_seconds):>6} ago  {s.label[:48]:48}  {s.reason}")
        if n_idle > 8 and not args.all:
            print(f"  ... {n_idle - 8} more idle sessions (use --all)")
    busy = busy_sessions(results)
    try:
        what = actions.resolve(cfg.action, cfg.platform)
    except actions.ActionError as e:
        what = "the action"
        print(f"\nwarning: {e}")
    print()
    print(f"=> BUSY: {what} would wait" if busy else f"=> ALL IDLE: {what} would proceed")
    return 1 if busy else 0


# ---------------------------------------------------------------- watch

def cmd_watch(cfg: config.Config, args) -> int:
    try:
        action = actions.resolve(cfg.action, cfg.platform)
    except actions.ActionError as e:
        log.error("%s", e)
        return 2
    detectors = build(cfg)
    mode = "DRY RUN" if cfg.dry_run else "LIVE"
    log.info("good-night %s starting (%s, %s): action=%s detectors=%s interval=%ds idle_checks=%d grace=%ds stale=%dm",
             __version__, mode, PRETTY[cfg.platform], action, ",".join(d.name for d in detectors), cfg.interval,
             cfg.idle_checks, cfg.grace, cfg.stale_minutes)
    # Preflight: refuse to start if anything later tonight could show a
    # permission prompt or fail. Every check here is non-interactive.
    checks = doctor.run_checks(cfg, dry_run=cfg.dry_run, action=action)
    bad = doctor.failed(checks)
    if bad:
        log.error("preflight failed; not starting so nothing can prompt or fail at night:")
        for c in bad:
            log.error("  FAIL %s: %s", c.name, c.detail)
            if c.fix:
                log.error("       fix: %s", c.fix)
        log.error("Run `good-night doctor` for details, or `good-night setup` once.")
        return 2
    log.info("preflight OK (%s)", ", ".join(f"{c.name}={c.status}" for c in checks if c.status != doctor.INFO))
    keep = night.KeepAwake(cfg)
    keep.start()
    try:
        return _watch_loop(cfg, args, detectors, action, keep)
    finally:
        keep.stop()


def _watch_loop(cfg, args, detectors, action, keep) -> int:
    streak = 0
    while True:
        results = collect(detectors)
        busy = busy_sessions(results)
        if busy:
            streak = 0
            log.info("busy: %s", "; ".join(f"{s.detector}:{s.label} ({s.reason})" for s in busy[:6])
                     + (f" ... +{len(busy) - 6}" if len(busy) > 6 else ""))
        else:
            streak += 1
            log.info("all idle (%d/%d)", streak, cfg.idle_checks)
        if streak >= cfg.idle_checks:
            if not night.grace_period(cfg, action):
                return 3
            # Last-second re-check: something may have started during the grace period.
            again = busy_sessions(collect(detectors))
            if again:
                log.info("activity during grace period (%s); back to watching", again[0].label)
                streak = 0
            else:
                ok = night.perform(cfg, action, keep)
                if ok and not cfg.dry_run and action == actions.SLEEP:
                    log.info("woke up again; good-night is done (run `watch` again for another night)")
                return 0 if ok else 2
        if args.once:
            return 1 if busy else 0
        time.sleep(cfg.interval)


# ---------------------------------------------------------------- misc

def cmd_check_sudo(cfg: config.Config, args) -> int:
    if cfg.platform != MACOS:
        print(f"Not needed on {PRETTY[cfg.platform]}: no good-night action uses sudo there.")
        return 0
    ok, msg = shutdown.sudo_ok()
    if ok:
        print("OK: `sudo -n /sbin/shutdown -h now` is allowed without a password.")
        return 0
    print("NOT CONFIGURED: `sudo -n /sbin/shutdown -h now` would need a password.")
    if msg:
        print(f"  sudo said: {msg}")
    print("  Fix: ./scripts/setup-sudo.sh   (see README, 'One-time sudo setup')")
    return 1


def cmd_doctor(cfg: config.Config, args) -> int:
    print(f"good-night {__version__} on {PRETTY[cfg.platform]}")
    checks = doctor.run_checks(cfg, dry_run=False)
    doctor.print_checks(checks)
    bad = doctor.failed(checks)
    print()
    if bad:
        print(f"=> {len(bad)} problem(s); `watch` will refuse to start. Fix the above"
              + (", or run ./bin/good-night setup" if cfg.platform == MACOS else ""))
        return 1
    print("=> ready: `watch` will not show any permission prompt")
    return 0


def _osascript(script: str, timeout: int = 60) -> "tuple[int, str]":
    try:
        p = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def cmd_setup(cfg: config.Config, args) -> int:
    """One-time interactive setup: every password / permission prompt happens
    here, so `watch` never has to show one. Only what the chosen action needs."""
    try:
        action = actions.resolve(cfg.action, cfg.platform)
    except actions.ActionError as e:
        print(f"good-night: {e}")
        return 2
    if cfg.platform == MACOS:
        return _setup_macos(cfg, args, action)
    print(f"good-night setup on {PRETTY[cfg.platform]} (action: {action})")
    print("Nothing to grant here: no step needs admin rights or a permission prompt.\n")
    if cfg.platform == WINDOWS:
        if action == actions.HIBERNATE:
            hib, why = windows.hibernation()
            print(f"hibernation: {'available' if hib else 'NOT available' if hib is False else 'unknown'} ({why})")
            if not hib:
                print(f"  -> {windows.HIBERNATE_FIX}")
        if cfg.dialog and windows.powershell_available():
            print("showing a sample countdown popup (closes itself after 10s) ...")
            proc = windows.countdown_popup("This is the good-night countdown popup. At night it says how long "
                                           "until the PC acts, with OK (now) and Cancel.", 10)
            if proc is not None:
                try:
                    proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
    print("\n== verification (non-interactive) ==")
    return cmd_doctor(cfg, args)


def _setup_macos(cfg: config.Config, args, action: str) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    host = macos.host_app()
    hname = host.name if host else "this terminal"
    print(f"good-night setup (action: {action}; running inside {hname})")
    print(f"macOS grants permissions per app: run `watch` from {hname} too, or rerun setup there.\n")
    shutting_down = action == actions.SHUTDOWN

    # 1. sudoers rule (shutdown only)
    print("[1/4] passwordless `sudo /sbin/shutdown -h now`")
    if not actions.needs_sudo(action, MACOS):
        print(f"      not needed for {action} (`{actions.plan(action, MACOS).display}` needs no sudo).")
        print("      If you switch to GOOD_NIGHT_ACTION=shutdown later, run `setup --action shutdown` once.")
    else:
        ok, _ = shutdown.sudo_ok()
        if ok:
            print("      already set up")
        elif args.skip_sudo:
            print("      skipped (--skip-sudo)")
        else:
            rc = subprocess.call([os.path.join(root, "scripts", "setup-sudo.sh")])
            if rc != 0:
                print("      sudo setup failed; fix it and rerun setup")
                return 1

    # 2. Automation grants for graceful quit (shutdown only; SIGTERM otherwise)
    print("\n[2/4] graceful quit permission for the apps open right now")
    if not shutting_down:
        print(f"      not needed for {action}: your apps stay open")
    elif not cfg.quit_apps or args.no_app_grants:
        print("      skipped; apps will get SIGTERM, which needs no permission")
    else:
        print(f"      macOS will ask \"{hname} wants to control <app>\" once per app.")
        print("      Allow = graceful quit at night; Don't Allow = SIGTERM instead. Neither prompts again.")
        for a in doctor.quit_targets(cfg):
            st = macos.automation_permission(a.bundle_id, ask=False)
            if st == macos.ERR_AE_EVENT_WOULD_REQUIRE_CONSENT:
                st = macos.automation_permission(a.bundle_id, ask=True)
            print(f"      {a.name:28} {macos.PERMISSION_TEXT.get(st, st)}")
        print("      Apps you open later just get SIGTERM; rerun setup any time to allow them too.")

    # 3. notifications
    print("\n[3/4] notifications")
    if cfg.notify:
        rc, out = _osascript('display notification "Test notification from good-night setup" '
                             'with title "good-night" sound name "Glass"', timeout=30)
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        (cfg.state_dir / "notifications-initialized").touch()
        print("      sent a test notification" + ("" if rc == 0 else f" (osascript: {out})"))
        print("      if macOS asks whether Script Editor may send notifications, choose Allow")
    else:
        print("      disabled (GOOD_NIGHT_NOTIFY=0)")

    # 4. countdown dialog (needs no permission; shown so you know what it looks like)
    print("\n[4/4] countdown dialog (no permission needed)")
    if cfg.dialog:
        verb = actions.words(action)[0]
        rc, out = _osascript('display dialog "This is the good-night countdown dialog. '
                             f'At night it says how many seconds until your Mac will {verb}, with a Cancel button." '
                             'with title "good-night setup" buttons {"OK"} default button "OK" '
                             'giving up after 15', timeout=30)
        print("      ok" if rc == 0 else f"      dialog failed: {out}")
    else:
        print("      disabled (GOOD_NIGHT_DIALOG=0)")

    print("\n== verification (non-interactive) ==")
    return cmd_doctor(cfg, args)


def cmd_actions(cfg: config.Config, args) -> int:
    m = actions.matrix()
    for os_name in ("macos", "windows", "linux"):
        print(f"{PRETTY[os_name]} (default: {actions.DEFAULTS[os_name]})"
              + ("  <- this machine" if os_name == cfg.platform else ""))
        for a in actions.ACTIONS:
            print(f"  {a:12} {m[a][os_name]}")
    return 0


def cmd_cancel(cfg: config.Config, args) -> int:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.cancel_file.touch()
    print(f"cancel requested ({cfg.cancel_file}); a pending countdown will stop.")
    return 0


def _fix_console() -> None:
    """Windows consoles may not be UTF-8; never crash on a session title."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _fix_console()
    action_help = (f"what to do when all agents are idle: {', '.join(actions.ACTIONS)} "
                   "(default: sleep on macOS, hibernate on Windows; env GOOD_NIGHT_ACTION)")
    ap = argparse.ArgumentParser(prog="good-night",
                                 description="Put your computer to sleep, hibernate or shut it down "
                                             "once your AI coding agents are idle.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("watch", help="poll detectors and run the action when all idle (default)")
    p.add_argument("--action", help=action_help)
    p.add_argument("--dry-run", action="store_true", help="log what would happen; never act")
    p.add_argument("--once", action="store_true", help="single check, no sleep loop (for testing)")
    p = sub.add_parser("status", help="show every detector's sessions and busy/idle")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="list all idle sessions")
    p.add_argument("--action", help=action_help)
    sub.add_parser("check-sudo", help="macOS: verify the passwordless shutdown sudoers rule")
    p = sub.add_parser("doctor", help="non-interactive check that `watch` will never prompt or fail")
    p.add_argument("--action", help=action_help)
    p = sub.add_parser("setup", help="one-time setup: whatever the action needs (sudo rule, prompts), then doctor")
    p.add_argument("--action", help=action_help)
    p.add_argument("--skip-sudo", action="store_true", help="don't install the sudoers rule")
    p.add_argument("--no-app-grants", action="store_true",
                   help="don't ask for graceful-quit permission; always use SIGTERM")
    sub.add_parser("cancel", help="cancel a running countdown")
    sub.add_parser("actions", help="list the actions available on each OS")
    args = ap.parse_args(argv)

    cfg = config.load()
    if getattr(args, "action", None):
        cfg.action = args.action
    cmd = args.cmd or "watch"
    if cmd == "watch":
        if getattr(args, "dry_run", False):
            cfg.dry_run = True
        if not hasattr(args, "once"):
            args.once = False
        setup_logging(cfg, args.verbose)
        try:
            return cmd_watch(cfg, args)
        except KeyboardInterrupt:
            log.info("stopped by user")
            return 130
    if cmd == "status":
        return cmd_status(cfg, args)
    if cmd == "check-sudo":
        return cmd_check_sudo(cfg, args)
    if cmd == "doctor":
        return cmd_doctor(cfg, args)
    if cmd == "setup":
        if not hasattr(args, "skip_sudo"):
            args.skip_sudo = False
        return cmd_setup(cfg, args)
    if cmd == "cancel":
        return cmd_cancel(cfg, args)
    if cmd == "actions":
        return cmd_actions(cfg, args)
    ap.error(f"unknown command {cmd}")
    return 2
