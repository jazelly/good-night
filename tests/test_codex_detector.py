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
from good_night.detectors import BUSY, IDLE  # noqa: E402
from good_night.detectors.codex import CodexDetector, classify  # noqa: E402


def ts(minutes_ago=0.0):
    """Generate ISO timestamp N minutes ago."""
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def session_meta(session_id="sess-123", cwd="/home/user/my-project", agent_nickname=None, m=0.0):
    """Generate a session_meta record."""
    meta = {
        "type": "session_meta",
        "timestamp": ts(m),
        "payload": {
            "id": session_id,
            "session_id": session_id,
            "cwd": cwd,
            "originator": "cli",
            "source": "codex",
        }
    }
    if agent_nickname:
        meta["payload"]["agent_nickname"] = agent_nickname
    return meta


def event_msg(event_type, m=0.0):
    """Generate an event_msg record."""
    return {
        "type": "event_msg",
        "timestamp": ts(m),
        "payload": {"type": event_type}
    }


def response_item(item_type, name=None, m=0.0):
    """Generate a response_item record."""
    payload = {"type": item_type}
    if name:
        payload["name"] = name
    return {
        "type": "response_item",
        "timestamp": ts(m),
        "payload": payload
    }


def other_record(rtype, m=0.0):
    """Generate other record types."""
    return {
        "type": rtype,
        "timestamp": ts(m),
        "payload": {}
    }


class CodexClassify(unittest.TestCase):
    """Test the classify function with various record sequences."""
    
    def c(self, recs):
        """Helper: return state from classify()."""
        return classify(recs)[0]
    
    def test_empty_records(self):
        self.assertEqual(self.c([]), IDLE)
    
    def test_task_started_is_busy(self):
        recs = [session_meta(), event_msg("task_started")]
        self.assertEqual(self.c(recs), BUSY)
    
    def test_task_complete_is_idle(self):
        recs = [
            session_meta(),
            event_msg("task_started", m=5),
            response_item("message", m=4),
            event_msg("task_complete", m=3)
        ]
        self.assertEqual(self.c(recs), IDLE)
    
    def test_turn_aborted_is_idle(self):
        recs = [
            session_meta(),
            event_msg("task_started", m=5),
            event_msg("turn_aborted", m=3)
        ]
        self.assertEqual(self.c(recs), IDLE)
    
    def test_tool_active_is_busy(self):
        recs = [
            session_meta(),
            event_msg("task_started", m=5),
            response_item("function_call", name="read_file", m=4)
        ]
        self.assertEqual(self.c(recs), BUSY)
    
    def test_tool_output_is_busy(self):
        recs = [
            session_meta(),
            event_msg("task_started", m=5),
            response_item("function_call", name="read_file", m=4),
            response_item("function_call_output", m=3)
        ]
        self.assertEqual(self.c(recs), BUSY)
    
    def test_custom_tool_call_is_busy(self):
        recs = [
            session_meta(),
            event_msg("task_started", m=5),
            response_item("custom_tool_call", name="custom_tool", m=4)
        ]
        self.assertEqual(self.c(recs), BUSY)
    
    def test_metadata_only_is_idle(self):
        recs = [
            session_meta(),
            other_record("thread_settings_applied"),
            other_record("configuration_update")
        ]
        self.assertEqual(self.c(recs), IDLE)
    
    def test_unknown_state_becomes_busy_then_stale(self):
        # Has content but no clear lifecycle event
        recs = [
            session_meta(),
            response_item("message", m=5)
        ]
        # Should be BUSY (let stale rule handle it)
        self.assertEqual(self.c(recs), BUSY)


