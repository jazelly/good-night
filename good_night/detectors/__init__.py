"""Detector registry. To add an agent (e.g. Codex), write a Detector subclass
and register it here."""

from __future__ import annotations

from typing import Dict, List, Type

from ..config import Config
from .base import BUSY, IDLE, INFO, Detector, Session
from .claude_code import ClaudeCodeDetector
from .cursor import CursorDetector

REGISTRY: Dict[str, Type[Detector]] = {
    ClaudeCodeDetector.name: ClaudeCodeDetector,
    CursorDetector.name: CursorDetector,
}

ALIASES = {"claude-code": "claude", "claude_code": "claude", "cursor-agent": "cursor"}


def build(cfg: Config) -> List[Detector]:
    out = []
    for raw in cfg.detectors:
        key = ALIASES.get(raw.lower(), raw.lower())
        cls = REGISTRY.get(key)
        if cls is None:
            raise SystemExit(
                f"good-night: unknown detector {raw!r} (known: {', '.join(sorted(REGISTRY))})"
            )
        out.append(cls(cfg))
    return out


__all__ = ["BUSY", "IDLE", "INFO", "Detector", "Session", "REGISTRY", "build"]
