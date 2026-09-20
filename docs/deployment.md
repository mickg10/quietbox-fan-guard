# Deployment and acceptance — 2026-09-20

Historical initial deployment (private at the time; subsequently made public):
https://github.com/mickg10/quietbox-fan-guard

## Installed runtime

- Both hosts: a `quietbox-fan-guard` checkout under the operator's source directory.
- Container: `quietbox-fan-guard`.
- Runtime image tag / OCI revision:
  `7ea503dd8dc85665b9e84f74c2d20f9c14c67f80`.
- Follow-up `e842fc4` adds Docker acceptance tests and documentation only;
  it does not change daemon, Dockerfile, Compose, or host configuration.
- Actual `/app/fan_guard.py` SHA-256 on **both running containers**:
  `615a29c683dbace3cd9c4c84c185536a9581381d183a0b96d5d845fe5528f6e4`.
- Config hashes verified against source: q4
  `74449c18dfb28f60ed0cba8a77c40895022a0c4ce3167d1867f7853cc2b53e9a`,
  q5 `0f84c551dd4e4b36fb7d9acc87b296e9a95f262a00b340f57dc1fbab10255701`.
- Docker enabled at host boot; container restart `unless-stopped`.
- `/etc/modules-load.d/quietbox-fan-guard.conf` loads `nct6775` and `k10temp`.
- No privileged mode, no network, no Docker socket. Read-only rootfs, CPU sensor
  and config mounts; writable fan-controller mount only. All capabilities dropped.
- Startup boost began 12:05:39 UTC on q4 and approximately 12:06:26 UTC on q5.
  Real full-speed readings were about 1,130–1,150 RPM. Both containers healthy,
  zero restarts. The daemon processes measured ~0.2% CPU each.
- Pump channel 6 remained PWM=255, mode=5 throughout; no pump target is applied.

## Tests completed

- 21 standard-library tests passed locally and on each host; the optional Docker
  test is skipped unless its image environment variable is supplied.
- Actual Docker acceptance passed independently on q4 (23.316 seconds) and q5
  (23.335 seconds), using fake sysfs, never host hardware. It proves startup
  quiet transition, main-process STOP, active watchdog maximum override,
  unhealthy state, CONT recovery, healthy state, and SIGTERM maximum-speed exit.
- The first fake-hardware Docker test exposed a fixture permissions error:
  capability-free root could not write the test user's files. Only fake PWM
  file permissions were adjusted; production container privileges were not widened.
- [GitHub CI](https://github.com/mickg10/quietbox-fan-guard/actions/runs/35509810698)
  passed unit tests, Docker build, and isolated Docker acceptance at `e842fc4`.

## Scope of verification

The 90°C / 30-second trigger, interrupted dwell, 85°C boundary, five-second
cooldown, ten-minute minimum, repeated cycles, delayed polling and restart
semantics are covered with deterministic tests. Process tests exercise actual
file I/O and signals with shortened test-only thresholds. Production thresholds
remain 90°C / 30s / 600s / 85°C / 5s.

No live CPU heat/stress test or host reboot was performed. Docker/watchdog tests
do not prove protection during a host freeze or Docker-daemon outage.

## Live ten-minute acceptance

Observed the full production-duration startup hold on both hosts (no shortened
production timers):

| Host | Quiet transition (UTC) | Settled fan 4 / fan 5 | CPU at final readback |
| --- | --- | --- | --- |
| q4 | 12:15:40 | 807 / 795 RPM | 57.625°C |
| q5 | 12:16:27 | 804 / 794 RPM | 63.125°C |

Both held maximum for at least 600 monotonic seconds despite cool CPU samples,
then switched automatically to manual fixed PWM. Final states were `quiet`,
`healthy=true`, zero remaining hold and zero container restarts. PWM readback:
q4 163/162, q5 164/160. Pump channel 6 on both remained PWM=255, mode=5.

The final repository includes later test/documentation commits; the running
image intentionally remains at the exact runtime revision above. Daemon,
Dockerfile, Compose and host configs are byte-identical to that revision, verified
by source diff and running-container hashes. No recreation was performed just
to incorporate tests/docs, which would unnecessarily restart the safety hold.
