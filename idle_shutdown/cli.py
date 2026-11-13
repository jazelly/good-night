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

from . import __version__, config, shutdown
from .detectors import BUSY, IDLE, INFO, Detector, Session, build
from .util import human_age

log = logging.getLogger("idle-shutdown")


def setup_logging(cfg: config.Config, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("idle-shutdown")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(fmt)
    root.addHandler(h)
    try:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(cfg.log_file)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


def collect(detectors: List[Detector]) -> List[Tuple[Detector, List[Session], str]]:
    out = []
    for d in detectors:
        if not d.available():
            out.append((d, [], "not found on this Mac"))
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
    print()
    print("=> BUSY: shutdown would wait" if busy else "=> ALL IDLE: shutdown would proceed")
    return 1 if busy else 0


# ---------------------------------------------------------------- watch

def _start_caffeinate() -> None:
    """Keep the Mac awake while we watch; otherwise it may sleep and never
    reach the shutdown. Exits automatically when we exit (-w pid)."""
    try:
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("could not start caffeinate: %s", e)


def grace_period(cfg: config.Config) -> bool:
    """Return True to proceed with shutdown, False if the user cancelled."""
    if cfg.grace <= 0:
        return True
    try:
        cfg.cancel_file.unlink()
    except FileNotFoundError:
        pass
    log.info("all idle; shutting down in %ds. Cancel: Ctrl-C, the dialog, or `idle-shutdown cancel`", cfg.grace)
    if cfg.notify:
        shutdown.notify("idle-shutdown", f"All agents idle. Shutting down in {cfg.grace}s.")
    dialog = shutdown.countdown_dialog(cfg.grace) if cfg.dialog else None
    deadline = time.time() + cfg.grace
    try:
        while time.time() < deadline:
            if cfg.cancel_file.exists():
                log.info("cancel file found; shutdown cancelled")
                return False
            if dialog is not None and dialog.poll() is not None:
                out, err = dialog.communicate()
                if dialog.returncode != 0 or "Cancel" in (out or ""):
                    if "-128" in (err or "") or "Cancel" in (out or "") or "cancel" in (err or "").lower():
                        log.info("shutdown cancelled from the dialog")
                        return False
                    log.warning("dialog failed (%s); continuing countdown", (err or "").strip())
                elif "Shut down now" in (out or ""):
                    log.info("user chose 'Shut down now'")
                    return True
                dialog = None
            time.sleep(1)
    finally:
        if dialog is not None and dialog.poll() is None:
            dialog.kill()
    return True


def cmd_watch(cfg: config.Config, args) -> int:
    detectors = build(cfg)
    mode = "DRY RUN" if cfg.dry_run else "LIVE"
    log.info("idle-shutdown %s starting (%s): detectors=%s interval=%ds idle_checks=%d grace=%ds stale=%dm",
             __version__, mode, ",".join(d.name for d in detectors), cfg.interval, cfg.idle_checks,
             cfg.grace, cfg.stale_minutes)
    if not cfg.dry_run:
        ok, msg = shutdown.sudo_ok()
        if not ok:
            log.error("sudo is not set up for passwordless `/sbin/shutdown -h now`:\n  %s", msg)
            log.error("Run ./scripts/setup-sudo.sh once (see README), or use DRY_RUN=1.")
            return 2
    if cfg.caffeinate:
        _start_caffeinate()

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
            if not grace_period(cfg):
                return 3
            # Last-second re-check: something may have started during the grace period.
            again = busy_sessions(collect(detectors))
            if again:
                log.info("activity during grace period (%s); back to watching", again[0].label)
                streak = 0
            else:
                if not cfg.dry_run:
                    ok, msg = shutdown.sudo_ok()
                    if not ok:
                        log.error("sudo check failed right before shutdown: %s", msg)
                        shutdown.notify("idle-shutdown: SHUTDOWN FAILED", "passwordless sudo for shutdown is not set up", "Basso")
                        return 2
                if cfg.notify:
                    shutdown.notify("idle-shutdown", "Shutting down now." if not cfg.dry_run else "DRY RUN: would shut down now.")
                if cfg.quit_apps:
                    shutdown.quit_apps(cfg.keep_apps, cfg.quit_wait, cfg.dry_run)
                return 0 if shutdown.shutdown_now(cfg.dry_run) else 2
        if args.once:
            return 1 if busy else 0
        time.sleep(cfg.interval)


# ---------------------------------------------------------------- misc

def cmd_check_sudo(cfg: config.Config, args) -> int:
    ok, msg = shutdown.sudo_ok()
    if ok:
        print("OK: `sudo -n /sbin/shutdown -h now` is allowed without a password.")
        return 0
    print("NOT CONFIGURED: `sudo -n /sbin/shutdown -h now` would need a password.")
    if msg:
        print(f"  sudo said: {msg}")
    print("  Fix: ./scripts/setup-sudo.sh   (see README, 'One-time sudo setup')")
    return 1


def cmd_cancel(cfg: config.Config, args) -> int:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.cancel_file.touch()
    print(f"cancel requested ({cfg.cancel_file}); a pending countdown will stop.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="idle-shutdown",
                                 description="Shut your Mac down once your AI coding agents are idle.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("watch", help="poll detectors and shut down when all idle (default)")
    p.add_argument("--dry-run", action="store_true", help="log what would happen; never shut down")
    p.add_argument("--once", action="store_true", help="single check, no sleep loop (for testing)")
    p = sub.add_parser("status", help="show every detector's sessions and busy/idle")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="list all idle sessions")
    sub.add_parser("check-sudo", help="verify the passwordless shutdown sudoers rule")
    sub.add_parser("cancel", help="cancel a running countdown")
    args = ap.parse_args(argv)

    cfg = config.load()
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
    if cmd == "cancel":
        return cmd_cancel(cfg, args)
    ap.error(f"unknown command {cmd}")
    return 2
