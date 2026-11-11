## 1. Measurements

- [x] 1.1 Time 5×5 and 9×9 full-bed meshes on a cold Max 4 with the nozzle at 140 °C, and measure how much back-to-back meshes differ, to set the default mesh size and the stopped-moving limit.
- [x] 1.2 Time `Z_TILT_ADJUST` and the following Z home on the Max 4, for the test's setup time.
- [x] 1.3 Measure a full soak at one bed and chamber temperature by hand, to check that height, shape, and speed settle as expected and that the probe doesn't drift with time alone.
- [x] 1.4 Find what caused the 50 µm probe jump on the test printer, starting with the plate at X195 Y105.

## 2. Klipper module

- [x] 2.1 Add `tltg_bed_soak` with `TLTG_BED_SOAK`, `TLTG_BED_SOAK_STOP`, and the `tltg_bed_soak/stop` endpoint, and enable it from `bed_soak.cfg`.
- [x] 2.2 Run the setup (nozzle to `start_extruder_probing_temp`, `G28`, chute wipe, `Z_TILT_ADJUST`, Z home), the cold mesh, heating, and meshes one at a time from a reactor timer, and refuse to start or end the test as the spec says.
- [x] 2.3 Skip the profile save for test meshes, read each probed matrix, and restore the previously loaded mesh at the end.
- [x] 2.4 Compute height, shape, speed, and the milestones, publish the small status, and append the run file once per mesh.
- [x] 2.5 Add `tltg_probe_report`, which records retried points for every bed mesh, and use it in the test with the check point, out-of-line points, and jumps.
- [x] 2.6 Add the probe retry limit to `tltg_klipper_fixes` for QIDI's `ProbeSessionHelper.run_probe`, re-zeroing the load cell for each retry once the nozzle is off the bed, and check it on the Max 4 by forcing a point to disagree (`PROBE SAMPLES_TOLERANCE=0.0001 SAMPLES_TOLERANCE_RETRIES=3`).

## 3. Fluidd

- [x] 3.1 Add the Bed soak card under Bed mesh on the Tune page in the Fluidd fork, with Test and Past runs tabs and problem points, as in the reviewed mockups.
- [x] 3.3 Mark `tltg_probe_report` points on the Bed mesh card and show their count in Bed mesh controls.
- [x] 3.2 Release the fork and pin it in `installer/package.yaml`.

## 4. Tests and hardware

- [x] 4.1 Cover the milestone and stopped-moving rules, the refusals, and leaving no profile or pending save, in the existing installer and module test suites.
- [x] 4.2 On the test Max 4, run the test at two bed and chamber combinations, stop one run partway, and check that heaters, the loaded mesh, profiles, and pending saves end as the spec says.

## 5. Release

- [x] 5.1 Describe the retry limit in `docs/fixes.md`. Document the test in `docs/tools.md`, the commands in `docs/settings.md`, the module and run folder in `docs/under-the-hood.md`, and add it to the Tools line in `docs/README.md`.
- [x] 5.2 Bump the package version and add the `CHANGELOG.md` section.
- [x] 5.3 Run `python3 scripts/format_klipper_configs.py`, `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
