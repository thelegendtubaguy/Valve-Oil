## Context

On the Max 4 the nozzle's load cell (`[probe_air]`, `c_sensor`) is both the bed probe and the Z endstop. Print start heats the bed (waiting until it is within 1 °C of target) and the chamber (waiting for target minus 3 °C, or the slicer's minimum chamber temperature). It then homes Z, measures an adaptive mesh with the nozzle at `start_extruder_probing_temp` (140 °C), and prints. Any bed movement after that mesh shows up in the first layers. A whole-bed height change shifts the first-layer squish, and a shape change makes the mesh stale.

The [Soak My Bed](https://github.com/marcofailli/soak-my-bed) Klipper plugin measures meshes in a loop while the bed heats, stores each probed matrix, and draws a GIF and a chart with matplotlib and SciPy when it ends. It reports the mean absolute change against the first and the previous mesh. This change builds the idea afresh. None of that plugin's code is used.

Relevant Klipper and Moonraker facts on the Max 4 (`01.01.07.01`):

- `bed_mesh.py` saves every `BED_MESH_CALIBRATE` result as a profile through `configfile.set`, which marks a config save pending. Removing the profile afterwards also leaves the save pending. Only the adaptive path, which sets the profile name to `None`, skips the save.
- QIDI renames Klipper's command to `_BED_MESH_CALIBRATE` and wraps it in an adaptive macro (`config/klipper-macros-qd/bed_mesh.cfg`) that also sets the touchscreen's print sub-status.
- A running command holds the G-code mutex until it finishes, so a mesh blocks other G-code commands, including a stop command, until it ends (`openspec/observations/qidi-platform.md`, Cancel, pause, and the G-code mutex).
- Moonraker registers every endpoint a Klipper module adds through `webhooks.register_endpoint` (`klippy_connection._request_endpoints`). A module endpoint is therefore callable from Fluidd and runs on Klipper's reactor without waiting for the G-code mutex.

## Goals / Non-Goals

**Goals:**
- Tell an owner how long their printer takes to reach printing temperature and to stop moving, at bed and chamber temperatures they choose.
- Update live, keep every run, and compare runs.
- Leave mesh profiles, pending config saves, the loaded mesh, and heaters as they were found.

**Non-Goals:**
- Changing print start, adding a soak wait to it, or feeding results into it.
- Replacing the print-start mesh, or saving a soak mesh for printing.
- Images or animations rendered on the printer, or new Python packages in Klipper's environment.
- Reading the nozzle's own thermal expansion. The nozzle is held at one temperature so it doesn't show up as bed movement.

## Decisions

### A Klipper module that runs one mesh at a time

`tltg_bed_soak` (`installer/klipper/tltg/bed_soak.py`, enabled from `bed_soak.cfg`) registers `TLTG_BED_SOAK` to start and `TLTG_BED_SOAK_STOP` to stop. Command names have no digits, because QIDI's G-code parser splits words at digits. Between meshes a reactor timer runs the next mesh with `run_script`, so the G-code queue is free between meshes and the test never holds it for longer than one mesh. Holding it for the whole test would block Fluidd and the touchscreen for an hour.

Rejected: one long G-code command for the whole test, which would block every other command until it ended.

### Meshes bypass QIDI's adaptive wrapper and skip the profile save

Each mesh runs Klipper's own calibrate command (`_BED_MESH_CALIBRATE` on QIDI, `BED_MESH_CALIBRATE` where it isn't renamed) over the configured `mesh_min` to `mesh_max` with the chosen probe count. While a test mesh runs, the module skips `ProfileManager.save_profile` for that mesh only and reads the probed matrix from `bed_mesh`. After the last mesh it clears the mesh and loads the profile that was loaded before, if any.

Rejected: saving a `soak` profile and removing it at the end, which leaves a config save pending and makes the touchscreen and Fluidd prompt for a restart.

### Stop works at once through an endpoint

The module also registers a `tltg_bed_soak/stop` webhook endpoint. Fluidd's Stop button calls it, and it turns off the heaters the test set immediately, without waiting for the mesh in progress. The test then ends when that mesh finishes. `TLTG_BED_SOAK_STOP` does the same from the console but waits its turn in the G-code queue.

### Setup matches print start

Before the cold mesh the test heats the nozzle to `start_extruder_probing_temp` and runs `G28`. It then wipes the nozzle at the chute (`OPTIMIZED_MOVE_TO_TRASH`, `OPTIMIZED_WIPE_NOZZLE_DROP`) and runs `Z_TILT_ADJUST`, as print start does. Last, it homes Z from the safe point (`_OPTIMIZED_HOME_Z_FROM_SAFE_POINT`), as `_OPTIMIZED_PREPARE_PRINT_MESH` does. Z tilt runs only this once. Running it again mid-test would re-level the gantry and reset the reference the bed's movement is measured against.

