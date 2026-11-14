import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from good_night import actions, config, doctor, windows  # noqa: E402

POWERCFG_S3_NO_HIB = """The following sleep states are available on this system:
    Standby (S3)

The following sleep states are not available on this system:
    Standby (S1)
        The system firmware does not support this standby state.

    Standby (S2)
        The system firmware does not support this standby state.

    Hibernate
        Hibernation has not been enabled.

    Standby (S0 Low Power Idle)
        The system firmware does not support this standby state.

    Hybrid Sleep
        Hibernation is not available.
"""

POWERCFG_S0_HIB = """The following sleep states are available on this system:
    Standby (S0 Low Power Idle) Network Connected
    Hibernate
    Fast Startup

The following sleep states are not available on this system:
    Standby (S1)
        The system firmware does not support this standby state.
"""

POWERCFG_GERMAN = """Die folgenden Standbymodi sind auf diesem System verfügbar:
    Standby (S3)
    Ruhezustand

Die folgenden Standbymodi sind auf diesem System nicht verfügbar:
    Standby (S1)
        Die Systemfirmware unterstützt diesen Standbymodus nicht.
"""


class PowerCfg(unittest.TestCase):
    def test_parse_s3_without_hibernate(self):
        ps = windows.parse_powercfg_a(POWERCFG_S3_NO_HIB)
        self.assertTrue(ps.parsed)
        self.assertEqual(ps.available, ["Standby (S3)"])
        self.assertTrue(ps.s3)
        self.assertFalse(ps.modern_standby)
        self.assertIn("Hibernation has not been enabled", ps.unavailable["Hibernate"])
        hib, why = windows.hibernation(ps, registry=lambda: None)
        self.assertIs(hib, False)
        self.assertIn("not been enabled", why)

    def test_parse_modern_standby_with_hibernate(self):
        ps = windows.parse_powercfg_a(POWERCFG_S0_HIB)
        self.assertTrue(ps.modern_standby)
        self.assertFalse(ps.s3)
        self.assertIs(windows.hibernation(ps, registry=lambda: None)[0], True)

    def test_localized_output_falls_back_to_registry(self):
        ps = windows.parse_powercfg_a(POWERCFG_GERMAN)
        self.assertTrue(ps.s3)
        self.assertIs(windows.hibernation(ps, registry=lambda: True)[0], True)
        self.assertIs(windows.hibernation(ps, registry=lambda: False)[0], False)
        self.assertIsNone(windows.hibernation(ps, registry=lambda: None)[0])

    def test_unparseable(self):
        ps = windows.parse_powercfg_a("")
        self.assertFalse(ps.parsed)


class WinDoctor(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config(platform="windows", detectors=[])
        self.p = mock.patch.object(windows, "powershell_available", return_value=True)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def run_with(self, text, action="", dry_run=False, registry=None):
        self.cfg.action = action
        with mock.patch.object(windows, "power_states", return_value=windows.parse_powercfg_a(text)), \
             mock.patch.object(windows, "hibernate_registry", return_value=registry), \
             mock.patch("good_night.doctor.Path.exists", return_value=True):
            return {c.name: c for c in doctor.run_checks(self.cfg, dry_run=dry_run)}

    def test_default_hibernate_unavailable_fails_with_fix(self):
        c = self.run_with(POWERCFG_S3_NO_HIB)
        self.assertIn("hibernate", c["action"].detail)
        self.assertEqual(c["hibernation"].status, doctor.FAIL)
        self.assertIn("powercfg /hibernate on", c["hibernation"].fix)
        self.assertIn("GOOD_NIGHT_ACTION=sleep", c["hibernation"].fix)
        self.assertNotIn("sudo shutdown rule", c)

    def test_hibernate_unavailable_only_warns_in_dry_run(self):
        c = self.run_with(POWERCFG_S3_NO_HIB, dry_run=True)
        self.assertEqual(c["hibernation"].status, doctor.WARN)

    def test_hibernate_available_passes(self):
        c = self.run_with(POWERCFG_S0_HIB)
        self.assertEqual(c["hibernation"].status, doctor.PASS)
        self.assertEqual([x for x in c.values() if x.status == doctor.FAIL], [])

    def test_sleep_on_modern_standby_warns(self):
        c = self.run_with(POWERCFG_S0_HIB, action="sleep")
        self.assertEqual(c["sleep"].status, doctor.WARN)
        self.assertIn("sleep vs hibernate", c)

    def test_sleep_on_s3_passes(self):
        c = self.run_with(POWERCFG_S3_NO_HIB, action="sleep")
        self.assertEqual(c["sleep"].status, doctor.PASS)

    def test_shutdown_needs_no_sudo(self):
        c = self.run_with("", action="shutdown")
        self.assertEqual(c["shutdown.exe"].status, doctor.PASS)
        self.assertEqual(c["close apps"].status, doctor.PASS)
        self.assertNotIn("sudo shutdown rule", c)


class Popup(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(windows.parse_popup(0, "1\r\n"), "now")
        self.assertEqual(windows.parse_popup(0, "2"), "cancel")
        self.assertEqual(windows.parse_popup(0, "-1"), "timeout")
        self.assertEqual(windows.parse_popup(1, ""), "error")

    def test_script_quotes(self):
        s = windows.popup_script("it's late", 30)
        self.assertIn("'it''s late', 30", s)
        self.assertIn("WScript.Shell", s)


class CloseWindows(unittest.TestCase):
    def test_script_excludes_keep_and_ancestors(self):
        s = windows.close_windows_script(1234, ["explorer", "O'Neil"], dry_run=False)
        self.assertIn("'explorer','o''neil'", s)
        self.assertIn("$p=1234", s)
        self.assertIn("CloseMainWindow", s)
        self.assertNotIn("CloseMainWindow", windows.close_windows_script(1, [], dry_run=True))

    def test_close_apps_waits_for_listed_pids(self):
        out = "111\tnotepad\tUntitled\n222\tchrome\t\n"
        with mock.patch.object(windows, "powershell_available", return_value=True), \
             mock.patch.object(windows, "_run", return_value=(0, out)), \
             mock.patch.object(windows, "pid_alive", side_effect=lambda p: False) as alive:
            windows.close_apps([], wait=5, dry_run=False)
        self.assertEqual({c[0][0] for c in alive.call_args_list}, {111, 222})


class Win32(unittest.TestCase):
    def test_suspend_really_sleeps(self):
        api = mock.Mock()
        api.SetSuspendState.return_value = 1
        self.assertTrue(windows.suspend(api))
        api.SetSuspendState.assert_called_once_with(False, False, False)  # Hibernate=FALSE

    def test_monitor_off(self):
        api = mock.Mock()
        api.PostMessageW.return_value = 1
        self.assertTrue(windows.monitor_off(api))
        api.PostMessageW.assert_called_once_with(0xFFFF, 0x0112, 0xF170, 2)

    def test_paths(self):
        with mock.patch.dict("os.environ", {"SystemRoot": r"C:\Windows"}):
            self.assertEqual(actions.windows_powershell(),
                             r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")

    def test_windows_config_dirs(self):
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": "/tmp/lad"}):
            log, state = config._local_dirs("windows")
        self.assertEqual(state, Path("/tmp/lad/good-night"))
        self.assertEqual(log.name, "good-night.log")
        self.assertIn("explorer", config.default_keep_apps("windows"))


if __name__ == "__main__":
    unittest.main()
