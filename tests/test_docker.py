"""Opt-in Docker acceptance, fake sysfs only; never mounts host hardware."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid


@unittest.skipUnless(os.environ.get("FAN_GUARD_DOCKER_IMAGE"), "opt-in Docker test")
class DockerAcceptance(unittest.TestCase):
    def test_stalled_process_watchdog_resume_and_stop(self):
        image = os.environ["FAN_GUARD_DOCKER_IMAGE"]
        name = "fan-guard-test-" + uuid.uuid4().hex[:12]

        def docker(*args):
            return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT)

        def await_condition(predicate, timeout=30):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    if predicate():
                        return
                except (OSError, ValueError, subprocess.CalledProcessError):
                    pass
                time.sleep(0.5)
            self.fail(f"condition timed out; logs: {docker('logs', '--tail', '10', name)}")

        with tempfile.TemporaryDirectory(prefix="fan-guard-docker-test-") as tmp:
            root = Path(tmp)
            fan = root / "fan/hwmon/hwmon98"
            cpu = root / "cpu/hwmon/hwmon97"
            fan.mkdir(parents=True)
            cpu.mkdir(parents=True)
            (fan / "name").write_text("nct6799")
            (cpu / "name").write_text("k10temp")
            (cpu / "temp1_label").write_text("Tctl")
            (cpu / "temp1_input").write_text("60000")
            for n in (4, 5, 6):
                (fan / f"pwm{n}").write_text("255")
                (fan / f"pwm{n}_enable").write_text("5")
                (fan / f"fan{n}_input").write_text("1100")
                # Production sysfs is root-owned. Fixtures belong to the test
                # user; with all capabilities dropped container root cannot
                # bypass their mode bits. Grant writes only on these fake files.
                (fan / f"pwm{n}").chmod(0o666)
                (fan / f"pwm{n}_enable").chmod(0o666)
            (root / "config.json").write_text(json.dumps({
                "quiet_pwm4": 163, "quiet_pwm5": 162, "boost_seconds": 2,
                "cool_seconds": 1, "hot_seconds": 2,
            }))
            created = False
            try:
                docker("run", "-d", "--name", name, "--network", "none", "--read-only",
                       "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                       "--pids-limit", "32", "--memory", "64m", "--cpus", "0.25",
                       "--tmpfs", "/run/fan-guard:rw,noexec,nosuid,size=1m,mode=0700",
                       "-v", f"{root / 'fan'}:/hardware/fan:rw",
                       "-v", f"{root / 'cpu'}:/hardware/cpu:ro",
                       "-v", f"{root / 'config.json'}:/config/config.json:ro", image)
                created = True
                await_condition(lambda: (fan / "pwm4").read_text().strip() == "163")
                await_condition(lambda: docker("inspect", "--format", "{{.State.Health.Status}}", name).strip() == "healthy")
                # STOP the main process, not 'docker pause' (which freezes healthchecks too).
                docker("kill", "--signal", "STOP", name)
                await_condition(lambda: (fan / "pwm4_enable").read_text().strip() == "0")
                self.assertEqual((fan / "pwm4").read_text().strip(), "255")
                await_condition(lambda: docker("inspect", "--format", "{{.State.Health.Status}}", name).strip() == "unhealthy")
                docker("kill", "--signal", "CONT", name)
                await_condition(lambda: (fan / "pwm4").read_text().strip() == "163")
                await_condition(lambda: docker("inspect", "--format", "{{.State.Health.Status}}", name).strip() == "healthy")
                self.assertEqual((fan / "pwm6_enable").read_text(), "5")
                docker("stop", "--time", "5", name)
                self.assertEqual(docker("inspect", "--format", "{{.State.ExitCode}}", name).strip(), "0")
                self.assertEqual((fan / "pwm4_enable").read_text().strip(), "0")
                self.assertEqual((fan / "pwm5_enable").read_text().strip(), "0")
                self.assertEqual((fan / "pwm6_enable").read_text(), "5")
            finally:
                if created:
                    subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)