Print start runs Z tilt adjust at temperature, so it corrects the part of the shape change that is a left-to-right tilt. The test reports the tilt within the shape change and doesn't separate it.

### What is measured

For mesh *k* with probed matrix *Mₖ* and the cold mesh *M₀*:

- **Height** is the mean of *Mₖ − M₀*, the whole-bed shift.
- **Shape** is the mean absolute value of *Mₖ − M₀ − height*, the warping left after removing that shift.
- **Speed** is the median absolute value of *Mₖ − Mₖ₋₁* divided by the minutes between the two meshes. Dividing by time makes it compare across mesh sizes and printers. Using the median means one bad point can't hold it over the limit; on the test printer one retried point moved 42 µm while the median moved 1.7 µm.

Z is not homed again between meshes, so a whole-bed shift shows up as height. That is the movement a print would see after print start homes and meshes.

Temperatures are sampled every second in memory. The run file gets them once per mesh, so the printer writes once per mesh.

### Milestones match print start

- **Bed at temperature** is when the bed is first within 1 °C of target, as print start's bed wait.
- **Chamber at temperature** is when the chamber first reaches target minus 3 °C, print start's default chamber wait.
- **Stopped moving** is the start of the last stretch of three or more meshes with speed under the limit, counted after the bed reached temperature and lasting to the end.
- **Ready to print** is the later of chamber at temperature and stopped moving.

The chamber has stopped warming when it rises less than 0.5 °C in 10 minutes while below its wait temperature. From then the results show its highest temperature, and stopping no longer waits for it.

With stopping on, the test ends after three meshes under the limit, all taken after the bed and chamber reached temperature or the chamber stopped warming. Otherwise it ends at the time limit (default 90 minutes, at most 240).

The stopped-moving limit is 2 µm per minute. Back-to-back cold meshes on the test printer moved a median 1.1–1.7 µm over 2 minutes, under 1 µm per minute.

### Probe retries stop at the limit

QIDI's `ProbeSessionHelper.run_probe` (Max 4 and Plus 5) resets its retry count on the first retry, right after re-zeroing the load cell. As a result `samples_tolerance_retries` (10 in QIDI's config) never ends a probe, and a point whose samples never agree is probed forever while holding the G-code queue (`openspec/observations/qidi-platform.md`). The re-zero also runs while the nozzle is still pressed on the bed, and it reads the load cell under load (ZERO −2.064 to −2.075 against −2.14 to −2.19 in the air). In three retries during the test printer's meshes, the samples after it read 45–57 µm lower than the ones before, and those values went into the mesh. In a `PROBE` forced to retry with a tiny tolerance, they instead triggered in the air, each 3 mm (`sample_retract_dist`) above the last.

`tltg_klipper_fixes` replaces `run_probe`, guarded by the source hash like its other fixes. The count runs to the limit, after which the probe fails with upstream's `Probe samples exceed samples_tolerance`. Each retry re-zeroes the load cell after the retract lifts the nozzle off the bed, the way QIDI zeroes before every probe point.

Rejected: re-zeroing on the first retry only, at contact as QIDI does. With the count no longer reset, the forced test's later samples all triggered in the air until the limit failed the probe. After the change, the same forced test read the bed on every retry (ZERO −2.190) and failed at the limit.

This applies to every probe, so a print-start mesh or Z tilt adjust with such a point now fails and cancels the print instead of hanging. The bed soak test gets the same failure and ends as for any probe error.

Rejected: raising an error from a G-code output handler when the test counts too many retries, which only covers the test and relies on an exception escaping `respond_info`.

### Problem points come from the console

QIDI's `probe.py` reports every sample as `probe at X,Y is z=…` and every retry as `Probe samples exceed tolerance. Retrying...` through `respond_info`. A small always-loaded module, `tltg_probe_report` (`installer/klipper/tltg/probe_report.py`), registers a G-code output handler. It collects the samples and retries for each point while a bed mesh calibrate runs, delimited by wrapping the calibrate command. It publishes the retried points of the most recent mesh as `printer.tltg_probe_report`: when the mesh was measured, and each point's position, tries, and sample spread. Probing itself is unchanged. The bed soak module reads the same per-point record for each of its meshes.

A point is out of line when its change since the previous mesh differs from the median change of the points around it by more than 15 µm, about ten times the noise measured on the test printer. Comparing with neighbours rather than the whole bed keeps steady warping, which moves neighbouring points alike, from flagging the bed's edges.

Out-of-line points and jumps are looked for only between meshes that both started after the bed reached temperature. In the first full run on the test printer (bed 100 °C, chamber 50 °C, 5×5), the bed took 10 minutes to reach temperature. Meanwhile its centre domed upward about 150 µm in uneven steps, rising 58, 18, 70, and 25 µm at the check point across successive meshes, and its edges changed by up to 70 µm more or less than their neighbours. Checking those meshes flagged 20 points and found two jumps that were bed movement; correcting for them flattened the height curve. Retried points are flagged in every mesh.

