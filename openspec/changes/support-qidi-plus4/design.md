## Context

Evidence lives in `openspec/observations/qidi-platform.md` under QIDI Plus 4. In short:

- **Same as the Max 4 and Plus 5:**
  - CoreXY with open-loop TMC2240 X/Y and sensorless homing (as on the Plus 5), 600 mm/s and 20000 mm/s².
  - A QIDI Box driven by `multi_color_controller`, `box_extras`, and `box_stepper`, with `print_stats_manager` and `saved_variables.cfg` state.
  - Klipper, Moonraker, and Fluidd, with Moonraker on port 7125.
- **Different:**
  - Host: Armbian 22.05 on Debian 10, an MKS `mks-pi` board (RK3328, four Cortex-A53 cores, 976 MB) with the `mks` user and `/home/mks`. System `python3` 3.7.3, glibc 2.28, kernel `5.16.20-rockchip64`, not QIDI's `5.10.160`. Armbian's zram swap is already on. 21 GB free on a 28 GB eMMC. `mks` needs a password for `sudo`.
  - Firmware identity: no `/home/<user>/update/firmware_manifest.json`. `/root/xindi/version` (root only) reads `soc = V2.1.1`, `ui = V211`, `mcu = V0.12.0`. The SOC payload is a Debian package, `QIDI-X-Plus4` `1.0.1`, that replaces 23 Klipper files and 77 Moonraker files over the factory image.
  - Stock macros: one `gcode_macro.cfg` with `homing_override`, `PRINT_START`, `PRINT_END`, `CANCEL_PRINT`, `PAUSE`, `RESUME`, and the probe and Z-offset macros; `box1.cfg` with `T0`–`T15` and `UNLOAD_T0`–`UNLOAD_T15`. No `klipper-macros-qd/`.
  - Z: two lead screws with `[z_tilt]`, a `smart_effector` probe with `[qdprobe]`, and a sensorless Z-max endstop.
  - Klipper's virtualenv: Python 3.7. All compiled modules were built by Debian 10's GCC 8.3.0. `box_stepper.so` is not a build `tltg_box_feed_motion` knows.
  - Screen: a serial touchscreen with its own firmware image, driven by `/root/xindi/build/xindi` as root. No framebuffer UI.
  - Update behavior: QIDI's package replaces `printer.cfg` and `gcode_macro.cfg` and keeps timestamped copies, so Valve Oil's include line is lost on every QIDI update, as the Max 4's config wipe does.

## Goals / Non-Goals

**Goals:**

- One installer that admits Plus 4 `2.1.1` with its own baseline, paths, and user, and leaves Max 4, Plus 5, and Smart 5 behavior unchanged.
- The installer's code keeps targeting Python 3.9. The Plus 4 gets an interpreter that runs it, not a second code base.
- The Plus 4's optimized behavior is QIDI's Plus 4 macros with Valve Oil's printer-neutral improvements, under QIDI's Plus 4 macro names.

**Non-Goals:**

- Plus 4 firmware before `2.1.1`. Owners update first.
- Board firmware of any kind on the Plus 4: Box (its firmware line is `1.1.1`, not `02.03.01.21`), toolhead, mainboard, closed loop.
- The Printer screen card, touchscreen patches, boot logo, and boot animation. The Plus 4's screen is a serial display with its own firmware.
- zram modules, Rockchip service changes, and other OS optimizations measured on QIDI's `5.10.160` image. Plus 4 OS changes follow measurement on a Plus 4.
- Printers modified away from stock, such as Beacon probe conversions. Guarded patches already skip files that don't match stock; Beacon-aware behavior is a separate change.

## Decisions

**D1. Develop on a feature branch, and admit only after a hardware pass on a stock Plus 4.** As with the Plus 5, admission, the stock snapshot, patch variants, and the Plus 4 macro folder land together, because a firmware listed in `printers:` is installable. The project has no Plus 4, so the hardware pass needs owners: once the read-only tasks pass, a `prerelease` build lets Plus 4 owners run the dry run and then the install, and their reports go into the observations.

**D2. The baseline comes from the `2.1.1` SOC payload plus a stock printer.** The payload (SHA-256 `ef432bd7…`) gives `printer.cfg`, `gcode_macro.cfg`, `box*.cfg`, `moonraker.conf`, `officiall_filas_list.cfg`, the 23 Klipper and 77 Moonraker files, and the Box templates. `plr.cfg`, `time_update.cfg`, `timelapse.cfg`, `KAMP_Settings.cfg`, `KAMP/`, and the rest of Klipper and Moonraker come only from a stock printer, read once and recorded with SHA-256. The snapshot is laid out like `installer/stock/qidi-plus5-defaults/` under `installer/stock/qidi-plus4-defaults/firmwares/plus4-2.1.1/`. A mirror of QIDI's package, like `Qidi-Plus5-Defaults`, would back `target_firmware` and the restore sets.

