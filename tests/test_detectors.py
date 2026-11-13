import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from good_night import config  # noqa: E402
from good_night.detectors import BUSY, IDLE, INFO  # noqa: E402
from good_night.detectors import claude_code, cursor  # noqa: E402
from good_night.util import parse_ts  # noqa: E402


def ts(minutes_ago=0.0):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def asst(content, stop, m=0.0):
    return {"type": "assistant", "isSidechain": False, "timestamp": ts(m),
            "message": {"role": "assistant", "stop_reason": stop, "content": content}}


def user(content, m=0.0, **kw):
    r = {"type": "user", "isSidechain": False, "timestamp": ts(m),
         "message": {"role": "user", "content": content}}
    r.update(kw)
    return r


META = [{"type": "last-prompt"}, {"type": "custom-title", "customTitle": "t"}, {"type": "cost-state"}]


class ClaudeClassify(unittest.TestCase):
    def c(self, recs):
        return claude_code.classify(recs)[0]

    def test_end_turn_with_trailing_metadata_is_idle(self):
        recs = [user("hi"), asst([{"type": "text", "text": "done"}], "end_turn"),
                {"type": "system", "subtype": "stop_hook_summary"}] + META
        self.assertEqual(self.c(recs), IDLE)

    def test_pending_tool_is_busy(self):
        recs = [user("hi"), asst([{"type": "thinking"}], "tool_use"),
                asst([{"type": "tool_use", "name": "Bash", "id": "1"}], "tool_use")]
        self.assertEqual(self.c(recs), BUSY)

    def test_tool_result_is_busy(self):
        recs = [asst([{"type": "tool_use", "name": "Bash"}], "tool_use"),
                user([{"type": "tool_result", "tool_use_id": "1"}]), {"type": "api-request"}]
        self.assertEqual(self.c(recs), BUSY)

    def test_ask_user_question_is_idle(self):
        recs = [user("hi"), asst([{"type": "tool_use", "name": "AskUserQuestion"}], "tool_use")]
        self.assertEqual(self.c(recs), IDLE)

    def test_new_prompt_is_busy(self):
        recs = [asst([{"type": "text"}], "end_turn"), user("do more")]
        self.assertEqual(self.c(recs), BUSY)

    def test_interrupted_is_idle(self):
        recs = [asst([{"type": "tool_use", "name": "Bash"}], "tool_use"),
                user([{"type": "text", "text": "[Request interrupted by user]"}])]
        self.assertEqual(self.c(recs), IDLE)

    def test_meta_and_slash_command_records_ignored(self):
        recs = [asst([{"type": "text"}], "end_turn"),
                user("<command-name>/model</command-name>"),
                user("<local-command-stdout>ok</local-command-stdout>"),
                user("caveat", isMeta=True)]
        self.assertEqual(self.c(recs), IDLE)

    def test_sidechain_ignored(self):
        recs = [asst([{"type": "text"}], "end_turn"),
                dict(user("sub"), isSidechain=True)]
        self.assertEqual(self.c(recs), IDLE)

    def test_streaming_is_busy(self):
        recs = [user("hi"), asst([{"type": "thinking"}], None)]
        self.assertEqual(self.c(recs), BUSY)


class DetectorRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = config.Config(claude_dir=root / "claude", cursor_dir=root / "cursor",
                                 quiet=60, stale_minutes=30)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, recs, age_min=0.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        t = time.time() - age_min * 60
        os.utime(path, (t, t))
        return path

    def test_claude_stale_busy_becomes_idle_even_if_mtime_fresh(self):
        # metadata rewrites keep mtime fresh; the record timestamps are old
        self.write(self.cfg.claude_dir / "p" / "s.jsonl",
                   [user("hi", m=120), asst([{"type": "tool_use", "name": "Bash"}], "tool_use", m=119)] + META)
        (s,) = claude_code.ClaudeCodeDetector(self.cfg).sessions()
        self.assertEqual(s.state, IDLE)
        self.assertIn("stale", s.reason)

    def test_claude_quiet_window(self):
        self.write(self.cfg.claude_dir / "p" / "s.jsonl",
                   [user("hi", m=0.2), asst([{"type": "text"}], "end_turn", m=0.1)])
        (s,) = claude_code.ClaudeCodeDetector(self.cfg).sessions()
        self.assertEqual(s.state, BUSY)

    def test_claude_subagent_activity_keeps_busy(self):
        self.write(self.cfg.claude_dir / "p" / "s.jsonl",
                   [user("hi", m=90), asst([{"type": "tool_use", "name": "Task"}], "tool_use", m=89)])
        self.write(self.cfg.claude_dir / "p" / "s" / "subagents" / "agent-1.jsonl", [{"x": 1}], age_min=1)
        (s,) = claude_code.ClaudeCodeDetector(self.cfg).sessions()
        self.assertEqual(s.state, BUSY)

    def test_cursor_turn_ended_idle_and_open_turn_busy(self):
        base = self.cfg.cursor_dir / "Users-x-proj" / "agent-transcripts"
        self.write(base / "a" / "a.jsonl",
                   [{"role": "user", "message": {"content": [{"type": "text"}]}},
                    {"role": "assistant", "message": {"content": [{"type": "text"}]}},
                    {"type": "turn_ended", "status": "success"}], age_min=5)
        self.write(base / "b" / "b.jsonl",
                   [{"role": "user", "message": {"content": [{"type": "text"}]}},
                    {"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Shell"}]}}],
                   age_min=5)
        self.write(base / "c" / "c.jsonl",
                   [{"role": "user", "message": {"content": [{"type": "text"}]}}], age_min=90)
        got = {s.session_id: s.state for s in cursor.CursorDetector(self.cfg).sessions()}
        self.assertEqual(got, {"a": IDLE, "b": BUSY, "c": IDLE})

    def test_cursor_running_terminal_is_info_only(self):
        t = self.cfg.cursor_dir / "Users-x-proj" / "terminals" / "1.txt"
        t.parent.mkdir(parents=True)
        t.write_text(f'---\npid: {os.getpid()}\ncommand: "pnpm dev"\nstatus: running\n---\nlog\n')
        (s,) = cursor.CursorDetector(self.cfg).sessions()
        self.assertEqual(s.state, INFO)


class Util(unittest.TestCase):
    def test_parse_ts(self):
        self.assertIsNotNone(parse_ts("2026-11-13T12:53:55.378Z"))
        self.assertIsNotNone(parse_ts("2026-11-13T12:53:55.3Z"))
        self.assertIsNone(parse_ts(None))


if __name__ == "__main__":
    unittest.main()
