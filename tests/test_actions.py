import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from good_night import actions, cli, config, night, shutdown  # noqa: E402
from good_night.actions import ActionError  # noqa: E402


class Resolve(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(actions.resolve("", "macos"), "sleep")
        self.assertEqual(actions.resolve(None, "windows"), "hibernate")
        self.assertEqual(actions.resolve("  ", "linux"), "sleep")

    def test_aliases_and_case(self):
        self.assertEqual(actions.resolve("Shutdown", "macos"), "shutdown")
        self.assertEqual(actions.resolve("poweroff", "windows"), "shutdown")
        self.assertEqual(actions.resolve("suspend", "windows"), "sleep")
        self.assertEqual(actions.resolve("screen-off", "macos"), "display-off")
        self.assertEqual(actions.resolve("display off", "windows"), "display-off")

    def test_unknown(self):
        with self.assertRaises(ActionError) as e:
            actions.resolve("reboot", "macos")
        self.assertIn("unknown action", str(e.exception))

    def test_hibernate_rejected_on_macos(self):
        with self.assertRaises(ActionError) as e:
            actions.resolve("hibernate", "macos")
        self.assertIn("not supported on macos", str(e.exception))
        self.assertIn("GOOD_NIGHT_ACTION=sleep", str(e.exception))

    def test_display_off_rejected_on_linux(self):
        with self.assertRaises(ActionError):
            actions.resolve("display-off", "linux")

    def test_needs(self):
        self.assertTrue(actions.needs_sudo("shutdown", "macos"))
        for a in ("sleep", "display-off"):
            self.assertFalse(actions.needs_sudo(a, "macos"))
        for a in actions.ACTIONS:
            self.assertFalse(actions.needs_sudo(a, "windows"))
            self.assertEqual(actions.closes_apps(a), a == "shutdown")


class Plan(unittest.TestCase):
    def test_macos(self):
        self.assertEqual(actions.plan("sleep", "macos").argv, ["/usr/bin/pmset", "sleepnow"])
        self.assertEqual(actions.plan("display-off", "macos").argv, ["/usr/bin/pmset", "displaysleepnow"])
        self.assertEqual(actions.plan("shutdown", "macos").argv,
                         ["/usr/bin/sudo", "-n", "/sbin/shutdown", "-h", "now"])
        with self.assertRaises(ActionError):
            actions.plan("hibernate", "macos")

    def test_windows(self):
        with mock.patch.dict("os.environ", {"SystemRoot": r"D:\Win"}):
            self.assertEqual(actions.plan("shutdown", "windows").argv,
                             [r"D:\Win\System32\shutdown.exe", "/s", "/t", "0", "/f"])
            self.assertEqual(actions.plan("hibernate", "windows").argv, [r"D:\Win\System32\shutdown.exe", "/h"])
        sleep = actions.plan("sleep", "windows")
        self.assertEqual((sleep.kind, sleep.call), ("win32", "suspend"))
        self.assertNotIn("rundll32", sleep.display)
        self.assertEqual(actions.plan("display-off", "windows").call, "monitor_off")

    def test_linux(self):
        self.assertEqual(actions.plan("shutdown", "linux").argv, ["systemctl", "poweroff"])
        self.assertEqual(actions.plan("sleep", "linux").argv, ["systemctl", "suspend"])
        self.assertEqual(actions.plan("hibernate", "linux").argv, ["systemctl", "hibernate"])

    def test_matrix_covers_everything(self):
        m = actions.matrix()
        self.assertEqual(m["hibernate"]["macos"], "unsupported")
        self.assertIn("shutdown.exe /h", m["hibernate"]["windows"])


class Env(unittest.TestCase):
    def test_env_and_flag(self):
        with mock.patch.dict("os.environ", {"GOOD_NIGHT_ACTION": "display-off"}):
            self.assertEqual(config.load().action, "display-off")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("GOOD_NIGHT_ACTION", None)
            self.assertEqual(config.load().action, "")

    def test_watch_rejects_bad_action_before_doing_anything(self):
        with mock.patch.dict("os.environ", {"GOOD_NIGHT_ACTION": "hibernate", "GOOD_NIGHT_LOG": "/dev/null"}), \
             mock.patch("good_night.system.current", return_value="macos"), \
             mock.patch("good_night.config.system.current", return_value="macos"), \
             mock.patch.object(night.KeepAwake, "start", side_effect=AssertionError("must not start")):
            self.assertEqual(cli.main(["watch", "--once", "--dry-run"]), 2)


class Perform(unittest.TestCase):
    def cfg(self, platform, **kw):
        tmp = tempfile.mkdtemp()
        return config.Config(platform=platform, state_dir=Path(tmp), notify=False, dialog=False, **kw)

    def test_dry_run_never_runs_anything(self):
        for plat, acts in (("macos", ("sleep", "display-off", "shutdown")),
                           ("windows", actions.ACTIONS), ("linux", ("sleep", "hibernate", "shutdown"))):
            for a in acts:
                cfg = self.cfg(plat, dry_run=True, quit_apps=False)
                with mock.patch("subprocess.run", side_effect=AssertionError("ran")), \
                     mock.patch("subprocess.Popen", side_effect=AssertionError("spawned")), \
                     mock.patch.dict("good_night.windows.WIN32_CALLS",
                                     {"suspend": mock.Mock(side_effect=AssertionError),
                                      "monitor_off": mock.Mock(side_effect=AssertionError)}):
                    self.assertTrue(night.perform(cfg, a), (plat, a))

    def test_macos_sleep_runs_pmset_without_sudo_or_quitting(self):
        cfg = self.cfg("macos")
        keep = mock.Mock()
        with mock.patch("good_night.shutdown.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run, \
             mock.patch.object(shutdown, "sudo_ok", side_effect=AssertionError("no sudo")), \
             mock.patch.object(shutdown, "quit_apps", side_effect=AssertionError("no quitting")):
            self.assertTrue(night.perform(cfg, "sleep", keep))
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0], ["/usr/bin/pmset", "sleepnow"])
        keep.stop.assert_called_once()  # caffeinate is stopped before sleeping

    def test_macos_shutdown_checks_sudo_and_quits_apps(self):
        cfg = self.cfg("macos")
        with mock.patch.object(shutdown, "sudo_ok", return_value=(False, "password required")), \
             mock.patch("good_night.shutdown.subprocess.run", side_effect=AssertionError("must not run")):
            self.assertFalse(night.perform(cfg, "shutdown"))
        with mock.patch.object(shutdown, "sudo_ok", return_value=(True, "")), \
             mock.patch.object(shutdown, "quit_apps") as quit_apps, \
             mock.patch("good_night.shutdown.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            self.assertTrue(night.perform(cfg, "shutdown"))
        quit_apps.assert_called_once()
        self.assertEqual(run.call_args[0][0], actions.MAC_SHUTDOWN)

    def test_windows_hibernate_and_shutdown_commands(self):
        cfg = self.cfg("windows")
        with mock.patch("good_night.night.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run, \
             mock.patch("good_night.windows.close_apps") as close:
            self.assertTrue(night.perform(cfg, "hibernate"))
            self.assertEqual(run.call_args[0][0][1:], ["/h"])
            close.assert_not_called()
            self.assertTrue(night.perform(cfg, "shutdown"))
            self.assertEqual(run.call_args[0][0][1:], ["/s", "/t", "0", "/f"])
            close.assert_called_once()

    def test_windows_failure_is_reported(self):
        cfg = self.cfg("windows")
        with mock.patch("good_night.night.subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="", stderr="Hibernation is not enabled")):
            self.assertFalse(night.perform(cfg, "hibernate"))

    def test_windows_sleep_and_display_use_win32(self):
        cfg = self.cfg("windows")
        calls = {"suspend": mock.Mock(return_value=True), "monitor_off": mock.Mock(return_value=True)}
        with mock.patch.dict("good_night.windows.WIN32_CALLS", calls), \
             mock.patch("subprocess.run", side_effect=AssertionError("no subprocess")):
            self.assertTrue(night.perform(cfg, "sleep"))
            self.assertTrue(night.perform(cfg, "display-off"))
        calls["suspend"].assert_called_once()
        calls["monitor_off"].assert_called_once()
        calls["suspend"].return_value = False
        with mock.patch.dict("good_night.windows.WIN32_CALLS", calls):
            self.assertFalse(night.perform(cfg, "sleep"))


class Dialog(unittest.TestCase):
    def test_parse_macos_dialog(self):
        self.assertEqual(shutdown.parse_dialog(0, "button returned:Sleep now, gave up:false", "", "sleep"), "now")
        self.assertEqual(shutdown.parse_dialog(0, "button returned:, gave up:true", "", "sleep"), "timeout")
        self.assertEqual(shutdown.parse_dialog(1, "", "execution error: User canceled. (-128)", "sleep"), "cancel")
        self.assertEqual(shutdown.parse_dialog(1, "", "boom", "shutdown"), "error")

    def test_grace_cancel_file(self):
        tmp = tempfile.mkdtemp()
        cfg = config.Config(platform="windows", state_dir=Path(tmp), grace=3, dialog=False, notify=False)
        real_sleep = night.time.sleep

        def touch(_):
            cfg.cancel_file.touch()
        with mock.patch.object(night.time, "sleep", side_effect=touch):
            self.assertFalse(night.grace_period(cfg, "hibernate"))
        self.assertIs(night.time.sleep, real_sleep)

    def test_grace_zero_proceeds(self):
        cfg = config.Config(platform="macos", grace=0)
        self.assertTrue(night.grace_period(cfg, "sleep"))


class KeepAwake(unittest.TestCase):
    def test_macos_caffeinate_started_and_stopped(self):
        cfg = config.Config(platform="macos")
        proc = mock.Mock()
        proc.poll.return_value = None
        with mock.patch("good_night.night.subprocess.Popen", return_value=proc) as popen:
            k = night.KeepAwake(cfg)
            k.start()
            k.stop()
        self.assertEqual(popen.call_args[0][0][0], "/usr/bin/caffeinate")
        proc.terminate.assert_called_once()

    def test_windows_execution_state(self):
        cfg = config.Config(platform="windows")
        with mock.patch("good_night.windows.keep_awake", return_value=True) as ka:
            k = night.KeepAwake(cfg)
            k.start()
            k.stop()
        self.assertEqual([c[0][0] for c in ka.call_args_list], [True, False])


if __name__ == "__main__":
    unittest.main()
