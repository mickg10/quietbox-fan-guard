# quietbox-fan-guard

Independent safety controller for quietbox4/5 chassis fans, not the photo or
trading project. Python standard library only. Run `python3 -m unittest -v`.
Only NCT6799 channels 4 and 5 may be written; channel 6 is the pump and must
remain untouched. Never use RPM-feedback cruise mode (it oscillates here).
Use fake sysfs for tests, never real hardware or modified production thresholds.
Sensor/control failures and shutdown must attempt maximum speed on both fans.
Do not claim protection against host/Docker failure: the Docker watchdog needs
Docker itself to remain running. Preserve per-host calibration and verify the
actual hardware before deploying to another machine.