**Check point and jumps.** Between meshes the test probes one check point, the bed centre, so each mesh has a check before and after it. A jump is a step: the check point's change during one mesh differs, in the same direction and by more than 15 µm, from what both the previous and the next mesh's check-point rates predict for that mesh's duration. Comparing with both neighbours keeps the fast but smooth movement after the heaters turn on from counting as a jump. A jump is therefore confirmed one mesh later. That mesh is then dropped, later meshes are shifted by the jump, and the run file gets a jump line. Probing the check point adds about 5 s to each 2-minute 5×5 mesh.

On the test printer, one probe at X195 Y105 that read 64 µm high was followed by every later probe reading 50–65 µm high. The shift survived a nozzle wipe, `G28`, Z tilt adjust, and a fresh Z home, so wiping and re-homing can't undo a jump. Its cause is unresolved.

### Live status stays small and the grids go to a file

`printer.tltg_bed_soak` reports the state and step, the settings, elapsed time, milestone times, the mesh count, the latest mesh's height, shape, and speed, an estimate of how far the current mesh has got (from the previous mesh's duration), the run file name, and the reason the test ended. Grids are not in the status. Moonraker sends every status change to every client, and its JSON writes are already its main cost on this printer.

Each run is one JSON Lines file in `~/printer_data/config/tltg_bed_soak/`, named from the start time and targets. It holds a settings line, one line per mesh with its matrix and the temperatures since the previous mesh, and a results line. Fluidd lists, downloads, and deletes the files through Moonraker's file API, and reads a run's grids when its mesh count changes. The config root is the one Moonraker lets Fluidd delete from. Uninstall keeps these files, since they are the owner's results.

### Fluidd card

The Fluidd fork adds a Bed soak card under Bed mesh on the Tune page, shown when `printer.tltg_bed_soak` exists. It has Test and Past runs tabs, following the reviewed mockups:

- Ready shows bed and chamber targets with the temperature presets, mesh size, stopping, time limit, and heaters after the test, plus warnings when the bed or chamber is still warm.
- Running shows four milestones, live temperatures, charts of temperature, height and shape, and speed against the limit, and a heat map of any mesh's change from the cold mesh with a button that shows it in the 3D Bed mesh view.
- Finished shows the times first, then a warning when points were flagged in more than one mesh or readings jumped.
- Problem points get a warning ring on the heat map and a list under it.
- Past runs shows a table and overlays of up to three runs.

The charts use one value axis each and share the time axis.

The fork's Bed mesh card marks the points in `printer.tltg_probe_report` on its 3D surface and lists them, when the loaded mesh is the one measured at that time. Bed mesh controls shows their count beside the active profile.

## Measurements

Test Max 4, 2026-11-10, bed 26 °C, chamber 25–26 °C, nozzle 140 °C, full bed (15 to 375 mm). The detailed numbers are in `openspec/observations/qidi-platform.md`.

| Step | Time |
| --- | --- |
| `G28` | 29–34 s |
| `Z_TILT_ADJUST` (already level, no retries) | 9.7 s |
| Z home from the safe point | 11.3 s |
| Chute wipe | 5 s |
| 5×5 mesh | 120–124 s |
| 9×9 mesh | 381 s |

5×5 is the default. It gives a point every 2 minutes; 9×9 gives one every 6½ minutes.

The first full run, from a cold printer to bed 100 °C and chamber 50 °C with a 5×5 mesh, took 2.2 minutes per mesh with the check point. The bed reached temperature after 10.1 minutes and the chamber after 11.9. The bed stopped moving at 16.1 minutes, 80 µm higher than cold on average with a 62 µm change of shape, mostly the centre doming, and the test ended itself after three meshes under the limit at 20.4 minutes. After the bed reached temperature, the check point moved 8, 8, 4, 2, and 1 µm per mesh.

## Risks / Trade-offs

- [A load-cell probe fails partway through a long test (`WEIGHT sensor error` has been seen during print start)] → the test ends, turns its heaters off, and keeps the meshes so far. A long run probes the bed thousands of times, so the failure rate decides whether one retry per mesh is worth adding.
- [Filament oozing from a 140 °C nozzle, debris, or a probe jump changes readings over an hour] → the check point catches jumps, and flagged points are shown. The cause of the jump seen on the test printer is still open (task 1.4).
- [The test's meshes heat the toolhead differently from print start] → the nozzle is held at print start's probing temperature, and the toolhead stays 5 mm above the hot bed only while probing, as in print start.
- [Someone moves the toolhead or changes a heater between meshes] → a changed heater target or a started print ends the test (spec). Moves between meshes are not blocked.

## Open Questions

- What caused the 50 µm jump on the test printer, and whether the plate at X195 Y105 had something on it.
- Whether the Plus 5's probe and chamber need different defaults once it is supported.
