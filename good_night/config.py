"""Configuration, read from environment variables (all optional)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from . import system


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"good-night: {name} must be an integer, got {raw!r}")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


# Apps we never ask to quit in the graceful step (Finder can't really quit;
# terminals may be hosting this very process). `shutdown` stops them anyway.
DEFAULT_KEEP_APPS = [
    "com.apple.finder",
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "com.mitchellh.ghostty",
    "dev.warp.Warp-Stable",
    "net.kovidgoyal.kitty",
    "org.alacritty",
    "com.github.wez.wezterm",
]


def _local_dirs(os_name: str) -> "tuple[Path, Path]":
    """(log file, state dir) in each OS's usual place."""
    home = Path.home()
    if os_name == system.MACOS:
        return home / "Library" / "Logs" / "good-night.log", home / "Library" / "Caches" / "good-night"
    if os_name == system.WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local")) / "good-night"
        return base / "good-night.log", base
    state = Path(os.environ.get("XDG_STATE_HOME") or (home / ".local" / "state")) / "good-night"
    return state / "good-night.log", state


def default_keep_apps(os_name: str) -> List[str]:
    if os_name == system.WINDOWS:
        from .windows import DEFAULT_KEEP_PROCESSES
        return list(DEFAULT_KEEP_PROCESSES)
    return list(DEFAULT_KEEP_APPS)


@dataclass
class Config:
    #: raw GOOD_NIGHT_ACTION / --action; "" = platform default (see actions.resolve)
    action: str = ""
    platform: str = field(default_factory=system.current)
    interval: int = 60
    idle_checks: int = 3
    detectors: List[str] = field(default_factory=lambda: ["claude", "cursor", "codex"])
    dry_run: bool = False
    grace: int = 60
    dialog: bool = True
    notify: bool = True
    quiet: int = 90
    stale_minutes: int = 30
    lookback_hours: int = 12
    quit_apps: bool = True
    quit_wait: int = 20
    keep_apps: List[str] = field(default_factory=lambda: list(DEFAULT_KEEP_APPS))
    caffeinate: bool = True
    claude_dir: Path = Path.home() / ".claude" / "projects"
    cursor_dir: Path = Path.home() / ".cursor" / "projects"
    codex_dir: Path = Path.home() / ".codex"
    log_file: Path = Path.home() / "Library" / "Logs" / "good-night.log"
    state_dir: Path = Path.home() / "Library" / "Caches" / "good-night"

    @property
    def cancel_file(self) -> Path:
        return self.state_dir / "cancel"


def load() -> Config:
    os_name = system.current()
    log_default, state_default = _local_dirs(os_name)
    claude_root = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_default = (Path(claude_root) if claude_root else Path.home() / ".claude") / "projects"
    codex_root = os.environ.get("CODEX_HOME")
    codex_default = Path(codex_root) if codex_root else Path.home() / ".codex"
    dry = _env_bool("GOOD_NIGHT_DRY_RUN", _env_bool("DRY_RUN", False))
    cfg = Config(
        action=os.environ.get("GOOD_NIGHT_ACTION", "").strip(),
        platform=os_name,
        interval=max(1, _env_int("GOOD_NIGHT_INTERVAL", 60)),
        idle_checks=max(1, _env_int("GOOD_NIGHT_IDLE_CHECKS", 3)),
        detectors=_env_list("GOOD_NIGHT_DETECTORS", ["claude", "cursor", "codex"]),
        dry_run=dry,
        grace=max(0, _env_int("GOOD_NIGHT_GRACE", 60)),
        dialog=_env_bool("GOOD_NIGHT_DIALOG", True),
        notify=_env_bool("GOOD_NIGHT_NOTIFY", True),
        quiet=max(0, _env_int("GOOD_NIGHT_QUIET", 90)),
        stale_minutes=max(1, _env_int("GOOD_NIGHT_STALE_MINUTES", 30)),
        lookback_hours=max(1, _env_int("GOOD_NIGHT_LOOKBACK_HOURS", 12)),
        quit_apps=_env_bool("GOOD_NIGHT_QUIT_APPS", True),
        quit_wait=max(0, _env_int("GOOD_NIGHT_QUIT_WAIT", 20)),
        keep_apps=default_keep_apps(os_name) + _env_list("GOOD_NIGHT_KEEP_APPS", []),
        caffeinate=_env_bool("GOOD_NIGHT_CAFFEINATE", True),
        claude_dir=Path(os.environ.get("GOOD_NIGHT_CLAUDE_DIR", str(claude_default))).expanduser(),
        cursor_dir=Path(
            os.environ.get("GOOD_NIGHT_CURSOR_DIR", str(Path.home() / ".cursor" / "projects"))
        ).expanduser(),
        codex_dir=Path(os.environ.get("GOOD_NIGHT_CODEX_DIR", str(codex_default))).expanduser(),
        log_file=Path(os.environ.get("GOOD_NIGHT_LOG", str(log_default))).expanduser(),
        state_dir=state_default,
    )
    return cfg