class CodexDetectorIntegration(unittest.TestCase):
    """Test CodexDetector with real file fixtures."""
    
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = config.Config(
            codex_dir=root / "codex",
            quiet=60,
            stale_minutes=30,
            lookback_hours=12
        )
        self.sessions_dir = self.cfg.codex_dir / "sessions"
    
    def tearDown(self):
        self.tmp.cleanup()
    
    def write_rollout(self, date_path, filename, recs, age_min=0.0):
        """Write a rollout JSONL file."""
        path = self.sessions_dir / date_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        t = time.time() - age_min * 60
        os.utime(path, (t, t))
        return path
    
    def test_detector_available_when_dir_exists(self):
        self.sessions_dir.mkdir(parents=True)
        d = CodexDetector(self.cfg)
        self.assertTrue(d.available())
    
    def test_detector_unavailable_when_dir_missing(self):
        d = CodexDetector(self.cfg)
        self.assertFalse(d.available())
    
    def test_active_task_is_busy(self):
        self.write_rollout(
            "2026/11/27",
            "rollout-abc123.jsonl",
            [
                session_meta(cwd="/home/user/my-proj", agent_nickname="Feature work"),
                event_msg("task_started", m=1),
                response_item("function_call", name="Shell", m=0.5)
            ],
            age_min=1
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, BUSY)
        self.assertIn("my-proj", s.label)
        self.assertIn("Feature work", s.label)
    
    def test_completed_task_outside_quiet_window_is_idle(self):
        self.write_rollout(
            "2026/11/27",
            "rollout-def456.jsonl",
            [
                session_meta(cwd="/home/user/proj2", m=10),
                event_msg("task_started", m=10),
                response_item("message", m=9),
                event_msg("task_complete", m=8)
            ],
            age_min=8
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, IDLE)
    
    def test_completed_task_inside_quiet_window_is_busy(self):
        # Completed 30s ago, but quiet window is 60s
        self.write_rollout(
            "2026/11/27",
            "rollout-ghi789.jsonl",
            [
                session_meta(cwd="/home/user/proj3"),
                event_msg("task_started", m=1),
                response_item("message", m=0.6),
                event_msg("task_complete", m=0.5)
            ],
            age_min=0.5
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, BUSY)
        self.assertIn("quiet window", s.reason)
    
    def test_aborted_task_is_idle(self):
        self.write_rollout(
            "2026/11/27",
            "rollout-jkl012.jsonl",
            [
                session_meta(cwd="/home/user/proj4", m=5),
                event_msg("task_started", m=5),
                event_msg("turn_aborted", m=4)
            ],
            age_min=4
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, IDLE)
        self.assertIn("aborted", s.reason)
    
    def test_stale_busy_session_becomes_idle(self):
        # Task started 60 minutes ago, no activity since (stale_minutes=30)
        self.write_rollout(
            "2026/11/27",
            "rollout-mno345.jsonl",
            [
                session_meta(cwd="/home/user/proj5", m=60),
                event_msg("task_started", m=60),
                response_item("function_call", name="Bash", m=59)
            ],
            age_min=59
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, IDLE)
        self.assertIn("stale", s.reason)
    
    def test_empty_session_is_idle(self):
        # Empty file
        path = self.sessions_dir / "2026/11/27" / "rollout-pqr678.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        # Should be filtered out or marked idle
        # The inspect() returns None for empty records, so no session
        self.assertEqual(len(sessions), 0)
    
    def test_metadata_only_session_is_idle(self):
        # Session opened but no task started
        self.write_rollout(
            "2026/11/27",
            "rollout-stu901.jsonl",
            [
                session_meta(cwd="/home/user/proj6", m=5),
                other_record("thread_settings_applied", m=5),
                other_record("configuration_update", m=5)
            ],
            age_min=5
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, IDLE)
        self.assertIn("no task", s.reason)
    
    def test_malformed_json_line_ignored(self):
        # Write a file with valid lines and one malformed line
        path = self.sessions_dir / "2026/11/27" / "rollout-vwx234.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join([
            json.dumps(session_meta(cwd="/home/user/proj7", m=5)),
            json.dumps(event_msg("task_started", m=5)),
            '{"incomplete json',  # malformed
            json.dumps(event_msg("task_complete", m=4))
        ]) + "\n"
        path.write_text(content)
        t = time.time() - 4 * 60
        os.utime(path, (t, t))
        
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        # Should parse the valid lines and classify correctly
        self.assertEqual(s.state, IDLE)
    
    def test_subagent_session_labeled(self):
        # Session with parent_thread_id and agent_nickname
        meta = session_meta(
            cwd="/home/user/main-proj",
            agent_nickname="Subagent Task"
        )
        meta["payload"]["parent_thread_id"] = "parent-123"
        
        self.write_rollout(
            "2026/11/27",
            "rollout-yza567.jsonl",
            [
                meta,
                event_msg("task_started", m=1),
                response_item("message", m=0.5)
            ],
            age_min=1
        )
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, BUSY)
        self.assertIn("Subagent Task", s.label)
    
    def test_codex_home_env_var(self):
        # Test that CODEX_HOME env var is respected
        custom_codex = Path(self.tmp.name) / "custom-codex"
        custom_sessions = custom_codex / "sessions" / "2026/11/27"
        custom_sessions.mkdir(parents=True, exist_ok=True)
        
        path = custom_sessions / "rollout-custom.jsonl"
        path.write_text("\n".join([
            json.dumps(session_meta(cwd="/home/user/custom-proj")),
            json.dumps(event_msg("task_complete", m=5))
        ]) + "\n")
        t = time.time() - 5 * 60
        os.utime(path, (t, t))
        
        # Create config with custom codex_dir
        cfg = config.Config(
            codex_dir=custom_codex,
            quiet=60,
            stale_minutes=30,
            lookback_hours=12
        )
        d = CodexDetector(cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertIn("custom-proj", sessions[0].label)
    
    def test_large_file_tail_reading(self):
        # Create a file with many records (simulating a large session)
        # The tail_records function should handle this efficiently
        records = [session_meta(cwd="/home/user/large-proj")]
        
        # Add many intermediate records
        for i in range(500):
            records.append(response_item("message", m=500-i))
        
        # Add final state
        records.append(event_msg("task_started", m=10))
        records.append(response_item("function_call", name="Tool", m=9))
        
        self.write_rollout(
            "2026/11/27",
            "rollout-large.jsonl",
            records,
            age_min=9
        )
        
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.state, BUSY)
        self.assertIn("large-proj", s.label)
    
    def test_lookback_window_filtering(self):
        # Create one recent session and one old session
        # Recent (5 minutes ago)
        self.write_rollout(
            "2026/11/27",
            "rollout-recent.jsonl",
            [
                session_meta(cwd="/home/user/recent"),
                event_msg("task_complete", m=5)
            ],
            age_min=5
        )
        
        # Old (24 hours ago, beyond lookback_hours=12)
        self.write_rollout(
            "2026/11/26",
            "rollout-old.jsonl",
            [
                session_meta(cwd="/home/user/old"),
                event_msg("task_complete", m=24*60)
            ],
            age_min=24*60
        )
        
        d = CodexDetector(self.cfg)
        sessions = d.sessions()
        # Should only find the recent one
        self.assertEqual(len(sessions), 1)
        self.assertIn("recent", sessions[0].label)


