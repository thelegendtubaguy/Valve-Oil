# Valve Oil's Klipper

[← Back to the documentation](README.md)

Valve Oil installs its own Klipper beside QIDI's. It is upstream Klipper v0.14 with QIDI's changes for these printers and Valve Oil's fixes, and it is off until you choose it. QIDI's Klipper stays installed and unchanged, and you can go back to it at any time.

It is new and hasn't been tested on every printer yet, so it stays off by default until it has.

## What changes

- Klipper's main loop does about half the work while printing, because v0.14 generates motor steps on separate threads. Klipper as a whole uses more processor time than before, spread over more cores.
- Motion is the same as with QIDI's Klipper to within a few microsteps, Z moves are identical, and the QIDI Box, load cell, closed-loop motors, lights and 3MF printing work as they do with QIDI's Klipper.
- Upstream fixes and features since QIDI's August 2024 Klipper come with it, among them input shaper calibration that sweeps as it shakes.
- `PROBE` reports `Result: at X,Y estimate contact at z=…` instead of `Result is z=…`, and `PROBE` and `PROBE_ACCURACY` give the height where the nozzle touches the bed, so their values read higher by the probe's `z_offset` (0.05 mm on the Max 4, 0.075 mm on the Plus 5, 0.11 mm on the Smart 5). Range and standard deviation are the same. Bed meshes, Z offsets and homing don't change.
- `BED_MESH_CALIBRATE` writes its table of probe points to `klippy.log` instead of the console.

## Which printers

It runs on the firmware whose QIDI modules were checked against it:

| Printer | QIDI firmware |
|---|---|
| QIDI Max 4 | `01.01.07.01` |
| QIDI Plus 5 | `01.01.02.01` |
| QIDI Smart 5 | `01.01.02.01` |

On other firmware it isn't installed, and QIDI's Klipper runs.

## Turning it on and off

In the [Valve Oil UI](valve-oil-ui.md), open **Settings**, then **Klipper**, and choose **Valve Oil's Klipper** or **QIDI's Klipper**. In Fluidd's console:

- `VO_KLIPPER` says which Klipper is chosen and which is running.
- `VO_KLIPPER USE=VALVE_OIL` switches to Valve Oil's Klipper.
- `VO_KLIPPER USE=QIDI` switches back to QIDI's.

Switching restarts Klipper, as `FIRMWARE_RESTART` does. It isn't possible during a print, or while Valve Oil is installing or updating. Your settings, saved variables, bed meshes and macros are shared by both, so nothing needs setting up again.

## If it doesn't start

Every time Klipper starts, QIDI's Klipper runs instead of Valve Oil's when:

- QIDI's firmware, or QIDI's modules in its Klipper, changed since Valve Oil's Klipper was installed, as after a QIDI firmware update from the touchscreen;
- a QIDI firmware update is in progress;
- Valve Oil's Klipper is incomplete or doesn't match the installed Valve Oil version;
- Valve Oil's Klipper didn't get ready in three starts in a row.

Valve Oil's Klipper stays chosen, and a printer warning says why QIDI's is running. Updating or reinstalling Valve Oil installs Valve Oil's Klipper for the new firmware when it is supported. To try again, choose Valve Oil's Klipper again; to stop trying, choose QIDI's.

Over SSH, deleting `~/printer_data/.vo_klipper.json` and restarting Klipper goes back to QIDI's Klipper:

```bash
rm ~/printer_data/.vo_klipper.json
curl -X POST "http://127.0.0.1:7125/machine/services/restart?service=klipper"
```

## Uninstalling

Uninstalling Valve Oil goes back to QIDI's Klipper and removes Valve Oil's. See [what the installer changes](under-the-hood.md#valve-oils-klipper) for the files involved.
