# If something goes wrong

[← Back to the README](../README.md)

## The installer stopped

Read the installer's output first. It stops before writing anything when firmware detection, preflight, printer state, or free-space checks fail, and says why.

**`[Errno 13] Permission denied: '/home/qidi/printer_data/.tltg_optimized_installer.lock'`** or **`Could not validate previous package version.`** after an earlier run with `sudo`: versions before 26.11.02.1 ran as root when started with `sudo` and left the files they created owned by root, which a later run as `qidi` couldn't open. Install the current version, with or without `sudo`; it gives those files back to `qidi` before it starts.

**Klipper is stopped with an error,** such as `... is not a valid config section`: you can still update with the installer. It can't read or flash the boards without Klipper, so after waiting for Klipper it leaves the board firmware as it is, installs its files, restarts Klipper, and then updates saved settings. If Klipper still doesn't start, the installer says so; fix the error shown in Fluidd and run the installer again. A new install needs Klipper ready to read the boards first, and otherwise stops with `Valve Oil reads the THR, mainboard, and USB Boxes before a new install and could not read them all, so nothing was installed.`

**A new install was refused because of a board,** with `This release of Valve Oil doesn't support a board on this printer yet, so nothing was installed.`: open the prefilled support issue link the installer printed above that line, and submit it. See [Boards Valve Oil doesn't support yet](install.md#boards-valve-oil-doesnt-support-yet). If it says a Box `is listed in box.cfg but is not connected`, connect that Box, or remove it in the touchscreen's Box setup, and run the installer again.

**`A QIDI firmware update is in progress or did not finish; run ./install.sh to follow or finish it before anything else`:** run `~/tltg-optimized-macros/install.sh`. See [A QIDI firmware update stopped part way](#a-qidi-firmware-update-stopped-part-way).

## QIDI firmware updates

### The firmware update didn't start

The installer, the printer warning, and the **Valve Oil** section in Fluidd and Valve Oil UI say why, after `The firmware update did not start:`. Nothing on the printer changed, Valve Oil was installed for the firmware the printer has, and the update is tried again at the next install, or the next update check with automatic updates on.

| Reason | What to do |
|---|---|
| `a print is running or paused`, `a heater is set to a temperature`, or `Klipper didn't report whether the printer is idle` | Wait until the printer is idle, with every heater off, and Klipper is ready. |
| `The firmware update needs <N> MiB free in /home/qidi.` or `there isn't enough free space on the printer` | Free up space in `/home/qidi`, such as large G-code files, timelapse videos, or old `tltg-optimized-macros-before-…zip` backups in `~/printer_data/`. `df -h /home/qidi` shows the free space. On the Max 4 the update needs about 2.1 GB. |
| `The firmware package could not be downloaded from this project's mirror` or `the firmware download couldn't be reached` | Check that the printer is online and can reach `github.com`. A partial download resumes next time. |
| `The downloaded firmware package does not match its pinned size and SHA-256.` | Try again. The bad download is deleted. If it keeps happening, [report it](#reporting-a-problem). |
| `the update needs sudo with QIDI's default password, as automatic updates do` | The `qidi` password isn't QIDI's default, `qiditech`. The update runs without a terminal and can't ask for it. Set it back to `qiditech` with `passwd` while you update, or keep the firmware you have. |
| `a board's bootloader or firmware isn't supported yet`, or `the THR firmware can't be updated for QIDI firmware … by this release` | Valve Oil doesn't update the QIDI firmware on a printer whose THR or mainboard it can't flash. Report the board with the link in its printer warning. |
| What to run first, such as `a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first`, or `an earlier installer or firmware operation needs recovery first; run the Valve Oil installer to see what is needed` | Finish that first: see the board's [rescue](#a-board-flash-couldnt-be-verified), or [Restore a backup](#restore-a-backup). |
| `the printer's firmware is now …` | The QIDI firmware changed between the check and the start. Run the installer again. |

### A QIDI firmware update stopped part way

If the update stops after it has stopped the services, the touchscreen shows `The firmware update didn't finish. Don't turn the printer off. Open Fluidd on a computer to see what to do next.`, and Fluidd shows the error `A QIDI firmware update didn't finish.` with what to do. Continue it by running `~/tltg-optimized-macros/install.sh` over SSH, or with **Update now**, once nothing prints and no heater is on. It continues from the step where it stopped. It isn't continued by itself at startup, so the printer isn't stopped at every boot.

What it says, and what it left running:

- **`Installing QIDI firmware … failed`** or **`The board firmware could not be updated (…); no board was written.`**: Klipper and Moonraker were started again. Run `./install.sh` to try again.
- **`A board firmware write could not be verified`**: only Moonraker was started again, so Fluidd shows what happened, and the board may hold part of an image. See [A board flash couldn't be verified](#a-board-flash-couldnt-be-verified), then run `./install.sh` to finish the update. Until that rescue has run, `./install.sh` and **Update now** refuse to continue with `The firmware update was not continued: …`, which names the command, and stop nothing.
- **`The mainboard was not found on USB`**: check that the printer is fully powered, then run `./install.sh`.
- **`The mainboard still runs firmware …, which the Klipper in QIDI firmware … can't use`**: Klipper, Moonraker, and the touchscreen were started again. Update the mainboard from the touchscreen's firmware page, then run `./install.sh` to finish the update.
- **`QIDI firmware … and the board firmware are installed, but installing Valve Oil for it failed`**: everything was started again. Run `./install.sh` again.
- **`The firmware update stopped continuing at boot after 3 attempts`**: it was interrupted at startup three times in a row. Run `./install.sh` to continue it, and [report it](#reporting-a-problem) if it stops again.
- **`Not carried over:`** after a finished update, or **`The saved calibration could not be restored`**: the update finished, but that part of your saved calibration from before it isn't in the new `printer.cfg`. Copy what you need by hand from `config/printer.cfg` in the backup the message names, or calibrate again. Don't restore that whole backup with `./restore.sh`: it holds the old firmware's stock config files.
- **`This release can't continue the firmware update`**: another Valve Oil release started it. The message gives the command that installs that release, which finishes it.

The update's full log is `~/.cache/tltg-firmware-update/update.log`.

### A board flash couldn't be verified

When a board write can't be verified, the board may hold part of an image. Klipper and the touchscreen stay stopped, and every other installer command refuses to run until the board is rescued. **Don't turn the printer off.** Run the command the installer printed, from `~/tltg-optimized-macros`:

- Mainboard: `./mainboard-firmware.sh rescue`. See [If a flash fails](../firmware/MAINBOARD_FIRMWARE.md#if-a-flash-fails).
- USB Box: `./box-firmware.sh rescue --box N`. See [If a flash fails](../firmware/BOX_FIRMWARE.md#if-a-flash-fails).
- THR: follow the recovery in [THR-only operations](../firmware/FLASHING.md), using the recovery files the installer named.

During a QIDI firmware update, rescue runs only once the update has stopped with that failure. Then run `./install.sh` to finish the update.

### The touchscreen says "Extract failed"

That's expected. While Valve Oil is installed, a QIDI firmware update started from the touchscreen, from a USB drive, or from a `QD_Mandatory_Update.zip` file stops at its first step, before anything on the printer changes. See [QIDI's own firmware updates](install.md#qidis-own-firmware-updates). The touchscreen tries a `QD_Mandatory_Update.zip` again each time it scans the USB drive, so remove the file from the drive. Valve Oil installs QIDI's firmware updates itself once a release supports them.

To get QIDI's own updates back, [uninstall Valve Oil](install.md#uninstall), which puts QIDI's touchscreen app back.

### The closed-loop motor drivers weren't updated

On the Max 4, a QIDI firmware update only updates the closed-loop motor drivers when both report exactly the firmware and hardware Valve Oil expects. The update's result and the **Valve Oil** section say which driver didn't and what it reported. Update them from the touchscreen's firmware page, and if they should have been updated, use **Report this board** to open a support issue. See [Closed-loop motor drivers](firmware.md#closed-loop-motor-drivers).

### The closed-loop motor driver update failed

The update stopped with `The closed-loop motor driver update failed:` and why: a driver still didn't report the new firmware after three tries. QIDI's firmware wasn't installed, the other driver wasn't touched, and the printer was started again; a driver that failed may answer nothing, so Klipper may say it can't reach it. Run `./install.sh` again to retry, or update the motor drivers from the touchscreen's firmware page. If it keeps failing, open the support issue the result links, which the **Valve Oil** section also shows. If it also says Valve Oil couldn't reset the motor drivers' connection, turn the printer off and on before using it. Don't print until both drivers report their firmware again.

## The touchscreen says "Printer service failed to start"

QIDI's touchscreen shows `Printer service failed to start` (`QDC_400_005_015_001`) when it can't reach Moonraker for about 50 seconds, and keeps showing it after Moonraker is back. If Fluidd loads and shows the printer, Moonraker is running again: restart the touchscreen app from Fluidd's power menu (see [Restart the touchscreen app](fluidd.md#restart-the-touchscreen-app)), or reboot the printer. Install, update, and uninstall restart the touchscreen app whenever they restart Moonraker, so the message doesn't stay after them.

If Fluidd can't connect either, Moonraker isn't running. Over SSH, `systemctl status moonraker` shows why, and the end of `~/printer_data/logs/moonraker.log` usually names the error.

## The touchscreen says "Update failed. Please ensure the MCU is properly connected"

QIDI's touchscreen shows `Update failed. Please ensure the MCU is properly connected, then click "Restart Firmware" or retry the update.` (`QDC_100_005_000_001`) when Klipper reports `Can not update MCU 'mcu' config as it is shutdown`. Nothing was being flashed. Klipper restarted while a heater was on. The board running that heater turns it off and shuts down once Klipper stops updating it, and Klipper can't use the board again until a firmware restart. Press **Restart Firmware** on the touchscreen, or run `FIRMWARE_RESTART` from Fluidd's console.

Before 26.11.09.2, an automatic update could restart Klipper during a preheat, since it checked only for a running print. Updates now wait until every heater is off.

## The camera doesn't show in Fluidd

At boot, QIDI's `makerbase-webcam.service` writes the camera into `~/printer_data/config/crowsnest.conf`. It could run before the camera's device link existed and write an empty `device:` line instead, and then crowsnest, which streams the camera, doesn't start and Fluidd shows no camera. The package makes it wait for the camera, so the next reboot fills the line in again.

To fix it without rebooting, run `sudo systemctl restart makerbase-webcam` over SSH. The camera comes back about 30 seconds later. If it still doesn't, `systemctl status crowsnest` and the end of `~/printer_data/logs/crowsnest.log` show why.

## Restore a backup

Every install and uninstall saves a backup `.zip` of your configuration under `/home/qidi/printer_data/`, named `tltg-optimized-macros-before-optimize-...zip` or `tltg-optimized-macros-before-uninstall-...zip`.

Restore interactively over SSH:

```bash
cd ~/tltg-optimized-macros && ./restore.sh
```

Restore a specific backup:

```bash
cd ~/tltg-optimized-macros && ./restore.sh --backup /home/qidi/printer_data/<backup-name>.zip
```

If restore completed and the recovery sentinel is still present, clear it with:

```bash
cd ~/tltg-optimized-macros && ./install.sh --clear-recovery-sentinel
```

To remove everything instead, see [Uninstall](install.md#uninstall).

## Box link monitor

When a Box drops off its USB link with `Lost communication with MCU 'mcu_boxN'`, nothing records why by default. The Box link monitor does. It's **off by default**; turn it on from Fluidd's console:

```gcode
SAVE_VARIABLE VARIABLE=tltg_box_link_monitor VALUE=1
```

It writes `printer_data/logs/tltg_box_link.log`, separate from `klippy.log` so it survives log rotation. The log is capped at 2 MB and rotates once to `tltg_box_link.log.1`. Set the variable back to `0` to stop recording; this takes effect without a restart.

**When reporting a Box disconnect,** attach this file along with the end of `klippy.log`.

**To find out why a Box dropped:**

1. Turn the Box off and on before printing.
2. After a drop, use `RESTART` or restart the Klipper service, not `FIRMWARE_RESTART`, so the monitor reads the Box before Klipper restarts it.

### Reading the log

- **Context:** Box MCU serial counters and any change to them, Box heater, Box processor temperature, and print state, for up to four Boxes.
- **The first failure line** from `klippy.log`: `Got EOF when reading from device`, `Timeout with MCU`, or `shutdown:`, plus the kernel USB lines at that moment.
- **`serial-history mcu_boxN:`** lines, written at that first failure: the last messages Klipper sent to and received from each Box, with their times. They show how long the Box went quiet before it disconnected and what Klipper last asked it to do. A `serial-history clock` line records when they were saved, for lining them up with the kernel USB lines.
- **`reset-reason mcu_boxN`** lines: what each Box reports when Klipper connects to it, or right away if you turn the monitor on while Klipper is running. The Box reports the most serious reset since it was turned on, not the latest:
  - `PowerOn`: nothing but power-on, or a power dip, since the Box was turned on.
  - `Software`: Klipper restarted the Box with `FIRMWARE_RESTART`, and nothing more serious happened.
  - `IndependentWatchdog`: the Box's firmware stopped responding and its watchdog restarted it at least once since it was turned on.

  `FIRMWARE_RESTART` changes `PowerOn` to `Software`, but it can't hide `IndependentWatchdog`.

## Reporting a problem

Open a [GitHub issue](https://github.com/thelegendtubaguy/valve-oil/issues/new/choose) and pick the form that fits. Each form asks for the printer details below, and for the logs that show what happened.

For a board Valve Oil doesn't support yet, use the link in the installer's output or in the board's printer warning instead: it opens the support form with the printer details and the board's report already filled in.

### Printer details

SSH into the printer, paste this whole block, and copy everything it prints into the issue. It only reads files; it changes nothing and works whether or not Valve Oil is installed.

```bash
python3 - <<'PY'
import json, os, re, subprocess
def run(*cmd):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as exc: return f"unavailable ({exc.__class__.__name__})"
home = os.path.expanduser("~")
state = os.path.join(home, "printer_data/config/tltg_optimized_state.yaml")
try:
    text = open(state).read()
    print("Valve Oil:", re.search(r"^  version: *'?([0-9.]+)", text, re.M).group(1),
          "installed", re.search(r"^installed_at: *'?([^'\n]+)", text, re.M).group(1))
except Exception:
    print("Valve Oil: not installed")
try:
    for name, part in json.load(open("/home/qidi/update/firmware_manifest.json")).items():
        print(f"{name}: {part.get('version')} ({part.get('file')})")
except Exception as exc:
    print("Firmware manifest: unreadable", exc.__class__.__name__)
try:
    print("USB Boxes in box.cfg:", len(re.findall(r"^\[mcu mcu_box", open(os.path.join(home, "printer_data/config/box.cfg")).read(), re.M)))
except OSError:
    print("USB Boxes in box.cfg: no box.cfg")
print("Kernel:", run("uname", "-rv"))
print("Auto-updates:", "on" if os.path.isfile(os.path.join(home, "printer_data/.tltg_optimized_auto_update_enrolled")) else "off")
print("zram:", "on" if "zram" in open("/proc/swaps").read() else "off")
print("AI detection:", run("systemctl", "is-active", "algo_app.service") or "unknown")
print("THR fix backups:", "yes" if os.path.isdir(os.path.join(home, "printer_data/.tltg_thr_firmware_backups")) else "no")
try:
    import urllib.request
    q = "http://127.0.0.1:7125/printer/objects/query?print_stats=state&webhooks=state,state_message"
    s = json.load(urllib.request.urlopen(q, timeout=5))["result"]["status"]
    print("Klipper:", s["webhooks"]["state"], "/", s["print_stats"]["state"])
except Exception as exc:
    print("Klipper: no answer from Moonraker", exc.__class__.__name__)
PY
```

It prints the installed Valve Oil version, QIDI's firmware versions for every board, how many USB Boxes `box.cfg` lists, the kernel, whether automatic updates, zram, and QIDI AI detection are on, whether Valve Oil has saved toolhead board firmware backups, and Klipper's state.

### Logs

- **`klippy.log`:** in Fluidd, open **Configuration**, switch the file list to **logs**, and download `klippy.log`; or copy `~/printer_data/logs/klippy.log` over SSH. Attach it right after the problem, before restarting Klipper again, since each restart starts a new log. If it's too big to attach, zip it.
- **Installer output:** copy everything the installer printed, from the command to the end. To get more detail, run it again with `--debug` added, for example `~/tltg-optimized-macros/install.sh --debug`.
- **Automatic update output:** `journalctl -u tltg-optimized-auto-update.service --since "2 days ago"`.
- **QIDI firmware update output:** `~/.cache/tltg-firmware-update/update.log`, and `journalctl -u tltg-firmware-update.service --since "2 days ago"`.
- **Update now output:** `~/.cache/tltg-status/job.log`, the log **Show log** shows in Fluidd and Valve Oil UI.
- **`moonraker.log`:** `~/printer_data/logs/moonraker.log`, for Fluidd or file upload problems.
- **The sliced file**, for print problems: attach the `.gcode` or `.gcode.3mf`, zipped.
