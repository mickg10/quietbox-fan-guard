"""Quietbox fixed-PWM fan guard. No pump control, network, or dependencies."""
import argparse
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import signal
import threading
import time
from dataclasses import dataclass


LOG = logging.getLogger("fan-guard")


@dataclass(frozen=True)
class Config:
    quiet_pwm4: int
    quiet_pwm5: int
    hot_c: float = 90.0
    hot_seconds: float = 30.0
    boost_seconds: float = 600.0
    cool_c: float = 85.0
    cool_seconds: float = 5.0
    poll_seconds: float = 1.0

    def __post_init__(self):
        for value in (self.quiet_pwm4, self.quiet_pwm5):
            if type(value) is not int or not 1 <= value <= 255:
                raise ValueError("quiet PWM must be an integer in 1..255")
        values = (self.hot_c, self.hot_seconds, self.boost_seconds,
                  self.cool_c, self.cool_seconds, self.poll_seconds)
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("thresholds and durations must be finite and positive")
        if not self.cool_c < self.hot_c < 125:
            raise ValueError("require cool_c < hot_c < 125")
        if self.poll_seconds > min(1, self.hot_seconds, self.cool_seconds):
            raise ValueError("poll interval must be <= 1 second and dwell durations")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text()))


class Policy:
    """Monotonic, sampled continuous dwell times; conservative startup/restart."""
    def __init__(self, config, now):
        self.config = config
        self.mode = "boost"
        self.boost_until = now + config.boost_seconds
        self.hot_since = None
        self.cool_since = None
        self.last_sample = None

    def fault(self, now):
        self.mode = "boost"
        self.boost_until = now + self.config.boost_seconds
        self.hot_since = self.cool_since = None
        self.last_sample = None

    def update(self, now, temperature):
        if not math.isfinite(temperature) or not 0 < temperature < 125:
            self.fault(now)
            raise ValueError("invalid CPU temperature")
        c = self.config
        if self.last_sample is not None and (
            now < self.last_sample or now - self.last_sample > 3 * c.poll_seconds
        ):
            self.fault(now)
        self.last_sample = now
        if self.mode == "quiet":
            if temperature > c.hot_c:
                if self.hot_since is None:
                    self.hot_since = now
                if now - self.hot_since >= c.hot_seconds:
                    self.fault(now)
                    self.last_sample = now
            else:
                self.hot_since = None
        else:
            if temperature < c.cool_c:
                if self.cool_since is None:
                    self.cool_since = now
            else:
                self.cool_since = None
            if (now >= self.boost_until and self.cool_since is not None
                    and now - self.cool_since >= c.cool_seconds):
                self.mode = "quiet"
                self.hot_since = self.cool_since = None
        return self.mode


def discover(root, name):
    matches = []
    for path in Path(root).glob("hwmon/hwmon*"):
        try:
            if (path / "name").read_text().strip() == name:
                matches.append(path)
        except OSError:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} controller under {root}, got {len(matches)}")
    return matches[0]


class Hardware:
    # Hardcoded allowlist deliberately cannot address the pump, channel 6.
    CHANNELS = (4, 5)

    def __init__(self, fan_root, cpu_root):
        self.fan_root = Path(fan_root)
        self.cpu_root = Path(cpu_root)

    def fans(self):
        return discover(self.fan_root, "nct6799")

    def temperature(self):
        cpu = discover(self.cpu_root, "k10temp")
        labels = [p for p in cpu.glob("temp*_label") if p.read_text().strip() == "Tctl"]
        if len(labels) != 1:
            raise RuntimeError("expected one CPU Tctl sensor")
        value = int(labels[0].with_name(labels[0].name.replace("_label", "_input")).read_text()) / 1000
        if not 0 < value < 125:
            raise ValueError(f"invalid CPU Tctl: {value}")
        return value

    @staticmethod
    def write(path, value):
        if int(path.read_text()) != value:
            path.write_text(str(value) + "\n")

    def apply(self, mode, config):
        fan = self.fans()
        failures = []
        for channel, quiet in zip(self.CHANNELS, (config.quiet_pwm4, config.quiet_pwm5)):
            try:
                pwm = fan / f"pwm{channel}"
                enable = fan / f"pwm{channel}_enable"
                desired, control = (255, 0) if mode == "boost" else (quiet, 1)
                # Stage duty before switching, then reassert after switching modes.
                self.write(pwm, desired)
                self.write(enable, control)
                self.write(pwm, desired)
                if int(pwm.read_text()) != desired or int(enable.read_text()) != control:
                    raise RuntimeError(f"channel {channel} did not retain PWM/mode")
            except (OSError, ValueError, RuntimeError) as exc:
                failures.append(f"channel {channel}: {exc}")
        if failures:
            raise RuntimeError("; ".join(failures))

    def speeds(self):
        fan = self.fans()
        result = {str(n): int((fan / f"fan{n}_input").read_text()) for n in self.CHANNELS}
        if any(rpm <= 0 or rpm > 20000 for rpm in result.values()):
            raise RuntimeError(f"invalid/stalled fan tachometer: {result}")
        return result


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data) + "\n")
    os.replace(tmp, path)


