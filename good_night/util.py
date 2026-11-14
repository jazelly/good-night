from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp like 2026-11-13T12:53:55.378Z."""
    if not isinstance(value, str) or not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        # Python 3.9 only accepts 0/3/6 fractional digits; normalise.
        try:
            head, _, tail = v.partition(".")
            frac, sign, tz = "", "", ""
            for i, ch in enumerate(tail):
                if ch in "+-":
                    frac, sign, tz = tail[:i], ch, tail[i + 1 :]
                    break
            else:
                frac = tail
            frac = (frac + "000000")[:6]
            dt = datetime.fromisoformat(f"{head}.{frac}{sign}{tz}")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def tail_records(path: Path, want: int = 400, max_bytes: int = 32 * 1024 * 1024) -> List[dict]:
    """Return up to `want` parsed JSON records from the end of a JSONL file.

    Session files can be tens of MB with single lines of several MB (tool
    output, images), so we read backwards in growing chunks instead of loading
    the whole file.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    chunk = 256 * 1024
    with open(path, "rb") as fh:
        while True:
            start = max(0, size - chunk)
            fh.seek(start)
            data = fh.read(size - start)
            lines = data.split(b"\n")
            if start > 0:
                lines = lines[1:]  # first line is probably partial
            records = []
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
            if len(records) >= want or start == 0 or chunk >= max_bytes:
                return records[-want:]
            chunk *= 4


def recent_files(root: Path, pattern: str, since: datetime) -> Iterator[Path]:
    """Yield files under root matching glob pattern with mtime >= since."""
    if not root.is_dir():
        return
    cutoff = since.timestamp()
    for p in root.glob(pattern):
        try:
            if p.is_file() and p.stat().st_mtime >= cutoff:
                yield p
        except OSError:
            continue


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) would send CTRL_C_EVENT on Windows.
        from .windows import pid_alive as win_pid_alive
        try:
            return win_pid_alive(pid)
        except (AttributeError, OSError):
            return True  # can't tell: assume alive (terminals are info-only anyway)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def human_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 90 * 60:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"
