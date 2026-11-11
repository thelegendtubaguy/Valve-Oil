# What the installer changes

[← Back to the README](../README.md)

For anyone who wants to know exactly what's touched on the printer. The rules behind every change:

- **Only recognized files are changed.** If a QIDI file doesn't match a version the installer knows, it's left alone and reported, and the feature that needed it is skipped.
- **Your changes win.** A setting you changed from QIDI's value is kept. A file you change after installing is kept and reported by uninstall.
- **Everything is backed up first.** See [Restore a backup](troubleshooting.md#restore-a-backup).
- **The printer must be idle** for any change that restarts Klipper or Moonraker.
- **The installer runs as `qidi`,** the user that owns `~/printer_data`, even when started with `sudo`, and uses `sudo` only for the steps that need it. Every run first gives any of its own files, everything named `tltg` in `~/printer_data`, `~/printer_data/config`, the home folder, and Klipper's `klippy/extras`, back to `qidi` if an earlier run as root left them owned by root. QIDI's own files keep their owners.
- **Each service restarts once.** Changes are made first, then each changed service restarts once at the end. After Moonraker restarts, the installer waits for it to answer, then restarts the touchscreen app, `qidi-client`, so it reconnects.

## Klipper configuration

| What | Change |
|---|---|
| `printer.cfg` | Adds `[include tltg-optimized-macros/*.cfg]`, and changes recognized QIDI values for homing speed and retraction, the [top speed](fixes.md#xy-motors-skipping-at-top-speed), Z-tilt and mesh travel speed, the Max 4's closed-loop homing timing, and a few others. On the Max 4 with firmware `01.01.06.04` or later, it also sets values QIDI changed in that firmware, which a QIDI update leaves as they were: the closed-loop motors' `query_cycle` from 5 to 10, and the chamber protection sensor's `max_temp` from 150 to 170. |
| `config/tltg-optimized-macros/` | The optimized macros. QIDI's own macros in `klipper-macros-qd/` stay in place, so files sliced with stock G-code print as before. `homing.cfg`, `nozzle_motion.cfg`, and `printer_profile.cfg` hold the printer's own homing, cutter, wiper, and chute moves and positions; the installer picks them for the printer it detects. |
| QIDI's `RESUME`, `CUT_FILAMENT_1`, `M106`, `M109.0`, `homing_override`, `_km_apply_print_offset`, and external spool sensor sections | Removed from QIDI's files and replaced by optimized versions. |
| `officiall_filas_list.cfg` | On the Max 4 with firmware `01.01.06.04` or later, renames QIDI's filament 25 from `PA-CF` to `PA6-CF`, as QIDI did in that firmware. |
| `saved_variables.cfg` | Adds missing defaults for [this project's settings](settings.md) and repairs missing QIDI Box tool mappings. Existing values are kept. |
| `config/qidi_filament_config` | A link to QIDI's filament files, for [editing them in Fluidd](qidi-box.md#editing-qidis-filament-list). Max 4 firmware `01.01.07.01` only. |
| QIDI's filament files, `~/QIDI_Client/resource/filament_config/filament-*.json` | Klipper adds the [Anycubic, Elegoo, and Bambu Lab filaments](qidi-box.md#anycubic-elegoo-and-bambu-lab-filaments) missing from them each time it starts. Nothing else in them changes. |

## Klipper and Moonraker source and services

| What | Change | Restarts |
|---|---|---|
| `klippy/extras/tltg_*.py` | Added modules for the Box link monitor, Box processor temperature, Box heater refresh, background mesh save, faster `SAVE_VARIABLE` and Box variable saves, cheap printer facts for the optimized macros, printer warnings for Fluidd's warning banner and the console, including a USB Box the installer can't flash yet, turning the [exhaust fan off](print-start.md#exhaust-fan-while-heating-the-chamber) before the chamber falls far enough behind to stop the print, [fixes from newer Klipper](fixes.md#fixes-from-newer-klipper), [motors turning on late](fixes.md#motors-turning-on-late), cheaper Klipper timer checks and status updates, Box load/unload speed, Box eject, unknown Box filaments, [Anycubic, Elegoo, and Bambu Lab spool tags](qidi-box.md#spool-tags) and their filaments, stuck Box loads, Box slot jogs, Box flow stop and stranded-filament recovery, [cutting with the nozzle cold](qidi-box.md#cutting-the-filament), [Spoolman spools for each slot](spoolman.md), the [RS-485 Box monitor](qidi-box.md#rs-485-boxes), the load-cell check, the [X/Y step-loss monitor](tools.md#xy-step-loss-monitor), the [sweeping resonance test](tools.md#input-shaper-calibration), the [bed soak test](tools.md#bed-soak-test) and the probe points each bed mesh retried, a [limit on probe retries](fixes.md#probe-points-retrying-forever), holding `SET_VELOCITY_LIMIT` to the [top speed](fixes.md#xy-motors-skipping-at-top-speed), cancelling [long pauses](print-start.md#long-pauses), and [Klipper's health check](fixes.md#klipper-slowing-down-during-long-box-prints), which also [logs late commands](fixes.md#motors-turning-on-late). | Klipper |
| `klippy/extras/homing.py` | On the Max 4, replaced with this project's version of QIDI's homing code, for the faster homing. QIDI's closed-loop homing checks stay in place. | Klipper |
| `klippy/extras/print_stats_manager.py` | Keeps the [real shutdown reason](fixes.md#the-real-error-instead-of-mcu-shutdown) on screen. | Klipper, once |
| `klippy/extras/box_config.py` | Lets Fluidd set Box heater targets, capped at 65 °C. | Klipper, once |
| Moonraker `file_manager.py` and `metadata.py` | The [3MF metadata cache and plate fix](fixes.md#multi-plate-3mf-files), and [the filaments a file uses](fixes.md#filaments-a-file-uses). | Moonraker, when the patch changes |
| Moonraker `spoolman.py` | Charges the new spool from the start of each filament change, keeps the Spoolman connection from dropping, and passes a spool picked in Fluidd to the [loaded slot](spoolman.md). | Moonraker, once |
| `~/tltg-moonraker-speedups`, `tltg-moonraker-speedups.pth` in `~/moonraker-env`, and `tltg-klipper-speedups.pth` in `~/klippy-env` | Adds `msgspec` and `uvloop`, which Moonraker uses when it can import them, to QIDI's Moonraker, and the same directory to QIDI's Klipper. Moonraker builds the JSON it sends to Fluidd and the touchscreen with `msgspec` and runs its event loop on `uvloop`, and Klipper encodes the status it sends Moonraker with `msgspec`, both using less CPU. Nothing in QIDI's `~/moonraker-env` or `~/klippy-env` is replaced. | Moonraker and Klipper, when the packages change |
| `/etc/systemd/system/{klipper,moonraker,qidi-client,nginx,crowsnest,algo_app}.service.d/tltg-priority.conf` | Sets each service's CPU priority and CPUs, so Klipper gets the CPU before Moonraker, and both before the touchscreen app. Klipper runs at nice -10 on CPUs 1 and 2, Moonraker at -5 on CPUs 0 and 3, and the touchscreen app at 5. nginx and the camera (crowsnest) run on CPU 0 at 1 and 2, and AI detection (`algo_app`) on CPU 3 at 19, as QIDI set them. The touchscreen app stays on CPU 0, where QIDI's start script puts it. See [Klipper sharing the CPU with the touchscreen](fixes.md#klipper-sharing-the-cpu-with-the-touchscreen). | Each service, once; AI detection picks it up the next time it starts |
| `/etc/systemd/system/qidi-tuning.service.d/tltg-tuning.conf` | Replaces QIDI's CPU tuning script, which ran again every 35 seconds, with one run at boot that pins the toolhead serial port's interrupt to CPU 2 and the USB controllers' to CPU 3. | QIDI's tuning service, once |
| `/etc/systemd/system/irqbalance.service.d/tltg-tuning.conf` | Starts QIDI's `irqbalance` as before, but tells it to leave those interrupts alone. Before, it moved them between the tuning script's runs. | `irqbalance`, once |
| `/etc/systemd/system/{moonraker,klipper}.service.d/tltg-restart-on-exit.conf` | Turns off `RemainAfterExit` in QIDI's Moonraker and Klipper services, so systemd starts them again 10 seconds after they stop without systemd stopping them. Before, they stayed down until a restart or reboot while still reported as running. | None |
| `/etc/systemd/system/makerbase-webcam.service.d/tltg-wait-for-camera.conf` | Makes QIDI's boot script that sets the camera for crowsnest wait up to 30 seconds for the camera's `index0` device link, and skips the script if none appears. Before, the script could run before the link existed and clear the camera from `crowsnest.conf`, leaving Fluidd without a camera. See [The camera doesn't show in Fluidd](troubleshooting.md#the-camera-doesnt-show-in-fluidd). | None; takes effect at the next boot |

## Fluidd and the web server

| What | Change |
|---|---|
| `~/tltg-fluidd` | This project's [Fluidd build](fluidd.md). Updates replace it whole. |
| `~/fluidd` | QIDI's Fluidd, left untouched as the fallback. |
| Fluidd's nginx configuration | Serves `~/tltg-fluidd` instead of `~/fluidd`, sending its ready-made `.gz` copies of scripts, styles, and images (`gzip_static`) instead of compressing them on each request, and adds a `/tltg-screen/` route for the Printer screen card and a `/tltg-addons/` route for the Add-ons settings. |
| `tltg-remote-screen` service | Streams the touchscreen to the [Printer screen](fluidd.md#printer-screen) card, sending a new image only when the screen changes. It only listens on the printer itself, and Moonraker checks every connection. It runs on CPU 0, away from Klipper's CPUs. |
| `tltg-addons` service | Lists, installs, and removes [add-ons](addons.md) for Fluidd's **Add-ons** settings by running `addons.sh`, and reads each add-on's status. It only listens on the printer itself, and Moonraker checks every request through the web server's `/tltg-addons/` route. It runs on CPU 0, and keeps its last change's log in `~/.cache/tltg-addons/`. |
| `~/printer_data/moonraker.asvc` | Adds `qidi-client`, so Fluidd can [restart the touchscreen app](fluidd.md#restart-the-touchscreen-app). Moonraker restarts. |

## System

| What | Change |
|---|---|
| `~/tltg-optimized-macros/` | The installer, `restore.sh`, `auto-update.sh`, `box-firmware.sh`, `mainboard-firmware.sh`, and `addons.sh`. |
| `~/tltg-addons/` and each add-on's service, such as `/etc/systemd/system/octoeverywhere.service` | Only for [add-ons](addons.md) you install. Each add-on has its own folder and a data folder for its settings. |
| USB QIDI Box firmware and `~/printer_data/.tltg_box_firmware_backups/` | Each install and automatic update flashes the [Valve Oil Box firmware](firmware.md#qidi-box-firmware) to every USB Box that needs it and keeps the Box's stock firmware in the backups folder. |
| `/etc/systemd/system/tltg-optimized-auto-update.{service,timer}` | Only if you turn on [automatic updates](install.md#automatic-updates). |
| `~/printer_data/logs/tltg_box_firmware_box<N>.json` | The shareable report for a USB Box whose bootloader or firmware the installer [doesn't support yet](firmware.md#qidi-box-firmware). Fluidd shows a banner while it's there. An install removes it once the Box has the Valve Oil firmware or leaves `box.cfg`. |
| `~/printer_data/config/tltg_bed_soak/` | [Bed soak test](tools.md#bed-soak-test) runs, one file each. Uninstall keeps them. |
| `~/printer_data/logs/tltg_rfid_*.json` | [Spool tag reports](qidi-box.md#spools-that-dont-read), written when a Box reads a tag it doesn't recognize or `TLTG_BOX_RFID_REPORT` runs. |
| `~/printer_data/logs/tltg_box485.log`, `tltg_box485_report.json`, and `~/printer_data/.tltg_box485_salt` | Written only when [RS-485 Box hardware](qidi-box.md#rs-485-boxes) is detected or `TLTG_RS_BOX_REPORT` runs. The salt keeps the codes that replace hardware ids the same on this printer. |
| DNS, package sources, `xl2tpd`, `bluetooth`, `usbdevice.service` ([ADB](install.md#adb)), `algo_app`, `rockchip.service`, touchscreen images | The [OS optimizations](install.md#os-optimizations), which every install and update applies; `algo_app` only if you turn off AI detection. |
| `/etc/default/cpufrequtils` | Sets QIDI's CPU frequency service to the `performance` governor instead of `ondemand`. Only with the OS optimizations. Uninstall removes the file, which puts `ondemand` back. |
| `/usr/local/lib/tltg-zram-swap/` and `/etc/systemd/system/tltg-zram-swap.service` | The [zram swap](install.md#zram-swap) kernel modules and the boot service that loads them and turns on swap, setting `vm.swappiness` to 80 and `vm.page-cluster` to 0. Only with the OS optimizations, and only on QIDI's kernel build. Stopping the service turns swap off and puts the kernel's defaults back. |

## What uninstall does

Uninstall removes the optimized macros and added modules, restores every QIDI file above, points the web server back at QIDI's Fluidd, removes `~/tltg-fluidd`, the `msgspec` and `uvloop` added for Moonraker and Klipper, the Printer screen and add-ons services, every installed add-on with its settings, the Moonraker and Klipper restart-on-exit settings, the service priorities and CPUs, the tuning and `irqbalance` changes, which brings QIDI's tuning script back, and the filament folder link, and restores Moonraker's service list. The Anycubic and Elegoo filaments stay in QIDI's filament list, since Box slots may use them. Values that QIDI's own configuration has for the printer's firmware, such as `query_cycle` 10, stay as they are. It asks before undoing the OS optimizations. Files you changed after installing are kept and listed.

Installs, updates, and automatic updates flash the [QIDI Box firmware](firmware.md#qidi-box-firmware). Other firmware changes only when you [ask for it](firmware.md). Uninstall doesn't change firmware. `./box-firmware.sh restore` puts QIDI's Box firmware back.
