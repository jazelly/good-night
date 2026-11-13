"""Cursor detector (Cursor IDE agent and the `cursor-agent` CLI).

Cursor writes each agent conversation to
~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl. Records carry
no timestamps, but every completed turn ends with
{"type":"turn_ended","status":"success"|"error"|...}. So:

  last record is turn_ended            -> idle
  anything else (user msg, tool calls) -> busy, until it goes stale

Activity time is the transcript mtime, or a sub-agent transcript's mtime if
newer. Agent terminals (~/.cursor/projects/*/terminals/*.txt) are reported in
`status` for information only: background dev servers (Metro, vite, ...) keep
"running" forever and must not block shutdown.

The cursor-agent-worker logs under ~/Library/Application Support/Cursor are
deliberately ignored: they are written every 30s as heartbeats
("Received frame ctx=worker-mode") whether or not anything is running.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from ..util import mtime, parse_ts, pid_alive, recent_files, tail_records
from .base import BUSY, IDLE, INFO, Detector, Session

_HEADER_RE = re.compile(r"^(\w+):\s*(.*?)\s*$")


def classify(records: List[dict]) -> "tuple[str, str]":
    if not records:
        return IDLE, "empty transcript"
    last = records[-1]
    if last.get("type") == "turn_ended":
        status = last.get("status") or "?"
        return IDLE, f"turn ended ({status})"
    role = last.get("role")
    content = (last.get("message") or {}).get("content") or []
    kinds = [b.get("type") for b in content if isinstance(b, dict)]
    if role == "assistant" and "tool_use" in kinds:
        names = [b.get("name", "?") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        return BUSY, f"running tool {', '.join(dict.fromkeys(names))}"
    if role == "assistant":
        return BUSY, "assistant is responding (turn not ended)"
    if role == "user":
        return BUSY, "prompt sent, agent is working"
    return BUSY, "turn in progress"


def parse_terminal_header(path: Path) -> dict:
    hdr = {}
    try:
        with open(path, "r", errors="replace") as fh:
            first = fh.readline().strip()
            if first != "---":
                return hdr
            for _ in range(40):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                m = _HEADER_RE.match(line.rstrip("\n"))
                if m:
                    k, v = m.group(1), m.group(2)
                    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                        v = v[1:-1]
                    hdr[k] = v
    except OSError:
        pass
    return hdr


class CursorDetector(Detector):
    name = "cursor"
    title = "Cursor"

    def available(self) -> bool:
        return self.cfg.cursor_dir.is_dir()

    def sessions(self) -> List[Session]:
        out = []
        since = self.since()
        for path in recent_files(self.cfg.cursor_dir, "*/agent-transcripts/*/*.jsonl", since):
            s = self.inspect(path)
            if s is not None:
                out.append(self.finalize(s))
        out += self.terminals(since)
        return out

    def _slug(self, path: Path) -> str:
        try:
            slug = path.relative_to(self.cfg.cursor_dir).parts[0]
        except ValueError:
            slug = path.parent.name
        # "Users-alice-Desktop-code-foo" -> "Desktop-code-foo"
        home = "-".join(Path.home().parts[1:])
        if home and slug.startswith(home + "-"):
            slug = slug[len(home) + 1 :]
        return slug

    def inspect(self, path: Path) -> Optional[Session]:
        records = tail_records(path, want=20)
        state, reason = classify(records)
        last = mtime(path)
        sub = path.parent / "subagents"
        if sub.is_dir():
            for p in sub.glob("*.jsonl"):
                m = mtime(p)
                if m and (last is None or m > last):
                    last = m
        return Session(
            detector=self.name,
            session_id=path.stem,
            label=self._slug(path),
            state=state,
            reason=reason,
            last_activity=last,
            path=path,
        )

    def terminals(self, since) -> List[Session]:
        out = []
        for path in recent_files(self.cfg.cursor_dir, "*/terminals/*.txt", since):
            hdr = parse_terminal_header(path)
            if hdr.get("status") != "running":
                continue
            try:
                pid = int(hdr.get("pid", "0"))
            except ValueError:
                pid = 0
            if not pid or not pid_alive(pid):
                continue
            cmd = hdr.get("command", "?")
            out.append(
                Session(
                    detector=self.name,
                    session_id=f"terminal {path.stem} pid {pid}",
                    label=self._slug(path),
                    state=INFO,
                    reason=f"background terminal still running: {cmd[:60]} (not counted)",
                    last_activity=parse_ts(hdr.get("started_at")) or mtime(path),
                    path=path,
                )
            )
        return out