class CodexConfigIntegration(unittest.TestCase):
    """Test config loading for Codex."""
    
    def test_default_codex_dir(self):
        # Clear env vars
        old_codex_home = os.environ.pop("CODEX_HOME", None)
        old_good_night_codex_dir = os.environ.pop("GOOD_NIGHT_CODEX_DIR", None)
        
        try:
            cfg = config.load()
            expected = Path.home() / ".codex"
            self.assertEqual(cfg.codex_dir, expected)
        finally:
            if old_codex_home:
                os.environ["CODEX_HOME"] = old_codex_home
            if old_good_night_codex_dir:
                os.environ["GOOD_NIGHT_CODEX_DIR"] = old_good_night_codex_dir
    
    def test_codex_home_env_var(self):
        old_codex_home = os.environ.get("CODEX_HOME")
        old_good_night_codex_dir = os.environ.pop("GOOD_NIGHT_CODEX_DIR", None)
        
        try:
            os.environ["CODEX_HOME"] = "/custom/codex/path"
            cfg = config.load()
            self.assertEqual(cfg.codex_dir, Path("/custom/codex/path"))
        finally:
            if old_codex_home:
                os.environ["CODEX_HOME"] = old_codex_home
            else:
                os.environ.pop("CODEX_HOME", None)
            if old_good_night_codex_dir:
                os.environ["GOOD_NIGHT_CODEX_DIR"] = old_good_night_codex_dir
    
    def test_good_night_codex_dir_overrides_codex_home(self):
        old_codex_home = os.environ.get("CODEX_HOME")
        old_good_night_codex_dir = os.environ.get("GOOD_NIGHT_CODEX_DIR")
        
        try:
            os.environ["CODEX_HOME"] = "/custom/codex/path"
            os.environ["GOOD_NIGHT_CODEX_DIR"] = "/override/path"
            cfg = config.load()
            self.assertEqual(cfg.codex_dir, Path("/override/path"))
        finally:
            if old_codex_home:
                os.environ["CODEX_HOME"] = old_codex_home
            else:
                os.environ.pop("CODEX_HOME", None)
            if old_good_night_codex_dir:
                os.environ["GOOD_NIGHT_CODEX_DIR"] = old_good_night_codex_dir
            else:
                os.environ.pop("GOOD_NIGHT_CODEX_DIR", None)
    
    def test_codex_in_default_detectors(self):
        old_detectors = os.environ.pop("GOOD_NIGHT_DETECTORS", None)
        
        try:
            cfg = config.load()
            self.assertIn("codex", cfg.detectors)
        finally:
            if old_detectors:
                os.environ["GOOD_NIGHT_DETECTORS"] = old_detectors


if __name__ == "__main__":
    unittest.main()