**D3. Detect the Plus 4 by `/dev_info.txt`, and read its version through `sudo`.** The installer already asks for `sudo` when it needs it. The update package isn't left registered with `dpkg`, so the package can't identify a Plus 4. A Plus 4 is recognized when `/dev_info.txt`, which `mks` can read, holds `QIDI@x-plus_4` (the value QIDI's own commented-out `preinst` check expects) and `/home/mks/printer_data` exists. One printer has confirmed the file; a stock printer confirms it again in task 2.1. Its firmware is `soc` from `/root/xindi/version`, read with `sudo cat`, and its key is `plus4-2.1.1`, prefixed like the Smart 5's because QIDI numbers each printer on its own. Firmware detection gains this second source beside the firmware manifest; the manifest path is unchanged for the other printers. Unattended updates use `sudo` with the `mks` default password, `makerbase`, as Max 4 updates use `qiditech`, and a changed password stops them in the same way.

**D4. Paths and user come from the detected printer.** `RuntimePaths` today defaults to `/home/qidi/printer_data` and `/home/qidi/update/firmware_manifest.json`. Each printer entry gains `user` and `home` (`qidi` and `/home/qidi` for the existing three, `mks` and `/home/mks` for the Plus 4), and the defaults come from the detected entry. The `TLTG_OPTIMIZED_*` overrides keep working for tests. 44 installer files outside the tests name `/home/qidi`. Most belong to features that stay off on a Plus 4 (D7); the rest read the printer's home from the paths. A test runs the Plus 4 lifecycle against a fixture under `/home/mks` and fails on any write under `/home/qidi`.

**D5. The installer runs under a downloaded CPython 3.11 on the Plus 4 (proposed; maintainer to confirm).** The launchers already run `python3 -I -S installer/runtime/bootstrap.py`, which uses only the standard library and the bundled libraries. On a Plus 4, where `python3` is older than 3.9, the launcher downloads one pinned `python-build-standalone` `aarch64-unknown-linux-gnu` `install_only` archive, checks its SHA-256, unpacks it into `/home/mks/.valve-oil/python/`, and runs the same command with it. Those builds link an older glibc than Debian 10's 2.28. The archive is about 30 MB; the Plus 4 has 21 GB free. Uninstall removes it. Other printers never download it.

Alternatives considered:
- **Make the installer run on Python 3.7.** At least 25 runtime files use 3.8 or 3.9 features (assignment expressions, `str.removeprefix`, `zoneinfo`), and the bundled `rich` and `typing_extensions` need 3.8. This touches code every printer runs, pins old library versions for all of them, and holds every later change to 3.7, which has been end of life since 2023.
- **Install a newer Python with `apt`.** Debian 10 has no Python 3.9 package, and its archive has moved to `archive.debian.org`.

**D6. Klipper extras stay loadable under Python 3.7.** The extras run inside Klipper's virtualenv, which is 3.7 on the Plus 4. All 40 files under `installer/klipper/` parse with `feature_version=(3, 7)` today. A standalone check, run with the others under **Validation**, keeps it that way. Whether each extra works against QIDI's Plus 4 Klipper is decided per module (task 1.5).

**D7. Features tied to other hardware or images stay off on the Plus 4.** Gate on hardware facts where one exists, as for the Plus 5:
- Box, THR, mainboard, and closed-loop firmware: the Plus 4 has none of the admitted bootloaders or firmware lines, and the flashing tools refuse unrecognized builds; the Plus 4 entry also lists no board firmware.
- zram: no kernel build is declared for `5.16.20-rockchip64`, so no module loads.
- `tltg_box_feed_motion`: the Plus 4's `box_stepper.so` is not a known build, so it stays inactive.
- The step-loss monitor stays inert without `cl_interface`.
- `system_optimizations` applies to every printer today. It writes Debian 11 (`bullseye`) apt sources, disables services, installs Moonraker wheels built for Python 3.9, and patches files under `/home/qidi`. It gains a per-printer declaration, and the Plus 4 declares none at first: `bullseye` sources on a Debian 10 host would turn the next `apt upgrade` into a release upgrade, and the wheels don't match the Plus 4's Python 3.7 Moonraker.
- The Printer screen card, touchscreen app patches, and boot logo and animation are declared for the printers with QIDI's framebuffer screen, which the Plus 4 doesn't have.
- The project's Fluidd build (`v1.37.7-tltg-…`) is offered only once checked against the Plus 4's Moonraker, `v0.8.0-379` (API `1.4.0`); QIDI's Fluidd `v1.30.4` stays otherwise.

**D8. The Plus 4 gets its own macro set for what overrides QIDI macros.** The common tree installs together with the printer folder and shares macro names across printers. That holds for the Max 4, Plus 5, and Smart 5 because all three run `klipper-macros-qd`. On the Plus 4, the macros that wrap or replace those names have nothing to wrap. So:
- The common tree is split into printer-neutral files (the wrappers for the Klipper extras and helpers that call only Klipper and Valve Oil commands) and files that override `klipper-macros-qd` names. `inventory.md` classifies every file by the macros and objects it uses: 10 need a Plus 4 version, `step_loss_monitor.cfg` is not for the Plus 4, and the rest are printer-neutral or follow their extra. One would stop Klipper outright: `temperatures.cfg` reads an MCU named `THR`, which the Plus 4 doesn't have.
- `installer/klipper/printers/plus4/` holds Plus 4 versions under QIDI's Plus 4 names. They start from QIDI's `2.1.1` macros, with every coordinate, current, speed, and dwell QIDI's, and add only behavior already shipped on the other printers. First candidates: drop the `G4 P100` after each of the 16 `runout_N` writes in stock `PRINT_START` (1.6 s), and save the bed mesh without freezing Klipper (`tltg_background_save_config`). Z tilt and bed mesh travel stay at QIDI's 150 mm/s until homing and probing are measured on a Plus 4: its X/Y are open-loop, and the Plus 5 lost position at 565 mm/s. The exhaust-fan fix for the chamber heater doesn't apply: the Plus 4's `M106 P3` only sets `chamber_circulation_fan`.
- The include line goes after `[include box.cfg]`, the last QIDI include, so Valve Oil's definitions follow QIDI's, ahead of any `SAVE_CONFIG` block.

## Data to collect on a running stock Plus 4

All read-only. Results go into the Plus 4 observations.

| Question | How | Unblocks |
|---|---|---|
| Stock files not in the payload | `plr.cfg`, `time_update.cfg`, `timelapse.cfg`, `KAMP_Settings.cfg`, `KAMP/`, with SHA-256 | snapshot (D2) |
| Full Klipper and Moonraker trees against the payload | file list and SHA-256 of `~/klipper` and `~/moonraker` | source patches, extras (D6) |
| Host board, CPU, memory, kernel build | device tree, `/proc/cpuinfo`, `free`, `uname -a` | OS decisions, priorities |
| Services | `systemctl list-units`, unit files | D7, update flow |
| Klipper objects | `printer/objects/list` | cooling, Box, warnings |
| Box behavior with the Plus 4 `box_extras.so` and `box_stepper.so` | harness first, then a traced load and unload (`scripts/box_trace/`) | Box modules |
| Stock homing duration and reliability | timed `G28` repeated, `klippy.log` | homing |
| Cutter, wiper, chute, and park clearances | watch stock tool change, `MOVE_TO_TRASH`, and `CLEAR_NOZZLE` | macro folder |
| Probe repeatability | `PROBE_ACCURACY` | print start |
| Idle and printing host load and memory | the measurements used on the Max 4 | priorities, extras load |
| Moonraker version and API | `server/info`, sources against upstream | Fluidd build, Moonraker patches |

## Risks / Trade-offs

- **[No project Plus 4]** → Admission waits for owner reports on stock printers (D1). The dry run must be reliable enough to send to strangers first.
- **[A Plus 4 in LAN-only mode has no internet]** → QIDI's touchscreen app puts the Plus 4's LAN-only mode in force with `iptables` rules that drop every reply from outside the LAN, so the launcher's and the interpreter's downloads time out. Turning LAN mode off on the touchscreen restored a printer's internet access. The launcher checks for those rules before downloading and says to turn LAN mode off on the touchscreen for the install, or to copy the bundle and interpreter archive from a computer. The USB installer runs through QIDI's Max 4 and Plus 5 touchscreen app and doesn't apply.
- **[A downloaded interpreter is a new dependency]** → Pinned by SHA-256, used only on the Plus 4, removed on uninstall (D5). If it fails to run, the launcher stops before anything changes.
- **[Two macro sets to keep in step]** → Plus 4 files carry only behavior already shipped on the other printers, and contract tests cover their positions the way the Plus 5's chute window is covered.
- **[QIDI updates replace `printer.cfg` and `gcode_macro.cfg`]** → Same recovery as the Max 4: the next install or update restores the include line, and preflight reports the missing one.
- **[Extras written later with 3.8+ syntax]** → The 3.7 check (D6) fails the build.
