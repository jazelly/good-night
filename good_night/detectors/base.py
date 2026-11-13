"""Detector interface. Add a new agent by subclassing Detector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ..config import Config
from ..util import now

BUSY = "busy"
IDLE = "idle"
INFO = "info"  # shown in `status` but never blocks shutdown


@dataclass
class Session:
    detector: str
    session_id: str
    label: str
    state: str
    reason: str
    last_activity: Optional[datetime] = None
    path: Optional[Path] = None

    @property
    def age_seconds(self) -> Optional[float]:
        if self.last_activity is None:
            return None
        return max(0.0, (now() - self.last_activity).total_seconds())


class Detector:
    #: short id used in GOOD_NIGHT_DETECTORS
    name = "base"
    #: human-readable name
    title = "Base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        """Whether this agent appears to be installed / has data on this Mac."""
        raise NotImplementedError

    def sessions(self) -> List[Session]:
        """Return sessions touched within the lookback window."""
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------

    def since(self) -> datetime:
        return now() - timedelta(hours=self.cfg.lookback_hours)

    def finalize(self, s: Session) -> Session:
        """Apply the shared quiet-window and stale rules.

        * idle but with activity inside the quiet window -> busy (it may be
          between two steps; we'd rather wait one more poll).
        * busy but with no activity for stale_minutes -> idle (crashed /
          killed session, or one stuck on a permission prompt nobody will
          answer tonight).
        """
        age = s.age_seconds
        if s.state == IDLE and age is not None and age < self.cfg.quiet:
            s.state = BUSY
            s.reason = f"{s.reason}; but active {int(age)}s ago (quiet window {self.cfg.quiet}s)"
        elif s.state == BUSY and age is not None and age > self.cfg.stale_minutes * 60:
            s.state = IDLE
            s.reason = f"stale: {s.reason}, but no activity for {int(age // 60)}m"
        return s
