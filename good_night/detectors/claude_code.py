"""Claude Code detector.

Every Claude Code session -- the `claude` CLI, `claude remote-control`, and
the code sessions of the Claude desktop app -- appends its transcript in real
time to ~/.claude/projects/<project>/<session-id>.jsonl.

File mtime alone is NOT a reliable signal: Claude rewrites metadata records
(custom-title, last-prompt, cost-state, ...) into idle sessions. So we look at
the *last conversation record* (type user/assistant on the main chain) and at
record timestamps:

  assistant, stop_reason end_turn/stop_sequence/... -> idle (turn finished)
  assistant with tool_use, tool is AskUserQuestion   -> idle (waiting for you)
  assistant with other tool_use                      -> busy (tool running)
  assistant without stop_reason                      -> busy (still streaming)
  user tool_result                                   -> busy (model working)
  user prompt                                        -> busy (model working)
  user "[Request interrupted by user]"               -> idle

A long-lived `claude rc` shell that is just sitting there writes nothing, so it
never counts as busy.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..util import mtime, parse_ts, recent_files, tail_records
from .base import BUSY, IDLE, Detector, Session

# Tools that mean "the agent is asking the human something".
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
FINISHED_STOPS = {"end_turn", "stop_sequence", "max_tokens", "refusal", "pause_turn"}
# User records that are bookkeeping, not a new turn for the model.
NON_TURN_PREFIXES = (
    "<local-command",
    "<command-name>",
    "<command-message>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
)


def _content(rec: dict):
    msg = rec.get("message") or {}
    return msg.get("content")


def _blocks(rec: dict) -> list:
    c = _content(rec)
    if isinstance(c, list):
        return [b for b in c if isinstance(b, dict)]
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return []


def _user_text(rec: dict) -> str:
    return "".join(b.get("text", "") for b in _blocks(rec) if b.get("type") == "text").strip()


def _is_turn_record(rec: dict) -> bool:
    t = rec.get("type")
    if t not in ("user", "assistant"):
        return False
    if rec.get("isSidechain"):
        return False
    if t == "user":
        if rec.get("isMeta") or rec.get("isCompactSummary"):
            return False
        blocks = _blocks(rec)
        if not any(b.get("type") == "tool_result" for b in blocks):
            text = _user_text(rec)
            if not text and not blocks:
                return False
            if text.startswith(NON_TURN_PREFIXES):
                return False
    return True


def classify(records: List[dict]) -> "tuple[str, str]":
    """Return (state, reason) from a session's trailing records."""
    turns = [r for r in records if _is_turn_record(r)]
    if not turns:
        return IDLE, "no conversation yet"
    last = turns[-1]
    if last.get("type") == "assistant":
        # Collect tool_use blocks from the trailing run of assistant records
        # (Claude writes one record per content block).
        pending = []
        for r in reversed(turns):
            if r.get("type") != "assistant":
                break
            pending += [b.get("name", "?") for b in _blocks(r) if b.get("type") == "tool_use"]
        stop = (last.get("message") or {}).get("stop_reason")
        if pending:
            if all(n in WAITING_TOOLS for n in pending):
                return IDLE, f"waiting for your answer ({', '.join(sorted(set(pending)))})"
            return BUSY, f"running tool {', '.join(dict.fromkeys(pending))}"
        if stop in FINISHED_STOPS:
            return IDLE, f"turn finished ({stop})"
        return BUSY, "assistant is generating"
    # user record
    blocks = _blocks(last)
    if any(b.get("type") == "tool_result" for b in blocks):
        return BUSY, "tool finished, model is working"
    text = _user_text(last)
    if text.startswith("[Request interrupted"):
        return IDLE, "interrupted by user"
    return BUSY, "prompt sent, model is working"


class ClaudeCodeDetector(Detector):
    name = "claude"
    title = "Claude Code"

    def available(self) -> bool:
        return self.cfg.claude_dir.is_dir()

    def sessions(self) -> List[Session]:
        out = []
        for path in recent_files(self.cfg.claude_dir, "*/*.jsonl", self.since()):
            s = self.inspect(path)
            if s is not None:
                out.append(self.finalize(s))
        return out

    def inspect(self, path: Path) -> Optional[Session]:
        records = tail_records(path, want=400)
        if not records:
            return None
        state, reason = classify(records)

        last: Optional[datetime] = None
        title = None
        cwd = None
        for r in records:
            ts = parse_ts(r.get("timestamp"))
            if ts and (last is None or ts > last):
                last = ts
            if r.get("type") == "custom-title" and r.get("customTitle"):
                title = r["customTitle"]
            elif r.get("type") == "agent-name" and r.get("agentName") and not title:
                title = r["agentName"]
            if r.get("cwd"):
                cwd = r["cwd"]
        # Sub-agents (Task tool) write to <session>/subagents/*.jsonl while the
        # main file is quiet; count that as activity of this session.
        sub = path.with_suffix("") / "subagents"
        if sub.is_dir():
            for p in sub.glob("*.jsonl"):
                m = mtime(p)
                if m and (last is None or m > last):
                    last = m
        if last is None:
            last = mtime(path)

        project = Path(cwd).name if cwd else path.parent.name
        label = project + (f" / {title}" if title else "")
        return Session(
            detector=self.name,
            session_id=path.stem,
            label=label,
            state=state,
            reason=reason,
            last_activity=last,
            path=path,
        )
