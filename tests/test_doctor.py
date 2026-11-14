import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from good_night import config, doctor, macos  # noqa: E402

LSAPPINFO = '''
 1) "loginwindow" ASN:0x0-0x3003:
    bundleID="com.apple.loginwindow"
    pid = 171 type="UIElement" flavor=3 Version="3085.6.3"
 2) "universalaccessd" ASN:0x0-0x8008:
    bundleID=[ NULL ]
    pid = 454 !signalled type="BackgroundOnly" flavor=3
 3) "Cursor" ASN:0x0-0x5cb9cb4:
    bundleID="com.todesktop.230313mzl4w4u92"
    pid = 98972 type="Foreground" flavor=3 Version="3.1"
 4) "Microsoft Word" ASN:0x0-0x4407403:
    bundleID="com.microsoft.Word"
    pid = 6753 type="Foreground" flavor=3
'''


class LsAppInfo(unittest.TestCase):
    def test_parse(self):
        apps = macos.parse_lsappinfo(LSAPPINFO)
        self.assertEqual([a.name for a in apps], ["loginwindow", "universalaccessd", "Cursor", "Microsoft Word"])
        self.assertEqual(apps[1].bundle_id, "")
        self.assertEqual(apps[2].pid, 98972)
        self.assertEqual(apps[3].app_type, "Foreground")


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "claude").mkdir()
        (root / "cursor").mkdir()
        self.cfg = config.Config(claude_dir=root / "claude", cursor_dir=root / "cursor", state_dir=root / "state",
                                 platform="macos", action="shutdown")
        apps = macos.parse_lsappinfo(LSAPPINFO)
        perms = {"com.todesktop.230313mzl4w4u92": macos.NO_ERR,
                 "com.microsoft.Word": macos.ERR_AE_EVENT_WOULD_REQUIRE_CONSENT}
        self.patches = [
            mock.patch.object(macos, "running_apps", side_effect=lambda foreground_only=True:
                              [a for a in apps if not foreground_only or a.app_type == "Foreground"]),
            mock.patch.object(macos, "host_app", return_value=None),
            mock.patch.object(macos, "automation_permission", side_effect=lambda b, ask=False: perms[b]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def by_name(self, checks):
        return {c.name: c for c in checks}

    def test_missing_sudo_and_notifications_fail(self):
        with mock.patch("good_night.shutdown.sudo_ok", return_value=(False, "a password is required")), \
             mock.patch.object(macos, "notifications_decided", return_value=False):
            checks = doctor.run_checks(self.cfg)
        c = self.by_name(checks)
        self.assertEqual(c["sudo shutdown rule"].status, doctor.FAIL)
        self.assertEqual(c["notifications"].status, doctor.FAIL)
        self.assertEqual(len(doctor.failed(checks)), 2)

    def test_dry_run_only_warns_on_sudo(self):
        with mock.patch("good_night.shutdown.sudo_ok", return_value=(False, "x")), \
             mock.patch.object(macos, "notifications_decided", return_value=True):
            checks = doctor.run_checks(self.cfg, dry_run=True)
        self.assertEqual(doctor.failed(checks), [])

    def test_all_good_and_quit_split(self):
        with mock.patch("good_night.shutdown.sudo_ok", return_value=(True, "")), \
             mock.patch.object(macos, "notifications_decided", return_value=True):
            checks = doctor.run_checks(self.cfg)
        self.assertEqual(doctor.failed(checks), [])
        q = self.by_name(checks)["quit apps"]
        self.assertIn("graceful: Cursor", q.detail)
        self.assertIn("Microsoft Word (not asked yet)", q.detail)

    def test_notifications_disabled_is_not_a_failure(self):
        self.cfg.notify = False
        with mock.patch("good_night.shutdown.sudo_ok", return_value=(True, "")), \
             mock.patch.object(macos, "notifications_decided", return_value=False):
            self.assertEqual(doctor.failed(doctor.run_checks(self.cfg)), [])

    def test_sleep_needs_no_sudo_and_no_quit_grants(self):
        self.cfg.action = ""  # macOS default = sleep
        with mock.patch("good_night.shutdown.sudo_ok", side_effect=AssertionError("sudo must not be checked")), \
             mock.patch.object(macos, "notifications_decided", return_value=True), \
             mock.patch.object(macos, "automation_permission", side_effect=AssertionError("no grants")):
            checks = doctor.run_checks(self.cfg)
        c = self.by_name(checks)
        self.assertEqual(doctor.failed(checks), [])
        self.assertIn("sleep", c["action"].detail)
        self.assertEqual(c["sudo shutdown rule"].status, doctor.INFO)
        self.assertEqual(c["quit apps"].status, doctor.INFO)

    def test_display_off_needs_no_sudo(self):
        self.cfg.action = "display-off"
        with mock.patch("good_night.shutdown.sudo_ok", return_value=(False, "nope")), \
             mock.patch.object(macos, "notifications_decided", return_value=True):
            self.assertEqual(doctor.failed(doctor.run_checks(self.cfg)), [])

    def test_hibernate_on_macos_fails_clearly(self):
        self.cfg.action = "hibernate"
        with mock.patch.object(macos, "notifications_decided", return_value=True):
            checks = doctor.run_checks(self.cfg)
        bad = doctor.failed(checks)
        self.assertEqual([c.name for c in bad], ["action"])
        self.assertIn("hibernatemode", bad[0].detail)

    def test_setup_marker_counts_as_decided(self):
        self.cfg.state_dir.mkdir()
        (self.cfg.state_dir / "notifications-initialized").touch()
        self.assertTrue(macos.notifications_decided(self.cfg.state_dir))


if __name__ == "__main__":
    unittest.main()
