import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from fan_guard import Config, Policy, Hardware, atomic_json, watchdog, failsafe, config_path


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.c = Config(163, 162)
        self.p = Policy(self.c, 0)

    def advance(self, start, end, temp):
        for t in range(start, end + 1):
            self.p.update(t, temp)
        return self.p.mode

    def quiet(self):
        self.assertEqual(self.advance(0, 600, 60), "quiet")

    def test_startup_full_ten_minutes(self):
        self.assertEqual(self.advance(0, 599, 60), "boost")
        self.assertEqual(self.p.update(600, 60), "quiet")

    def test_exact_hot_dwell_and_second_boost(self):
        self.quiet()
        self.assertEqual(self.advance(601, 630, 91), "quiet")
        self.assertEqual(self.p.update(631, 91), "boost")
        self.assertEqual(self.advance(632, 1230, 60), "boost")
        self.assertEqual(self.p.update(1231, 60), "quiet")
        self.assertEqual(self.advance(1232, 1262, 91), "boost")

    def test_hot_dwell_resets_at_90(self):
        self.quiet()
        self.advance(601, 629, 91)
        self.p.update(630, 90)
        self.assertEqual(self.advance(631, 660, 91), "quiet")
        self.assertEqual(self.p.update(661, 91), "boost")

    def test_last_five_seconds_must_be_below_85(self):
        self.advance(0, 599, 86)
        self.assertEqual(self.advance(600, 604, 84), "boost")
        self.p.update(605, 85)
        self.assertEqual(self.advance(606, 610, 84), "boost")
        self.assertEqual(self.p.update(611, 84), "quiet")

    def test_stays_full_indefinitely_if_hot(self):
        self.assertEqual(self.advance(0, 3000, 95), "boost")

    def test_poll_gap_forces_fresh_full_hold(self):
        self.quiet()
        self.assertEqual(self.p.update(615, 60), "boost")
        self.assertEqual(self.p.boost_until, 1215)

    def test_clock_backwards_is_fail_safe(self):
        self.quiet()
        self.assertEqual(self.p.update(599, 60), "boost")

    def test_bad_temperature_forces_full(self):
        for value in (0, -10, 125, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.p.update(601, value)
                self.assertEqual(self.p.mode, "boost")

    def test_fault_resets_cool_hold(self):
        self.quiet()
        self.p.fault(601)
        self.assertEqual(self.advance(602, 1200, 60), "boost")
        self.assertEqual(self.p.update(1201, 60), "quiet")

    def test_restart_cannot_forget_boost(self):
        self.quiet()
        restarted = Policy(self.c, 800)
        self.assertEqual(restarted.mode, "boost")
        self.assertEqual(restarted.boost_until, 1400)

    def test_invalid_config(self):
        for kw in ({"quiet_pwm4": 0}, {"quiet_pwm5": 256}, {"cool_c": 91},
                   {"hot_seconds": float("nan")}, {"poll_seconds": 2},
                   {"quiet_pwm4": True}):
            with self.assertRaises(ValueError):
                Config(**({"quiet_pwm4": 163, "quiet_pwm5": 162} | kw))


class ProfileTests(unittest.TestCase):
    def test_packaged_profiles(self):
        self.assertEqual(Config.load(config_path(profile="q4")).quiet_pwm4, 163)
        self.assertEqual(Config.load(config_path(profile="q5")).quiet_pwm5, 160)

    def test_explicit_config_takes_precedence(self):
        self.assertEqual(config_path("/example.json", "q4"), Path("/example.json"))

    def test_legacy_compose_mount(self):
        self.assertEqual(config_path(), Path("/config/config.json"))

    def test_unknown_profile_and_path_traversal_rejected(self):
        for value in ("", "other", "../q4", "/q4"):
            with self.assertRaises(ValueError):
                config_path(profile=value)


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.fan_root, self.cpu_root = root / "fan", root / "cpu"
        self.fan = self.fan_root / "hwmon/hwmon42"
        self.cpu = self.cpu_root / "hwmon/hwmon99"
        self.fan.mkdir(parents=True)
        self.cpu.mkdir(parents=True)
        (self.fan / "name").write_text("nct6799\n")
        (self.cpu / "name").write_text("k10temp\n")
        (self.cpu / "temp1_label").write_text("Tctl\n")
        (self.cpu / "temp1_input").write_text("56000\n")
        for n in (4, 5, 6):
            for suffix, value in (("", "255"), ("_enable", "5")):
                (self.fan / f"pwm{n}{suffix}").write_text(value)
            (self.fan / f"fan{n}_input").write_text("1100")
        self.hw = Hardware(self.fan_root, self.cpu_root)
        self.c = Config(163, 162)
        self.runtime = root / "runtime"
        self.runtime.mkdir()

    def test_temperature_uses_label_and_dynamic_hwmon_number(self):
        self.assertEqual(self.hw.temperature(), 56)

    def test_quiet_and_boost_never_touch_pump(self):
        self.hw.apply("quiet", self.c)
        self.assertEqual((self.fan / "pwm4").read_text().strip(), "163")
        self.assertEqual((self.fan / "pwm5").read_text().strip(), "162")
        self.assertEqual((self.fan / "pwm4_enable").read_text().strip(), "1")
        self.hw.apply("boost", self.c)
        self.assertEqual((self.fan / "pwm4").read_text().strip(), "255")
        self.assertEqual((self.fan / "pwm4_enable").read_text().strip(), "0")
        self.assertEqual((self.fan / "pwm6_enable").read_text(), "5")

    def test_missing_or_wrong_sensor_fails_closed(self):
        (self.cpu / "name").write_text("wrong")
        with self.assertRaises(RuntimeError):
            self.hw.temperature()
        self.assertTrue(failsafe(self.hw, self.c))

    def test_missing_one_fan_still_attempts_other(self):
        (self.fan / "pwm4").unlink()
        with self.assertRaises(RuntimeError):
            self.hw.apply("boost", self.c)
        self.assertEqual((self.fan / "pwm5_enable").read_text().strip(), "0")

    def test_failed_write_readback_detected(self):
        with patch.object(self.hw, "write"):
            with self.assertRaises(RuntimeError):
                self.hw.apply("quiet", self.c)

    def test_stalled_fan(self):
        (self.fan / "fan4_input").write_text("0")
        with self.assertRaises(RuntimeError):
            self.hw.speeds()

    def test_watchdog_stale_forces_max_and_latches(self):
        self.hw.apply("quiet", self.c)
        atomic_json(self.runtime / "status.json", {"monotonic": 10, "healthy": True})
        self.assertEqual(watchdog(self.hw, self.c, self.runtime, now=21), 1)
        self.assertTrue((self.runtime / "watchdog-trip").exists())
        self.assertEqual((self.fan / "pwm4").read_text().strip(), "255")

    def test_watchdog_healthy_does_not_change_quiet_pwm(self):
        self.hw.apply("quiet", self.c)
        atomic_json(self.runtime / "status.json", {"monotonic": 10, "healthy": True})
        self.assertEqual(watchdog(self.hw, self.c, self.runtime, now=15), 0)
        self.assertEqual((self.fan / "pwm4").read_text().strip(), "163")

    def test_watchdog_missing_or_malformed_status(self):
        for content in (None, "garbage", "{}", "null"):
            if content is not None:
                (self.runtime / "status.json").write_text(content)
            self.assertEqual(watchdog(self.hw, self.c, self.runtime, now=15), 1)

    def test_real_process_shutdown_and_sensor_fault(self):
        config = self.runtime / "config.json"
        config.write_text(json.dumps({"quiet_pwm4": 163, "quiet_pwm5": 162,
                                      "boost_seconds": 1, "poll_seconds": 0.1,
                                      "cool_seconds": 0.2, "hot_seconds": 0.2}))
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "fan_guard.py"),
                   "run", "--config", str(config), "--fan-root", str(self.fan_root),
                   "--cpu-root", str(self.cpu_root), "--runtime", str(self.runtime)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def wait_for(predicate):
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail(f"daemon exited early: {process.returncode}")
                try:
                    status = json.loads((self.runtime / "status.json").read_text())
                    if predicate(status):
                        return status
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            self.fail("daemon condition not reached")

        try:
            wait_for(lambda s: s["mode"] == "quiet")
            (self.cpu / "temp1_input").write_text("91000")
            wait_for(lambda s: s["mode"] == "boost")
            (self.cpu / "temp1_input").write_text("60000")
            wait_for(lambda s: s["mode"] == "quiet")
            (self.cpu / "temp1_input").unlink()
            wait_for(lambda s: not s["healthy"] and s["mode"] == "boost")
            self.assertEqual((self.fan / "pwm4").read_text().strip(), "255")
            (self.cpu / "temp1_input").write_text("60000")
            wait_for(lambda s: s["mode"] == "quiet")
            process.terminate()
            self.assertEqual(process.wait(timeout=3), 0)
            self.assertEqual((self.fan / "pwm4_enable").read_text().strip(), "0")
            self.assertEqual((self.fan / "pwm5_enable").read_text().strip(), "0")
            self.assertEqual((self.fan / "pwm6_enable").read_text(), "5")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
