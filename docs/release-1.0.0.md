# Public release and live CPU benchmark — 2026-09-20

## Public distribution

Repository visibility changed from private to public after reviewing all tracked
files and all three pre-release commits for credentials, private keys, tokens,
and private network addresses. No credentials were found. The historical
deployment document was marked as historical and its operator home path was
generalized; no history rewrite was necessary.

- Release tag: `v1.0.0`, runtime revision `3d3ab720dc29dc649d2f2e95dc0dcb112fa2216f`.
- Public image: `ghcr.io/mickg10/quietbox-fan-guard:1.0.0`.
- Registry digest:
  `sha256:d1d0432c6334fe1e9f687f82b2707f7ea1e903e3a7690fa6cef05d86a175a114`.
- [Publication workflow](https://github.com/mickg10/quietbox-fan-guard/actions/runs/35510813771)
  passed unit tests, image build, and isolated Docker watchdog acceptance before
  pushing the image. The regular CI workflows also passed.
- Anonymous pull was verified on q4 with an empty/nonexistent Docker credential
  directory. No GitHub login or source checkout is needed to install.
- The image bundles both calibration profiles. Set `QUIETBOX_PROFILE=q4` or
  `QUIETBOX_PROFILE=q5`; unknown names/path traversal are rejected.
- The README command uses `--restart=always`, not `unless-stopped`. Host Docker
  is enabled at boot on both machines. No disruptive host reboot was performed.

## Live q4 benchmark

Explicitly user-authorized live CPU test, using the already installed TT Max
controller. No synthetic temperature injection or reduced fan-guard thresholds.

- Benchmark ID: `7e1a8de801ed`.
- Duration: 240 seconds, 32 CPU workers, no GPU workload, no memory-streaming
  workload, CPU emergency cutoff 97°C. Hardware power/thermal limits unchanged.
- Started 12:25:58.747 UTC, finished 12:29:58.886 UTC.
- Result: **completed**, all 32 workers verified, all exited zero; 982,076 total
  workload iterations. 219 telemetry samples, CPU utilization reached 100%.
- Peak observed CPU temperature: **96.875°C**. First recorded sample above 90°C
  was 12:26:00.974 UTC. The fan daemon samples independently once per second.
- At 12:26:12 UTC the daemon still reported quiet mode, 95.375°C and 811/803 RPM.
- At **12:26:32.533 UTC** it entered boost with a fresh **600-second** hold.
  Tachometers then rose to roughly **1,130–1,150 RPM**. The daemon remained
  healthy throughout the full CPU workload, without watchdog trips/restarts.
- After load completion the CPU fell below 85°C, but fans correctly remained
  at maximum until the ten-minute minimum elapsed.

This thermal test exercised the initially deployed `7ea503d` control loop before
replacing it with the registry release. `v1.0.0` adds only packaged-profile
selection to the Python code; the temperature policy and hardware write logic
are unchanged. The registry image itself is covered by Docker acceptance and
the one-command installation checks. This distinction avoids claiming a second
live heat test against a different image revision.

## Completed cooldown and installation

At **12:36:33.022 UTC**, after 600.486 monotonic seconds in boost and with the
CPU below 85°C, q4 automatically returned to quiet mode. Settled readings were
**814/801 RPM**, CPU **57.5°C**, healthy with no fault. This completes the live
quiet → sustained heat → maximum → ten-minute hold → quiet cycle.

After this observation, q4's source-built container was gracefully replaced by
the public image using the README's single `docker run` command. Q5 was migrated
with the same command and its `q5` profile. The old containers were removed;
their images and source checkouts were retained for rollback. Q5's initial
registry request timed out, so the identical pulled q4 image was also transferred
over SSH; its subsequent registry retry succeeded. This was not an image change.

Both registry containers use OCI revision
`3d3ab720dc29dc649d2f2e95dc0dcb112fa2216f`, profile environment selection (no
host config-file mount), and restart policy **always**. Both passed live health
checks. Pumps remain untouched. Replacing the containers intentionally starts
a fresh conservative ten-minute startup hold; these are not stuck in boost.
