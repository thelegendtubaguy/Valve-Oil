# If something goes wrong

[← Back to the README](../README.md)

## The printer stops at the boot logo

If the touchscreen stays on the boot logo and the printer never joins the network, over Wi-Fi or Ethernet, see [Recovering a printer that won't boot](recovering-an-unbootable-printer.md). It gets the printer booting again over USB without wiping it, and saves its logs on the way. The cause found so far is QIDI's touchscreen **Format External Storage** button, which can replace the system's `/sbin` link on printers that came without the FAT formatting tools.

## The installer stopped

Read the installer's output first. It stops before writing anything when firmware detection, preflight, printer state, or free-space checks fail, and says why. Each run also saves a log in `~/printer_data/logs`, such as `valve-oil-install-20261117-091402.log`, which the installer names when it stops; attach it when you [report a problem](#reporting-a-problem). See [Logs](install.md#logs).

**`[Errno 13] Permission denied: '/home/qidi/printer_data/.vo_installer.lock'`** or **`Could not validate previous package version.`** after an earlier run with `sudo`: versions before 26.11.02.1 ran as root when started with `sudo` and left the files they created owned by root, which a later run as `qidi` couldn't open. Install the current version, with or without `sudo`; it gives those files back to `qidi` before it starts.

**Klipper is stopped with an error,** such as `... is not a valid config section`:

- **A new install** refuses to start and changes nothing, with `Klipper isn't ready (error: ...), so nothing was installed.` and Klipper's own first line of the error. Valve Oil isn't installed yet, so the error comes from the printer as it is. Fix it first, using the error Fluidd or the touchscreen shows, then run the installer again. It also refuses while Klipper is shut down, or still starting after a minute.
- **An update over an installed Valve Oil** may be what fixes the error, since Valve Oil's own files can cause one. The installer installs its Klipper files and restarts Klipper before it changes anything else. It can't read or flash the boards without Klipper, so it leaves the board firmware as it is. If Klipper starts, the install carries on as usual. If it doesn't, the installer puts its files back, changes nothing else, doesn't restart the printer, and stops with `Klipper still did not start with Valve Oil's files, so they were put back and nothing else was changed.` Fix the error shown in Fluidd and run the installer again.

Automatic updates and **Update now** don't install anything while Klipper isn't ready. The printer warning and Valve Oil UI's **Updates** screen say `Klipper isn't ready; fix the error Fluidd shows, or run the Valve Oil installer`.

**A new install was refused because of a board,** with `This release of Valve Oil doesn't support a board on this printer yet, so nothing was installed.`: open the prefilled support issue link the installer printed above that line, and submit it. See [Boards Valve Oil doesn't support yet](install.md#boards-valve-oil-doesnt-support-yet). If it says a Box `is listed in box.cfg but is not connected`, connect that Box, or remove it in the touchscreen's Box setup, and run the installer again.

**The install or uninstall was cut off,** by a power cut, the printer restarting, or the SSH session closing: run the same command again. It finishes what the stopped run started, from wherever it stopped. An uninstall after a new install was cut off says `An earlier install stopped before it finished`: run the installer again first, then uninstall. Closing the SSH session no longer stops a run; it carries on to the end and writes only its log. If the earlier run left system changes it couldn't roll back, the next run says so, leaves them as they are, keeps the record as `~/printer_data/.vo_system_journal.json.set-aside-<time>`, and goes on; attach that file when you [report a problem](#reporting-a-problem).

**`A QIDI firmware update is in progress or did not finish; run ./install.sh to follow or finish it before anything else`:** run `~/valve-oil/install.sh`. See [A QIDI firmware update stopped part way](#a-qidi-firmware-update-stopped-part-way).

## The USB installer

**The Valve Oil package isn't listed under Firmware → Offline Update:** the drive needs the `QD_Update` folder at its top level, as the zip has it, and must be formatted FAT32. Each printer lists only its own package, named `QD_MAX4_…` on the Max 4, `QD_PLUS5_…` on the Plus 5, and `S5_…` on the Smart 5.

**QIDI's app showed its own screen again after the package installed, and Valve Oil's installer never appeared:** the installer waits for the touchscreen app to restart and Klipper to be ready, for up to several minutes. If it doesn't appear, the drive has a log of why in `valve-oil/logs`, and the printer in `~/printer_data/logs`. QIDI's update folder was already put back before the installer started.

**The touchscreen says QIDI's update files are missing:** QIDI's Offline Update empties `/home/qidi/update` before it runs any package, and the drive had no copy of QIDI's files for this printer's firmware, so they couldn't be put back. Nothing else was changed. Download the QIDI firmware for your printer and version from QIDI and extract its files into `/home/qidi/update`, or run the Offline Update with QIDI's own package.

**Install stopped:** the screen names the step that didn't finish and why, and says what state the printer was left in: nothing changed, everything put back, or Valve Oil installed with that step left to run again. The log is on the drive as `valve-oil-install.log`. **Try again** runs the install again; **Quit** gives the touchscreen back to QIDI's app.

**The printer wasn't restarted:** after the install, the printer would have stopped at its boot logo if it had restarted, and the screen says what it found. Don't turn the printer off. Run `~/valve-oil/install.sh` over SSH, which repairs it, then tap **Try again**, or see [Recovering a printer that won't boot](recovering-an-unbootable-printer.md).

## QIDI firmware updates

### The firmware update didn't start

The installer, the printer warning, and the **Valve Oil** section in Fluidd and Valve Oil UI say why, after `The firmware update did not start:`. Nothing on the printer changed, Valve Oil was installed for the firmware the printer has, and the update is tried again at the next install, or the next update check with automatic updates on.

| Reason | What to do |
|---|---|
| `a print is running or paused`, `a heater is set to a temperature`, or `Klipper didn't report whether the printer is idle` | Wait until the printer is idle, with every heater off, and Klipper is ready. |
| `The firmware update needs <N> MiB free in /home/qidi.` or `there isn't enough free space on the printer` | The update already removed old logs and QIDI's leftover download. Free up more space in `/home/qidi`, such as large G-code files, timelapse videos, or old `vo-macros-before-…zip` backups in `~/printer_data/`. `df -h /home/qidi` shows the free space. On the Max 4 the update needs about 2.1 GB. |
| `The firmware package could not be downloaded from this project's mirror` or `the firmware download couldn't be reached` | Check that the printer is online and can reach `github.com`. A partial download resumes next time. |
| `The downloaded firmware package does not match its pinned size and SHA-256.` | Try again. The bad download is deleted. If it keeps happening, [report it](#reporting-a-problem). |
| `the update needs sudo with QIDI's default password, as automatic updates do` | The `qidi` password isn't QIDI's default, `qiditech`. The update runs without a terminal and can't ask for it. Set it back to `qiditech` with `passwd` while you update, or keep the firmware you have. |
| `a board's bootloader or firmware isn't supported yet`, or `the THR firmware can't be updated for QIDI firmware … by this release` | Valve Oil doesn't update the QIDI firmware on a printer whose THR or mainboard it can't flash. Report the board with the link in its printer warning. |
| What to run first, such as `a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first`, or `an earlier installer or firmware operation needs recovery first; run the Valve Oil installer to see what is needed` | Finish that first: see the board's [rescue](#a-board-flash-couldnt-be-verified), or [Restore a backup](#restore-a-backup). |
| `the printer's firmware is now …` | The QIDI firmware changed between the check and the start. Run the installer again. |

### A QIDI firmware update stopped part way

If the update stops after it has stopped the services, the touchscreen shows `The firmware update didn't finish. Don't turn the printer off. Open Fluidd on a computer to see what to do next.`, and Fluidd shows the error `A QIDI firmware update didn't finish.` with what to do. Continue it by running `~/valve-oil/install.sh` over SSH, or with **Update now**, once nothing prints and no heater is on. It continues from the step where it stopped. It isn't continued by itself at startup, so the printer isn't stopped at every boot.

What it says, and what it left running:

- **`Installing QIDI firmware … failed`** or **`The board firmware could not be updated (…); no board was written.`**: Klipper and Moonraker were started again. Run `./install.sh` to try again.
- **`A board firmware write could not be verified`**: only Moonraker was started again, so Fluidd shows what happened, and the board may hold part of an image. See [A board flash couldn't be verified](#a-board-flash-couldnt-be-verified), then run `./install.sh` to finish the update. Until that rescue has run, `./install.sh` and **Update now** refuse to continue with `The firmware update was not continued: …`, which names the command, and stop nothing.
- **`The mainboard was not found on USB`**: check that the printer is fully powered, then run `./install.sh`.
- **`The mainboard still runs firmware …, which the Klipper in QIDI firmware … can't use`**: Klipper, Moonraker, and the touchscreen were started again. Update the mainboard from the touchscreen's firmware page, then run `./install.sh` to finish the update.
- **`QIDI firmware … and the board firmware are installed, but installing Valve Oil for it failed`**: everything was started again. Run `./install.sh` again.
- **`The firmware update stopped continuing at boot after 3 attempts`**: it was interrupted at startup three times in a row. Run `./install.sh` to continue it, and [report it](#reporting-a-problem) if it stops again.
- **`Not carried over:`** after a finished update, or **`The saved calibration could not be restored`**: the update finished, but that part of your saved calibration from before it isn't in the new `printer.cfg`. Copy what you need by hand from `config/printer.cfg` in the backup the message names, or calibrate again. Don't restore that whole backup with `./restore.sh`: it holds the old firmware's stock config files.
- **`This release can't continue the firmware update`**: another Valve Oil release started it. The message gives the command that installs that release, which finishes it.

The update's full log is `~/.cache/valve-oil/firmware-update/update.log`. What QIDI's package printed while it installed, much of it in Chinese, is in `qidi-package.log` beside it; the update shows its last lines when the package fails.

### A board flash couldn't be verified

When a board write can't be verified, the board may hold part of an image. Klipper and the touchscreen stay stopped, and every other installer command refuses to run until the board is rescued. **Don't turn the printer off.** Run the command the installer printed, from `~/valve-oil`:

- Mainboard: `./mainboard-firmware.sh rescue`. See [If a flash fails](../firmware/MAINBOARD_FIRMWARE.md#if-a-flash-fails).
- USB Box: `./box-firmware.sh rescue --box N`. See [If a flash fails](../firmware/BOX_FIRMWARE.md#if-a-flash-fails).
- THR: nothing to run. Turn the printer off with its power switch, wait 10 seconds, and turn it on again: Valve Oil finishes the THR firmware as the printer starts, before Klipper does. `./install.sh` finishes it too. See [If a THR flash is interrupted](firmware.md#if-a-thr-flash-is-interrupted).

During a QIDI firmware update, rescue runs only once the update has stopped with that failure. Then run `./install.sh` to finish the update.

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

## Klipper says `Option 'serial' in section 'mcu' must be specified`

Klipper shows this, and the touchscreen shows it under `QDC_100_005_000_001`, when `~/printer_data/config/printer.cfg` is empty. Before 26.11.17.1, turning the printer off within about 30 seconds of a `SAVE_CONFIG`, such as the one at the end of input shaper calibration from the touchscreen, could leave it empty. QIDI's `SAVE_CONFIG` doesn't wait for the new file to reach storage, and Valve Oil's [storage repair](install.md#os-optimizations) means the printer no longer writes every file straight away.

A factory reset isn't needed. `SAVE_CONFIG` keeps the file it replaces as `printer-YYYYMMDD_HHMMSS.cfg`, but QIDI's touchscreen app deletes those files each time it starts, so after a restart the copy to use is the one in the newest backup from a Valve Oil install or update. Over SSH, put that `printer.cfg` back and run the installer again, which puts back its own changes to the file:

```bash
cd ~/printer_data && ls -t vo-macros-before-*.zip | head -1
unzip -p <that backup>.zip config/printer.cfg > config/printer.cfg
cd ~/valve-oil && ./install.sh
```

Then press **Restart Firmware** on the touchscreen, or run `FIRMWARE_RESTART` from Fluidd's console. Calibration saved since that install or update, such as the input shaper result `SAVE_CONFIG` was saving, isn't in the backup, so run it again.

## The toolhead is stuck in the filament cutter

Before 26.11.18.1, a cut with the nozzle below 135 °C, such as `OPTIMIZED_CUT_FILAMENT` run from Fluidd's macros or a cut while a print was paused, stopped with `Extrude below minimum temp` while the toolhead was pressed against the cutter. Homing doesn't move the toolhead off the cutter first, so after a restart homing Y can catch on it. Turn the motors off with `M84`, or turn the printer off, slide the toolhead away from the cutter by hand, then home. On the Max 4 and Plus 5 the cutter is at the front left, so slide the toolhead right; on the Smart 5 it's at the front right, so slide it left.

## The camera doesn't show in Fluidd

At boot, QIDI's `makerbase-webcam.service` writes the camera into `~/printer_data/config/crowsnest.conf`. It could run before the camera's device link existed and write an empty `device:` line instead, and then crowsnest, which streams the camera, doesn't start and Fluidd shows no camera. The package makes it wait for the camera, so the next reboot fills the line in again.

To fix it without rebooting, run `sudo systemctl restart makerbase-webcam` over SSH. The camera comes back about 30 seconds later. If it still doesn't, `systemctl status crowsnest` and the end of `~/printer_data/logs/crowsnest.log` show why.

## Restore a backup

Every install and uninstall saves a backup `.zip` of your configuration under `/home/qidi/printer_data/`, named `vo-macros-before-optimize-...zip` or `vo-macros-before-uninstall-...zip`.

Restore interactively over SSH:

```bash
cd ~/valve-oil && ./restore.sh
```

Restore a specific backup:

```bash
cd ~/valve-oil && ./restore.sh --backup /home/qidi/printer_data/<backup-name>.zip
```

If restore completed and the recovery sentinel is still present, clear it with:

```bash
cd ~/valve-oil && ./install.sh --clear-recovery-sentinel
```

To remove everything instead, see [Uninstall](install.md#uninstall).

## Box link monitor

When a Box drops off its USB link with `Lost communication with MCU 'mcu_boxN'`, nothing records why by default. The Box link monitor does. It's **off by default**; turn it on from Fluidd's console:

```gcode
SAVE_VARIABLE VARIABLE=vo_box_link_monitor VALUE=1
```

It writes `printer_data/logs/vo_box_link.log`, separate from `klippy.log` so it survives log rotation. The log is capped at 2 MB and rotates once to `vo_box_link.log.1`. Before 26.11.18.2 it was `tltg_box_link.log`. Set the variable back to `0` to stop recording; this takes effect without a restart.

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

## Diagnostics

**Diagnostics**, on the Diagnostics tab of Valve Oil UI's [System](valve-oil-ui.md#diagnostics) screen, checks the printer's `klippy.log` for faults Valve Oil knows, and says what each one means and what to do. It reads the newest part of the log, up to 48 MB, at the lowest priority, so it doesn't slow a print; it says how far back it reached. Pick how far back to look: since Klipper last started, the last 24 hours, or the whole log. **Download report** saves what it found, with the log lines that show it, to attach to an issue, and [Report a problem](#reporting-a-problem)'s support bundle carries the last report.

Each finding says whether it needs you:

- **Needs attention:** it happened, and the Valve Oil release installed now doesn't fix it.
- **Fixed by the release installed now:** it happened on an earlier release.
- **Valve Oil repaired it:** it happened, and Valve Oil repaired it each time before it failed anything.
- **For reference:** QIDI's error codes, with the lines before each.

Valve Oil also writes a `vo_box_watch:` line to `klippy.log` the moment one of the Box faults below happens, on every printer with a USB Box. To check a log someone sent, run `python3 scripts/check_klippy_log.py klippy.log` from a copy of this repository; it also takes a support bundle's zip.

### Box unload stopped inside a cancelled part

A filament change ran while the print was inside a part you'd cancelled, and Klipper dropped its moves, so the filament wasn't cut or pulled back and the [unload stopped](qidi-box.md#filament-caught-at-the-toolhead). Valve Oil 26.11.22.2 and later let a filament change's moves through a [cancelled part](qidi-box.md#filament-changes-inside-a-cancelled-part). On an earlier release, update Valve Oil.

### Box slot step clock

While printing, the Box feeds filament on its own whenever its buffer asks, timing the slot motor itself. When print start, or `MCB_AUTO_START`, turns that feeding back on while the Box is feeding, the Box keeps the slot motor's timing off from Klipper's, by up to 51 seconds, for the rest of the print. An unload's pull-back then runs that slot's motor late or in bursts, and the toolhead sensor never clears. Diagnostics names the slot and how many seconds it's off.

Valve Oil 26.11.22.2 and later line the slot motor's timing back up before each unload, so this needs nothing from you. On an earlier release, update Valve Oil; until then, restarting Klipper clears it until the next print start.

### The Box fed a slot during a Valve Oil move

The Box fed a slot, or switched its motor off, while Valve Oil was moving that slot itself: a [jog](qidi-box.md#moving-a-slot-by-hand), a spool turn, a spool tag read, or a Box load or unload move. The slot's filament may not be where either expected. Check the slot before its next load, and [report it](#reporting-a-problem) with the support bundle so it can be fixed.

### Box unload stopped with filament at the toolhead

An unload pulled the filament back and the toolhead filament sensor still detected it. Something held it: at the toolhead, in the Box, or a slot motor running late. If Diagnostics also shows a [slot step clock](#box-slot-step-clock) or a [cancelled part](#box-unload-stopped-inside-a-cancelled-part), that's the cause. Otherwise see [Filament caught at the toolhead](qidi-box.md#filament-caught-at-the-toolhead).

### QIDI error codes

Diagnostics lists every error code QIDI's Box code reported, `Code:QDE_004_008; Message:Extruder unloading failure.` for example, with the lines before it. The Box's codes:

| Code | Means |
|---|---|
| `QDE_004_001` | A slot didn't load to the Box hub; check the slot's filament and reload it |
| `QDE_004_003` | A slot didn't unload; unload it again |
| `QDE_004_005` | Load the slot named first |
| `QDE_004_006`, `QDE_004_024` | The filament didn't reach, or enter, the extruder |
| `QDE_004_008`, `QDE_004_009`, `QDE_004_025` | The extruder didn't unload; see [Box unload stopped with filament at the toolhead](#box-unload-stopped-with-filament-at-the-toolhead) |
| `QDE_004_013` | The Box detected filament wrapping around a spool |
| `QDE_004_016` | A slot ran out |
| `QDE_004_019` | Check that the PTFE tube isn't bent |
| `QDE_004_023` | Automatic reload after a runout failed |

## Reporting a problem

Open a [GitHub issue](https://github.com/thelegendtubaguy/valve-oil/issues/new/choose) and pick the form that fits. Each form asks for the printer details below, and for the logs that show what happened.

The quickest way is **Open an issue** under **Report a problem**, in the **Valve Oil** section of Fluidd's Settings or on Valve Oil UI's [Updates](valve-oil-ui.md#updates) screen. It opens the bug report form with the printer, its details, and how Valve Oil was installed already filled in, and downloads a support bundle, `valve-oil-support-<printer>-<time>.zip`, to drag into the form. The bundle holds `klippy.log` and `moonraker.log` with the last rotated copy of each, `vo_box_link.log`, the output of the last **Update now**, add-on change, and QIDI firmware update, the last [Diagnostics](#diagnostics) report, Valve Oil's update status, and the printer details. Each log is cut to its newest 16 MB. Logs can show the printer's network addresses, so look through the bundle before you attach it. It doesn't include what the installer printed when you ran it over SSH, so paste that in yourself.

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
state = os.path.join(home, "printer_data/config/vo_state.yaml")
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
print("Auto-updates:", "on" if os.path.isfile(os.path.join(home, "printer_data/.vo_auto_update_enrolled")) else "off")
print("zram:", "on" if "zram" in open("/proc/swaps").read() else "off")
print("AI detection:", run("systemctl", "is-active", "algo_app.service") or "unknown")
print("THR fix backups:", "yes" if os.path.isdir(os.path.join(home, "printer_data/.vo_thr_firmware_backups")) else "no")
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
- **Installer log:** each install, update, and uninstall saves `valve-oil-<run>-<date>.log` in `~/printer_data/logs`, in the same **logs** list as `klippy.log`. The USB installer also saves it on the drive as `valve-oil-install.log`.
- **Installer output:** copy everything the installer printed, from the command to the end. To see the log's detail as it runs, run it again with `--debug` added, for example `~/valve-oil/install.sh --debug`.
- **Automatic update output:** `journalctl -u vo-auto-update.service --since "2 days ago"`. An update from a release before 26.11.22.1 is logged under that release's name, `tltg-optimized-auto-update.service`.
- **QIDI firmware update output:** `~/.cache/valve-oil/firmware-update/update.log` and `qidi-package.log` beside it, and `journalctl -u vo-firmware-update.service --since "2 days ago"`.
- **Update now output:** `~/.cache/valve-oil/status/job.log`, the log **Show log** shows in Fluidd and Valve Oil UI.
- **`moonraker.log`:** `~/printer_data/logs/moonraker.log`, for Fluidd or file upload problems.
- **The sliced file**, for print problems: attach the `.gcode` or `.gcode.3mf`, zipped.
