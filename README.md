# Quietbox fan guard

[![tests](https://github.com/mickg10/quietbox-fan-guard/actions/workflows/test.yml/badge.svg)](https://github.com/mickg10/quietbox-fan-guard/actions/workflows/test.yml)

A small, dependency-free Python daemon in Docker for the two chassis/radiator
fan channels on quietbox4 and quietbox5. It reads AMD `k10temp` **Tctl**, not an
unreliable motherboard AUX temperature. The pump is deliberately excluded.

**800 RPM normally → CPU >90°C for 30s → maximum for at least 10 minutes →
800 RPM after the final 5s below 85°C.**

This is hardware-specific software for the verified ASRock B850M-C/NCT6799
fan mapping below, not a universal fan installer. Do not run it alongside
another fan controller.

## Install: one Docker command

Requires Linux Docker Engine enabled at boot, with `nct6775` and `k10temp`
already loaded. They are configured persistently on q4/q5. Other hosts must
first verify their controller paths and fan wiring; this container deliberately
does not have privileges to load host kernel modules.

For **q4**, paste this single command (line continuations are just formatting).
For **q5**, change only `QUIETBOX_PROFILE=q4` to `QUIETBOX_PROFILE=q5`.
No checkout, Compose, config file, or registry login is needed for the public image.

```sh
docker run -d --name quietbox-fan-guard --restart=always \
  --network=none --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --pids-limit=32 --memory=64m --cpus=0.25 --stop-timeout=10 \
  --log-driver=json-file --log-opt=max-size=2m --log-opt=max-file=2 \
  --tmpfs /run/fan-guard:rw,noexec,nosuid,size=1m,mode=0700 \
  --mount type=bind,src=/sys/devices/platform/nct6775.656,dst=/hardware/fan \
  --mount type=bind,src=/sys/devices/pci0000:00/0000:00:18.3,dst=/hardware/cpu,readonly \
  -e QUIETBOX_PROFILE=q4 \
  ghcr.io/mickg10/quietbox-fan-guard:1.0.0
```

**The first ten minutes run at maximum speed.** This is a conservative startup
hold, not failed calibration. `--restart=always` restarts the container after a
crash or host/Docker restart, including after a manual stop followed by reboot.
Docker itself must start at boot. Removing the container uninstalls it; merely
stopping it does not permanently disable it across reboots.

If a container with this name already exists, do not start a second controller:
stop it gracefully first (`docker stop quietbox-fan-guard`, which requests max
fans), remove that stopped container, then run the installation command.
Keep the old image available for rollback. No automatic image updates are installed.

## Status and manual override

```sh
docker exec quietbox-fan-guard python /app/fan_guard.py status
docker logs --tail 10 quietbox-fan-guard
docker exec quietbox-fan-guard python /app/fan_guard.py max
```

`max` latches a fresh ten-minute hold. Status reports mode, CPU temperature,
both RPM readings, remaining hold, heartbeat and faults. Nothing is exposed on
the network. Normal stop/SIGTERM requests full speed, not the quiet PWM.

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

## Build from source (optional)

No network, no published ports, no Docker socket, no privileged mode, all
capabilities dropped, read-only root filesystem. Only the fan-controller device
directory is writable; the CPU device and config are read-only. Logs are capped
at two 2-MB files. The Docker restart policy is `always`.

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
Compose uses a read-only config mount; the published-image command uses the
built-in profile. An explicit `--config` takes precedence, then
`QUIETBOX_PROFILE`, then the legacy `/config/config.json` mount. Unknown profile
names are rejected. None of these options permit changing the pump channel.

Release tags `v*` trigger unit tests, image build and isolated Docker safety
acceptance **before** publication to GHCR. Images carry an OCI source/revision
label and are tagged by version, full Git revision, and `latest`. The base image
is digest-pinned. Only x86-64 hardware is currently supported and tested.

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

Opt-in Docker acceptance against an already built image (temporary fake sensor
files only, about 30 seconds):

```sh
FAN_GUARD_DOCKER_IMAGE=quietbox-fan-guard:$REVISION python3 -m unittest -v tests.test_docker
```

This suspends a disposable container's main process, proves its separate
healthcheck forces maximum duty, resumes it, and proves clean shutdown also
forces maximum. The test removes only its own uniquely named container and
temporary fake-sensor directory.

Automated tests use fake sysfs only, including exact dwell boundaries, resets, cooldown
extension, restarts, missing/bad sensors, pump exclusion, write failures,
watchdog overrides, and real-process signal cleanup. Automated tests do not heat
a real CPU or change production thresholds. See the dated
[initial deployment](docs/deployment.md) and
[release and live benchmark](docs/release-1.0.0.md) records for live evidence and
its limitations.