def failsafe(hw, config):
    try:
        hw.apply("boost", config)
        return True
    except Exception:
        LOG.exception("FAILED to force maximum fan speed; operator action required")
        return False


def config_path(explicit=None, profile=None, config_dir=None):
    """Explicit file, packaged named profile, or legacy Compose config mount."""
    if explicit is not None:
        return Path(explicit)
    if profile is not None:
        if profile not in ("q4", "q5"):
            raise ValueError("QUIETBOX_PROFILE must be q4 or q5")
        base = Path(config_dir) if config_dir else Path(__file__).resolve().parent / "config"
        return base / f"{profile}.json"
    return Path("/config/config.json")


def watchdog(hw, config, runtime, now=None):
    """Separate Docker healthcheck process: stale loop => force max, latch trip."""
    now = time.monotonic() if now is None else now
    try:
        status = json.loads((runtime / "status.json").read_text())
        age = now - status["monotonic"]
        if not 0 <= age <= 10:
            raise RuntimeError(f"stale heartbeat: {age:.1f}s")
        return 0 if status["healthy"] else 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        LOG.error("watchdog forcing maximum speed: missing/invalid/stale heartbeat")
        # Trip stays until main loop consumes it and starts a new ten-minute hold.
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "watchdog-trip").touch()
        failsafe(hw, config)
        return 1


def run(hw, config, runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "daemon.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        policy = Policy(config, time.monotonic())
        logged_mode = None
        next_log = 0.0
        try:
            hw.apply("boost", config)
            while not stop.is_set():
                now = time.monotonic()
                temperature, rpm, error = None, {}, None
                try:
                    trip = runtime / "watchdog-trip"
                    if trip.exists():
                        policy.fault(now)
                        trip.unlink()
                    temperature = hw.temperature()
                    mode = policy.update(now, temperature)
                    hw.apply(mode, config)
                    rpm = hw.speeds()
                except Exception as exc:
                    error = str(exc)
                    policy.fault(now)
                    failsafe(hw, config)
                    LOG.error("fail-safe: %s", error)
                status = {
                    "mode": policy.mode, "cpu_c": temperature, "rpm": rpm,
                    "healthy": error is None, "error": error, "monotonic": now,
                    "boost_remaining_s": max(0, policy.boost_until - now) if policy.mode == "boost" else 0,
                    "quiet_pwm": {"4": config.quiet_pwm4, "5": config.quiet_pwm5},
                }
                atomic_json(runtime / "status.json", status)
                if policy.mode != logged_mode or now >= next_log:
                    LOG.info(json.dumps(status))
                    logged_mode, next_log = policy.mode, now + 30
                stop.wait(config.poll_seconds)
        finally:
            failsafe(hw, config)
            LOG.info("exiting with maximum fan speed requested")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "watchdog", "status", "max"))
    parser.add_argument("--config", help="override the packaged profile or Compose config")
    parser.add_argument("--fan-root", default="/hardware/fan")
    parser.add_argument("--cpu-root", default="/hardware/cpu")
    parser.add_argument("--runtime", default="/run/fan-guard")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.load(config_path(args.config, os.environ.get("QUIETBOX_PROFILE")))
    hw, runtime = Hardware(args.fan_root, args.cpu_root), Path(args.runtime)
    if args.command == "run":
        run(hw, config, runtime)
    elif args.command == "watchdog":
        return watchdog(hw, config, runtime)
    elif args.command == "max":
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "watchdog-trip").touch()
        return 0 if failsafe(hw, config) else 1
    else:
        print((runtime / "status.json").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
