# Quietbox fan guard

A small, dependency-free Python daemon in Docker for the two chassis/radiator
fan channels on quietbox4 and quietbox5. It reads AMD `k10temp` **Tctl**, not an
unreliable motherboard AUX temperature. The pump is deliberately excluded.

## Policy

- Quiet mode: calibrated **fixed PWM**, approximately 800 RPM. No oscillating
  RPM-feedback loop. Actual RPM varies slightly with the fans and environment.
- **Strictly above 90°C for 30 continuous sampled seconds:** maximum fan speed.
  A sample at or below 90°C resets that timer.
- Maximum mode lasts **at least 600 seconds**. Return to quiet only when the
  last **5 continuous sampled seconds are strictly below 85°C**. At or above
  85°C resets that timer. If still hot after ten minutes, remain at maximum;
  five cool seconds after that deadline are sufficient (no additional ten-minute
  block). Cooling during the final five seconds of the original hold counts.
- Poll every second, using a monotonic clock. A gap exceeding three polling
  intervals resets into a new ten-minute maximum hold.
- Every startup/restart begins at maximum for ten minutes. No persistent timer
  database: restarts can only extend the safety hold, never shorten it.
- Missing/invalid CPU or fan tachometer, or failed PWM readback: maximum, with a
  new ten-minute hold after the last fault. SIGTERM/SIGINT and ordinary exceptions
  attempt maximum before exit.

## Hardware and calibration

| Host | Fan 4 PWM / 255 | Fan 5 PWM / 255 | Sensor / controller |
| --- | ---: | ---: | --- |
| q4 | 163 | 162 | k10temp Tctl / nct6799 |
| q5 | 164 | 160 | k10temp Tctl / nct6799 |

Only `pwm4`, `pwm4_enable`, `pwm5`, `pwm5_enable` are writable by application
code. Channel 6 (pump, around 4,800 RPM) is never changed. Hardware discovery
matches the chip names beneath stable device paths, not changing `hwmonN`
indices. Ambiguous or missing chips fail closed. The config cannot select other
fan channels. These mappings were inspected on the two B850M-C systems; do not
reuse this deployment on a different machine without checking its wiring.

Quiet uses `pwm_enable=1` (manual duty); maximum uses `pwm_enable=0` (hardware
full-speed mode) and duty 255. See the [kernel driver documentation](https://www.kernel.org/doc/html/next/hwmon/nct6775.html).

## Docker deployment

No network, no published ports, no Docker socket, no privileged mode, all
capabilities dropped, read-only root filesystem. Only the fan-controller device
directory is writable; the CPU device and config are read-only. Logs are capped
at two 2-MB files. The Docker restart policy is `unless-stopped`.

Requires Docker Compose, the `nct6775` and `k10temp` drivers, and these verified
host paths:

```
/sys/devices/platform/nct6775.656
/sys/devices/pci0000:00/0000:00:18.3
```

From a clean committed checkout on each host:

```sh
export QUIETBOX=q4  # q5 on quietbox5
export REVISION=$(git rev-parse HEAD)
docker compose up -d --build --wait
docker exec quietbox-fan-guard python /app/fan_guard.py status
docker logs --tail 10 quietbox-fan-guard
```

Deployment can also set these two non-secret values in a local ignored `.env`.
Ordinary host/Docker restarts restart the installed container. A manually stopped
container stays stopped until explicitly started; stopping requests full speed.

Immediate maximum override (latches a fresh ten-minute hold):

```sh
docker exec quietbox-fan-guard python /app/fan_guard.py max
```

Status JSON reports mode, CPU temperature, both RPM readings, remaining hold,
heartbeat, and faults. This is local only; no new network management interface.

## Watchdog and limitations

Every five seconds a separate Docker healthcheck checks the loop heartbeat.
If it is missing/invalid or older than ten seconds, the healthcheck **actively
forces maximum speed** and leaves a trip marker. A resumed main loop consumes
that marker and starts another ten-minute hold. This is more than merely marking
the container unhealthy: Docker does not automatically restart unhealthy
containers. The process exit restart policy handles ordinary crashes separately.

This is not a hardware watchdog. It cannot guarantee fan changes during a host
hang, Docker daemon outage, inaccessible controller, or kill/pause of the entire
container that prevents healthcheck execution. Hardware CPU thermal protection
is unchanged, and remains necessary. Do not run another fan-control program
concurrently. The controller mount exposes more files than the application
writes; the narrow channel allowlist is enforced in code, not separate mounts.

## Tests

```sh
python3 -m unittest -v
```

Tests use fake sysfs only, including exact dwell boundaries, resets, cooldown
extension, restarts, missing/bad sensors, pump exclusion, write failures,
watchdog overrides, and real-process signal cleanup. No test heats a real CPU
or changes production thresholds. Runtime acceptance is recorded in
`docs/deployment.md`; unit-test timing proof is distinct from live thermal testing.
