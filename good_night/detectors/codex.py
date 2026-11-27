"""Codex detector (Codex CLI and Codex Desktop App).

Both Codex CLI and the Codex Desktop app write rollout transcripts as JSONL
files under $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl (default
~/.codex/sessions/).

Each line is a JSON object with `type`, optional `payload` dict, and
`timestamp` (ISO string). Session lifecycle:

  type == "session_meta": session metadata with keys like `id`, `session_id`,
                          `cwd`, `originator`, `source`, `agent_nickname`, etc.
  type == "event_msg":
    payload.type == "task_started"  -> a turn has started (BUSY)
    payload.type == "task_complete" -> turn finished cleanly (IDLE, subject to quiet window)
    payload.type == "turn_aborted"  -> turn aborted/interrupted (IDLE, subject to quiet window)
  type == "response_item":
    function_call, custom_tool_call         -> tool invoked (BUSY)
    function_call_output, custom_tool_call_output -> tool finished, model working (BUSY)
    reasoning, message, agent_message, additional_tools, configuration_update

A session in progress has task_started and subsequent response_item records
without a following task_complete or turn_aborted. A completed session ends
with task_complete or turn_aborted.

Large JSONL files are read efficiently (head for session_meta + tail for
latest state). Partial/truncated last lines (while Codex is writing) are
ignored. Quiet window and stale timeout are applied via finalize().
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..util import mtime, parse_ts, recent_files, tail_records
from .base import BUSY, IDLE, Detector, Session

# Event types that signal the end of a turn (idle, subject to quiet window)
TURN_END_EVENTS = {"task_complete", "turn_aborted"}

# Response item types that indicate tool activity (busy)
TOOL_ACTIVE_TYPES = {"function_call", "custom_tool_call"}

# Response item types that indicate tool completion (model working, still busy)
TOOL_OUTPUT_TYPES = {"function_call_output", "custom_tool_call_output"}


def _extract_session_meta(records: List[dict]) -> "dict[str, any]":
    """Extract session metadata from records (read from head)."""
    for r in records:
        if r.get("type") == "session_meta" and isinstance(r.get("payload"), dict):
            return r["payload"]
    return {}


def _extract_label(meta: dict) -> str:
    """Build a human-readable label from session metadata."""
    cwd = meta.get("cwd")
    nickname = meta.get("agent_nickname")
    
    if nickname and cwd:
        # Short path like Claude Code does
        project = Path(cwd).name if cwd else "?"
        return f"{project} / {nickname}"
    elif nickname:
        return nickname
    elif cwd:
        return Path(cwd).name
    
    # Fallback to session_id or source
    sid = meta.get("session_id") or meta.get("id") or "?"
    if len(sid) > 12:
        sid = sid[:12] + "..."
    return sid


def classify(records: List[dict]) -> "tuple[str, str]":
    """Return (state, reason) from a session's records.
    
    Expects records to be in chronological order (oldest first from head,
    then newest from tail).
    """
    if not records:
        return IDLE, "no session data"
    
    # Find the last event_msg and track tool activity
    last_event_type = None
    last_event_time = None
    tool_activity = []
    
    for r in records:
        rtype = r.get("type")
        payload = r.get("payload")
        
        if rtype == "event_msg" and isinstance(payload, dict):
            event_type = payload.get("type")
            if event_type in ("task_started", "task_complete", "turn_aborted"):
                last_event_type = event_type
                ts = parse_ts(r.get("timestamp"))
                if ts:
                    last_event_time = ts
        
        elif rtype == "response_item" and isinstance(payload, dict):
            item_type = payload.get("type")
            if item_type in TOOL_ACTIVE_TYPES:
                tool_name = payload.get("name") or payload.get("function") or "tool"
                tool_activity.append(("active", tool_name))
            elif item_type in TOOL_OUTPUT_TYPES:
                tool_activity.append(("output", "tool_output"))
    
    # Determine state based on the last event and tool activity
    if last_event_type == "task_started":
        # A turn is in progress
        if tool_activity:
            last_kind, last_tool = tool_activity[-1]
            if last_kind == "active":
                return BUSY, f"running tool {last_tool}"
            else:  # output
                return BUSY, "tool finished, model is working"
        return BUSY, "task in progress"
    
    elif last_event_type in TURN_END_EVENTS:
        # Turn ended cleanly or was aborted
        return IDLE, f"turn {last_event_type.replace('task_', '').replace('_', ' ')}"
    
    # No clear task lifecycle events found
    # Check if we have only metadata/config records (session opened but no task)
    has_content = any(
        r.get("type") in ("event_msg", "response_item")
        for r in records
    )
    if not has_content:
        return IDLE, "no task started"
    
    # Ambiguous: has some content but no clear lifecycle event
    # Let the stale rule handle this
    return BUSY, "session in unknown state"


class CodexDetector(Detector):
    name = "codex"
    title = "Codex"

    def available(self) -> bool:
        return self.cfg.codex_dir.is_dir()

    def sessions(self) -> List[Session]:
        out = []
        # Pattern: sessions/YYYY/MM/DD/rollout-*.jsonl
        for path in recent_files(self.cfg.codex_dir, "sessions/*/*/*/*rollout-*.jsonl", self.since()):
            s = self.inspect(path)
            if s is not None:
                out.append(self.finalize(s))
        return out

    def inspect(self, path: Path) -> Optional[Session]:
        """Inspect a rollout JSONL file.
        
        Strategy: read head records for session_meta, then tail records for
        latest state. This avoids loading large files entirely into memory.
        """
        # Read first ~100 records for session_meta
        head_records = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for _ in range(100):
                    line = fh.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            head_records.append(rec)
                            if rec.get("type") == "session_meta":
                                # Found it, can stop
                                break
                    except (ValueError, json.JSONDecodeError):
                        continue
        except OSError:
            return None
        
        # Read tail records for latest state
        tail_recs = tail_records(path, want=50)
        
        # Combine: head records + tail records (may overlap, but that's fine)
        records = head_records + tail_recs
        
        if not records:
            return None
        
        # Extract metadata
        meta = _extract_session_meta(records)
        label = _extract_label(meta)
        
        # Classify state
        state, reason = classify(records)
        
        # Activity time: latest timestamp from records, or mtime
        last: Optional[datetime] = None
        for r in records:
            ts = parse_ts(r.get("timestamp"))
            if ts and (last is None or ts > last):
                last = ts
        
        if last is None:
            last = mtime(path)
        
        # Session ID
        session_id = meta.get("session_id") or meta.get("id") or path.stem
        
        return Session(
            detector=self.name,
            session_id=session_id,
            label=label,
            state=state,
            reason=reason,
            last_activity=last,
            path=path,
        )
