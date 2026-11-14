"""Platform dispatch for everything `watch` does once the agents are idle:
keep the machine awake while watching, notify, show a cancellable countdown,
close apps (shutdown only), and run the action.

macOS-only pieces (osascript, lsappinfo, caffeinate, sudo) live in
shutdown.py / macos.py and are only touched when cfg.platform is macOS;
Windows pieces live in windows.py.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

from . import actions, shutdown, windows
from .config import Config
from .system import LINUX, MACHINE, MACOS, WINDOWS

log = logging.getLogger("good-night")


# ---------------------------------------------------------------- keep awake

class KeepAwake:
    """caffeinate on macOS, SetThreadExecutionState on Windows. Stopped right
    before the action so it can't interfere with sleep."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: Optional[subprocess.Popen] = None
        self.win = False

    def start(self) -> None:
        if not self.cfg.caffeinate:
            return
        if self.cfg.platform == MACOS:
            try:
                self.proc = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as e:
                log.warning("could not start caffeinate: %s", e)
        elif self.cfg.platform == WINDOWS:
            self.win = windows.keep_awake(True)
            if not self.win:
                log.warning("SetThreadExecutionState failed; the PC may fall asleep on its own while watching")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self.win:
            windows.keep_awake(False)
            self.win = False


# ---------------------------------------------------------------- notify


def notify(cfg: Config, title: str, message: str, failure: bool = False) -> None:
    if not cfg.notify:
        return
    if cfg.platform == MACOS:
        shutdown.notify(title, message, "Basso" if failure else "Glass")
    else:
        # No toast on Windows/Linux (that would need an AppUserModelID or
        # notify-send); the countdown popup / console is the notification.
        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------- countdown


def _start_dialog(cfg: Config, action: str):
    if not cfg.dialog:
        return None
    verb = actions.words(action)[0]
    if cfg.platform == MACOS:
        try:
            return shutdown.countdown_dialog(cfg.grace, action)
        except OSError as e:
            log.warning("could not show the countdown dialog: %s", e)
            return None
    if cfg.platform == WINDOWS:
        return windows.countdown_popup(
            f"All AI agents look idle. This PC will {verb} in {cfg.grace} seconds.\n\n"
            "OK = do it now, Cancel = stop good-night.", cfg.grace)
    return None


def _read_dialog(cfg: Config, proc, action: str) -> str:
    out, err = proc.communicate()
    if cfg.platform == MACOS:
        return shutdown.parse_dialog(proc.returncode, out, err, action)
    return windows.parse_popup(proc.returncode, out)


def grace_period(cfg: Config, action: str) -> bool:
    """Return True to proceed, False if the user cancelled."""
    if cfg.grace <= 0:
        return True
    try:
        cfg.cancel_file.unlink()
    except FileNotFoundError:
        pass
    _, _, ing = actions.words(action)
    log.info("all idle; %s in %ds. Cancel: Ctrl-C, the dialog, or `good-night cancel`", ing, cfg.grace)
    notify(cfg, "good-night", f"All agents idle. {ing.capitalize()} in {cfg.grace}s.")
    dialog = _start_dialog(cfg, action)
    deadline = time.time() + cfg.grace
    next_tick = 0.0
    try:
        while time.time() < deadline:
            if cfg.cancel_file.exists():
                log.info("cancel file found; %s cancelled", action)
                return False
            if dialog is not None and dialog.poll() is not None:
                answer = _read_dialog(cfg, dialog, action)
                dialog = None
                if answer == "cancel":
                    log.info("%s cancelled from the dialog", action)
                    return False
                if answer == "now":
                    log.info("user chose '%s'", actions.words(action)[1])
                    return True
                if answer == "error":
                    log.warning("countdown dialog failed; continuing the countdown")
            left = deadline - time.time()
            if cfg.platform != MACOS and time.time() >= next_tick:
                log.info("%s in %ds ...", ing, max(0, int(left + 0.5)))
                next_tick = time.time() + 10
            time.sleep(1)
    finally:
        if dialog is not None and dialog.poll() is None:
            dialog.kill()
    return True


# ---------------------------------------------------------------- the action


def close_apps(cfg: Config) -> None:
    if cfg.platform == MACOS:
        shutdown.quit_apps(cfg.keep_apps, cfg.quit_wait, cfg.dry_run)
    elif cfg.platform == WINDOWS:
        windows.close_apps(cfg.keep_apps, cfg.quit_wait, cfg.dry_run)


def run_step(cfg: Config, action: str) -> bool:
    if cfg.platform == MACOS:
        return shutdown.run_action(action, cfg.dry_run)
    step = actions.plan(action, cfg.platform)
    if cfg.dry_run:
        log.info("DRY RUN: would run: %s", step.display)
        return True
    log.info("running: %s", step.display)
    if step.kind == "win32":
        try:
            ok = windows.WIN32_CALLS[step.call]()
        except (AttributeError, OSError) as e:
            log.error("%s FAILED: %s", action.upper(), e)
            return False
        if not ok:
            log.error("%s FAILED: %s returned FALSE", action.upper(), step.display)
        return ok
    argv = list(step.argv)
    if cfg.platform == LINUX and not os.path.isabs(argv[0]):
        argv[0] = shutil.which(argv[0]) or argv[0]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("%s FAILED: %s", action.upper(), e)
        return False
    if p.returncode != 0:
        msg = ((p.stderr or "") + (p.stdout or "")).strip()
        log.error("%s FAILED (exit %d): %s", action.upper(), p.returncode, msg)
        if action == actions.HIBERNATE and cfg.platform == WINDOWS:
            log.error("Is hibernation enabled? %s", windows.HIBERNATE_FIX)
        return False
    return True


def perform(cfg: Config, action: str, keep: Optional[KeepAwake] = None) -> bool:
    """Notify, close apps if the action needs it, stop keep-awake, act."""
    machine = MACHINE.get(cfg.platform, "computer")
    _, _, ing = actions.words(action)
    if actions.needs_sudo(action, cfg.platform) and not cfg.dry_run:
        ok, msg = shutdown.sudo_ok()
        if not ok:
            log.error("sudo check failed right before shutdown: %s", msg)
            notify(cfg, "good-night: SHUTDOWN FAILED", "passwordless sudo for shutdown is not set up", failure=True)
            return False
    notify(cfg, "good-night", (f"DRY RUN: would be {ing} now." if cfg.dry_run else f"{ing.capitalize()} now."))
    if actions.closes_apps(action) and cfg.quit_apps:
        close_apps(cfg)
    if keep is not None and not cfg.dry_run:
        keep.stop()
    ok = run_step(cfg, action)
    if not ok and cfg.platform != MACOS:  # shutdown.run_action already notified on macOS
        notify(cfg, f"good-night: {action} FAILED", f"the {machine} did not {actions.words(action)[0]}", failure=True)
    return ok
