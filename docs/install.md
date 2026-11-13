# Installing, updating, and uninstalling

[← Back to the README](../README.md)

## Install

SSH into your printer as `qidi@<your printer's ip>` and run:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/install-latest.sh)"
```

Running it with `sudo` works the same: the installer switches to `qidi` and asks for `sudo` itself when it needs it, so every file it writes belongs to `qidi` either way. The same goes for `restore.sh`, `auto-update.sh`, and `box-firmware.sh`.

The installer checks which printer it's on and its firmware (see [Supported printers](../README.md#supported-printers)), the printer's state, and free space before writing anything, and makes a backup of your configuration first.

Before it changes anything, it reads the bootloader and firmware of the toolhead board (THR), the mainboard, and each USB QIDI Box, without changing them, to check that this release supports them. It says `Reading the THR, mainboard, and USB Box firmware without changing it. Klipper and the touchscreen stop for a few minutes.` A new install reads every board. An update reads only the boards that aren't already on Valve Oil's firmware. A new install with a board this release doesn't support is refused; see [Boards Valve Oil doesn't support yet](#boards-valve-oil-doesnt-support-yet).

Along the way it asks about:

- **Updating the QIDI firmware**, only when the printer runs an older QIDI firmware this release updates from: `Update the firmware now?` If you answer yes, the update installs Valve Oil itself, as a `--yes` install does, without the questions below. See [QIDI firmware updates](#qidi-firmware-updates).
- **Disable QIDI AI detection**, only while it's on: turns off QIDI's `algo_app` service. When the installer has finished its system changes, it reminds you to also turn off Spaghetti Detection and Foreign Object Detection in the touchscreen's Settings -> Printing Options, so the screen matches.
- **QIDI Box setup**, when Box support is off or tool numbers don't match slot numbers.
- **Restarting Klipper** to apply the changes.
- **Automatic updates**, while they're off: `Would you like to turn on automatic updates?` See [Automatic updates](#automatic-updates).

When an install or update changes Klipper's Python code, which is QIDI's `homing.py` and this project's own `klippy/extras/tltg_*.py` modules, it restarts the Klipper service itself once the printer is idle, without asking, and waits for Klipper to be ready again. Klipper keeps Python code it has loaded until its process ends, so the Restart button and `RESTART` reload the configuration but not that code.

An install run with `--yes` and an automatic update have nobody to ask, so they always end by restarting the Klipper service once the printer is idle. If a print or a heater has started by then, they leave Klipper alone and say it was not restarted.

At the end, without asking, it puts [Valve Oil's board firmware](firmware.md) on each board that doesn't have it yet: the THR, the Max 4's mainboard, and each USB Box. Klipper and the touchscreen stop while it does, about three minutes for the mainboard and about four minutes per Box. Keep the printer and every Box powered on until the installer finishes. **After the THR is flashed, run input shaper calibration before bed leveling or printing.** On the Max 4 with firmware older than `01.01.07.01`, the THR and mainboard are flashed once the printer is on `01.01.07.01`.

While Valve Oil is installed, the touchscreen no longer installs QIDI's firmware updates; Valve Oil delivers them instead. See [QIDI's own firmware updates](#qidis-own-firmware-updates).

It applies the [OS optimizations](#os-optimizations) without asking. The system changes, including restarting Moonraker and the touchscreen, take a little over a minute on a first install; the installer names each one as it goes. When they need a reboot, the installer reboots the printer about 10 seconds after it finishes.

After installing, close every Fluidd tab and open Fluidd again.

### Boards Valve Oil doesn't support yet

Valve Oil only flashes board bootloader and firmware builds it recognizes. When it finds one it doesn't:

- **On a new install**, nothing is installed and no board is changed. The installer names the board, for example `THR: its bootloader is not supported yet (bootloader SHA-256 …). Nothing was installed and the board was not changed.`, and prints a link to a support issue with the printer's details and the board's report already filled in: `To get it supported, open this prefilled support issue: …`. Open it and submit it. A new install is also refused when a Box listed in the touchscreen's Box setup isn't connected (connect it, or remove it in the touchscreen's Box setup), or when the boards can't be read, for example because Klipper isn't ready; run the installer again once Klipper is ready and the printer is idle.
- **On an update** of an existing install, a THR or mainboard Valve Oil doesn't support keeps its firmware, and so does the other one of the two. Valve Oil updates everything else, doesn't update the QIDI firmware, and leaves QIDI's firmware updates to the touchscreen. A printer warning, `The THR can't get Valve Oil's firmware yet.` or `The mainboard can't get Valve Oil's firmware yet.`, links to the prefilled issue. Once a release supports the board, the next install or automatic update flashes it and switches QIDI's updates off.
- **A USB Box** Valve Oil doesn't support keeps its firmware, with the warning `Box N can't get the Valve Oil Box firmware yet.`, and the rest of the install goes ahead. See [QIDI Box firmware](firmware.md#qidi-box-firmware).

An install counts as new on a printer where Valve Oil isn't installed, including one where it was uninstalled. It also counts as new where an earlier Valve Oil release was installed without automatic updates and a QIDI firmware update has since removed its configuration.

### Dry run

To see what the installer would do without changing anything:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/install-latest.sh)" -- --dry-run
```

A dry run reads no board and stops nothing. When a [QIDI firmware update](#qidi-firmware-updates) is waiting, it says what the update would do and anything that would keep it from starting now, without downloading the package. If nothing would keep it from starting, that's all the dry run shows, since the update does the rest of the install itself.

### OS optimizations

Every install and update applies these, including on printers where an earlier install skipped them. The printer has little memory and CPU to spare, and each release is tested with them on. Uninstall asks whether to undo them:

- Adding `1.1.1.1` and `8.8.8.8` as fallback DNS servers.
- Pointing the printer's package sources at Debian's official servers.
- Turning off services the printer doesn't use: `xl2tpd`, `bluetooth`, and QIDI AI detection if you choose.
- Turning off [ADB](#adb), which gives anyone on your network a root shell on the printer without a password.
- Stopping a broken QIDI boot service from leaving the printer's storage in slow synchronous mode.
- Compressed swap in memory ([zram](#zram-swap)), so the printer doesn't run out of memory.
- Running the CPU at full speed, so Klipper's short bursts of work don't start at a third of it. With a 65 °C chamber, the processor ran about 1 °C warmer, 11 °C below where it starts slowing itself down.
- Still images instead of animations on the touchscreen.

When an install or update turns on zram swap, the CPU setting, the service priorities, or the storage repair, they take effect straight away, and the printer then reboots by itself, never during a print, to confirm they start at boot:

- An install reboots the printer about 10 seconds after it finishes, without asking, unless a print is running or paused or a heater is on. It checks again just before rebooting.
- An automatic update reboots only once Klipper has been idle for 10 minutes: no print running or paused, no command running, the toolhead still, and every heater, including the Box dryers, off. Until then, each hourly check tries again.

### Zram swap

The Max 4 has 486 MB of memory and no swap. QIDI's AI detection alone can hold about 190 MB during a print, and when memory runs out, Linux makes room by dropping program code that has to be read back from storage. Klipper then stalls for seconds at a time, and a stall during a print can end it with `MCU 'mcu' shutdown: Missed scheduling of next digital out event`, often right when a print is cancelled.

Zram gives Linux a compressed swap area in memory instead: 256 MB of swap that holds about 2.3 MB of idle data per MB of memory it uses. On a Max 4, with memory filled the way it was on a printer that hit this shutdown, Klipper's longest delay went from 405 ms to 4 ms, and the program that filled memory was no longer killed.

QIDI's kernel has no zram, so the package carries its own modules, built for the Max 4's kernel `5.10.160 #4 SMP PREEMPT Wed Sep 3 15:21:57 CST 2025`. The Plus 5's kernel, `5.10.160 #3 SMP PREEMPT Sat Aug 23 12:12:00 CST 2025`, is the same build with a different build date, so the modules load on it too. They load only on those two exact kernels; on any other, including after a QIDI update that changes it, the printer runs without zram until a package release supports that kernel. To check that zram is on, run `cat /proc/swaps` over SSH and look for `/dev/zram0`.

### ADB

QIDI's system image runs ADB (Android Debug Bridge) as `root` on TCP port 5555, on every network the printer joins, with no authentication. Anyone who can reach the printer can run commands on it as root, and changing the `qidi` password doesn't stop them, since ADB doesn't use it. It comes from `usbdevice.service`, a leftover from Rockchip's board image whose only function is ADB. Nothing QIDI ships uses it, and the USB port for flash drives is separate and keeps working.

The installer stops and disables `usbdevice.service`. To check that ADB is off, run `sudo ss -tlnp | grep 5555` over SSH; it should print nothing. If QIDI support asks for ADB, `sudo systemctl start usbdevice` turns it back on until the printer reboots or the next install or automatic update check turns it off again.

## Slicer start G-code

> [!TIP]
> In OrcaSlicer 2.4.0 or later, subscribe to the [OrcaCloud bundle](https://cloud.orcaslicer.com/b/49f26b9c2101), which covers the Max 4 and the Plus 5. Your slicer then gets updates by itself, with nothing to copy and paste.

Otherwise, copy the machine G-code into your slicer by hand from the folder for your slicer and printer:

| Slicer | QIDI Max 4 | QIDI Plus 5 |
|---|---|---|
| OrcaSlicer | [`orcaslicer_gcode/`](../orcaslicer_gcode/) | [`orcaslicer_gcode/plus5/`](../orcaslicer_gcode/plus5/) |
| QIDI Studio | [`qidistudio_gcode/`](../qidistudio_gcode/) | [`qidistudio_gcode/plus5/`](../qidistudio_gcode/plus5/) |

The two do the same thing; only their placeholder syntax differs. Files sliced with QIDI's stock G-code still print normally after installing, without the faster start.

## Updating

Run the install command again, or press **Update now** in the **Valve Oil** section of Fluidd's Settings or on Valve Oil UI's [Updates](valve-oil-ui.md#updates) screen. Updates keep your settings.

The installer won't install, update, or uninstall while a print is running or paused, or while any heater, including the chamber and the Box dryers, has a target. It says `Cannot continue while a heater is on.` Install, update, and uninstall restart Klipper, and Klipper restarted while a heater is on comes back with its mainboard shut down, which needs a firmware restart. Turn the heaters off and run it again.

### Seeing what's waiting

Fluidd's Settings page has a **Valve Oil** section, and Valve Oil UI has the same thing as its [Updates](valve-oil-ui.md#updates) screen. Both show the installed Valve Oil release, the printer's QIDI firmware, each board's firmware, and what's waiting to install, with the **Automatic updates** switch and **Update now**. While something is waiting, a printer warning in Fluidd and Valve Oil UI says so, such as `Valve Oil <version> is waiting.`, `QIDI firmware 01.01.07.01 is waiting.`, or `New firmware is waiting for the THR and Box 1.`, and says what installs it.

### QIDI firmware updates

Each Valve Oil release brings the printer to one QIDI firmware, the newest it supports:

| Printer | Valve Oil updates it to | From |
|---|---|---|
| QIDI Max 4 | `01.01.07.01` | `01.01.06.03`, `01.01.06.04`, `01.01.06.05` |
| QIDI Plus 5 | `01.01.02.01` | nothing older yet |

When you install or update Valve Oil on one of the older firmware versions, the installer says what it would do and asks first:

```
QIDI firmware 01.01.07.01 is available for this QIDI Max 4, which runs 01.01.06.05.
Valve Oil downloads QIDI's package (265 MB) from this project's mirror, installs it, updates the board firmware that needs it (THR, Mainboard), and installs Valve Oil for firmware 01.01.07.01.
It updates the closed-loop X/Y motor drivers first, one at a time, if they run the firmware QIDI's older package installed; otherwise they keep their firmware. The X and Y motors turn off, so the printer homes again before its next print.
The printer can't be used while it runs, usually 10 to 30 minutes. Don't turn the printer off. It continues on its own if this session disconnects, and after a power loss it continues when the printer starts.
Update the firmware now?
Type Y/YES to update it, or N/NO to install Valve Oil for the current firmware without updating it.
```

Answer **N** and Valve Oil installs for the firmware the printer has now; the firmware update stays waiting. `--yes` installs, **Update now**, and automatic updates don't ask.

**Before it starts,** the update checks that no print is running or paused, every heater target is 0, the printer has enough free space (about 2.1 GB in `/home/qidi` on the Max 4), the THR and mainboard are boards Valve Oil supports, no earlier installer or board operation needs recovery, and `sudo` accepts QIDI's default password (see below). It downloads QIDI's package only from this project's mirror ([Max 4](https://github.com/thelegendtubaguy/Qidi-Max4-Defaults/releases), [Plus 5](https://github.com/thelegendtubaguy/Qidi-Plus5-Defaults/releases)) and checks it against the size and SHA-256 the release names. A download that stops part way picks up where it stopped next time. If anything fails, it says `The firmware update did not start:` and why, followed by `Nothing was changed; it is tried again at the next install or update check.`, and Valve Oil installs for the current firmware as usual. That includes a mirror that can't be reached.

**While it runs,** the printer can't be used. The update runs as its own service on the printer, so it carries on if your SSH session ends, which QIDI's package usually causes when it restarts the network. In order, it:

1. Unpacks the package and checks that it's the printer's firmware.
2. On the Max 4, when the package changes their firmware, updates the [closed-loop X/Y motor drivers](firmware.md#closed-loop-motor-drivers) with QIDI's driver firmware, one at a time, after stopping the touchscreen and Moonraker (from here the touchscreen shows the message below) and turning the X and Y motors off. A driver that isn't exactly as expected keeps its firmware, and so does the other one; a driver that still doesn't take its firmware after three tries stops the update before QIDI's package is installed.
3. Stops the touchscreen, Klipper, and Moonraker. Fluidd can't reach the printer until Moonraker starts again. The touchscreen shows `Valve Oil is updating the printer's firmware. Don't turn the printer off.` A screen that had gone to sleep is turned on for it.
4. Saves a [backup](troubleshooting.md#restore-a-backup) of your configuration, then installs QIDI's system package. As a QIDI update from the touchscreen does, the package replaces the config folder with QIDI's stock files, keeping only `saved_variables.cfg`, `MCU_ID.cfg`, `timelapse.cfg`, `fluidd.cfg`, and `motion_accuracy_calibration.json`. Valve Oil then puts back your saved calibration: the `SAVE_CONFIG` block at the end of `printer.cfg`, with bed mesh profiles, input shaper results, PID tuning, and probe and endstop offsets, over QIDI's own values, as Klipper's `SAVE_CONFIG` would write it. A saved section the new firmware's config no longer has, or an option Valve Oil's install sets, isn't carried over; the result names each one. If QIDI's package resets `saved_variables.cfg` to its defaults, which it does when `saved_variables.cfg.bak` is missing, your saved variables are put back too. Other changes you made to the config folder, such as your own macro files or edits to `printer.cfg`, `moonraker.conf`, or `crowsnest.conf`, aren't put back; they're in that backup.
5. Flashes the THR and, on the Max 4, the mainboard, with the services still stopped.
6. Starts Klipper and Moonraker, installs Valve Oil for the new firmware, then starts the touchscreen.

Afterwards, home the printer and run input shaper calibration before bed leveling or printing.

USB Boxes aren't flashed during the update. QIDI's package resets the Box list in `box.cfg` until the touchscreen finds the Boxes again, and the next install, or the next update check with automatic updates on, flashes them.

**If the power goes off** during the update, it continues by itself when the printer starts again. If three starts in a row each stop part way, it stops trying at startup and leaves the printer running. Even so, keep the printer powered on: a board that loses power while it's being flashed may not come back by itself.

**Running `./install.sh` again** while an update is running follows it and shows its progress. If one stopped part way, it continues it, but only while nothing prints and no heater is on. If one finished after your session ended, it shows how it went. Until an update that stopped part way is finished, other installer commands stop with `A QIDI firmware update is in progress or did not finish; run ./install.sh to follow or finish it before anything else`. See [A QIDI firmware update stopped part way](troubleshooting.md#a-qidi-firmware-update-stopped-part-way).

**The `qidi` password.** The update runs without a terminal, so like automatic updates it uses `sudo` with QIDI's default password, `qiditech`. On a printer whose `qidi` password has been changed, the firmware update doesn't start: `the update needs sudo with QIDI's default password, as automatic updates do`.

**China-region printers.** The mirror holds QIDI's packages for its global (`NA`) region. On a printer that came with QIDI's China-region firmware, the update installs the `NA` package, including its network settings for the global region. Valve Oil doesn't handle China-region packages.

### QIDI's own firmware updates

While Valve Oil is installed, the touchscreen doesn't install QIDI's firmware packages, so a QIDI update can't remove Valve Oil or put the printer on a firmware Valve Oil doesn't support yet. QIDI's **New SOC firmware available** pop-up and the firmware page's online update no longer appear. An update started from a USB drive, or from a `QD_Mandatory_Update.zip` file, stops at its first step with `Extract failed`, before anything on the printer changes. The touchscreen's notices for a single board and its **Update** for the boards, such as the closed-loop motor drivers, still work.

This applies to the touchscreen apps Valve Oil has reviewed: Max 4 firmware `01.01.07.01` and Plus 5 firmware `01.01.02.01`. On the Max 4's older firmware, on a touchscreen app that has been replaced or changed, and on a printer whose THR or mainboard Valve Oil doesn't support yet, the touchscreen installs QIDI's updates as it does without Valve Oil. Uninstalling Valve Oil puts QIDI's touchscreen app back, and with it QIDI's updates.

### Automatic updates

Every install schedules Valve Oil's update check, 5 minutes after the printer starts or an install finishes, and then every hour. What it does depends on whether automatic updates are on:

- **Off:** it only looks. When a new Valve Oil release, a QIDI firmware update, or board firmware is waiting, the printer warning says `Automatic updates are off, so run the Valve Oil installer to install it.` It installs, stops, and flashes nothing. Install what's waiting by running the install command, or with **Update now**.
- **On:** it installs what's waiting by itself while the printer is idle: no print running or paused and every heater target 0. That includes a new Valve Oil release, the [QIDI firmware update](#qidi-firmware-updates), and Valve Oil's board firmware, such as for a USB Box connected since the last release. To read or flash a board, it stops Klipper and the touchscreen for a few minutes, and for longer to flash a Box or update the QIDI firmware. Each update runs the same checks as a normal install, applies the [OS optimizations](#os-optimizations), and reboots the printer when they need it, once Klipper has been idle for 10 minutes. Keep the printer and every Box powered on while an update runs. When something can't install yet, the warning says why: `Automatic updates are on, but it hasn't been installed yet:` followed by the reason, such as a print running or a heater on.

A board the update check tried and couldn't bring to Valve Oil's firmware isn't tried again by later checks until it reports another firmware version; the next install tries again.

Turn automatic updates on or off with the **Automatic updates** switch in Fluidd's **Valve Oil** settings or on Valve Oil UI's [Updates](valve-oil-ui.md#updates) screen, by answering the installer's question, or over SSH. It needs no password and takes effect at the next check. Printers that had automatic updates on with an earlier release keep them on.

```bash
~/tltg-optimized-macros/auto-update.sh --enable    # turn automatic updates on
~/tltg-optimized-macros/auto-update.sh --disable   # turn them off
~/tltg-optimized-macros/auto-update.sh --status    # whether they're on, the last check, and what's waiting
~/tltg-optimized-macros/auto-update.sh --run       # check now; installs only with automatic updates on
```

`--enable-systemd` and `--disable-systemd`, the names earlier releases used, still work. Automatic updates use `sudo` with QIDI's default password, `qiditech`, so on a printer whose `qidi` password has been changed they fail wherever they need `sudo`, which includes installing a release and the QIDI firmware update.

## Uninstall

If `~/tltg-optimized-macros/` is still on the printer:

```bash
~/tltg-optimized-macros/install.sh --uninstall --plain
```

Or fetch the latest installer and uninstall with it:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/install-latest.sh)" -- --uninstall
```

Uninstall always puts back everything an install changes in QIDI's own files: Fluidd goes back to QIDI's Fluidd, the Printer screen service is removed, QIDI's Moonraker and Klipper files are restored, and QIDI's touchscreen app is put back, so the touchscreen offers and installs QIDI's firmware updates again. It removes the update check and turns automatic updates off. It then asks whether to also undo the [OS optimizations](#os-optimizations); `--keep-system-optimizations` keeps them without asking. Files you changed after installing are left alone and listed.

Uninstall can't run while a [QIDI firmware update](#qidi-firmware-updates) is unfinished. It doesn't change firmware: the QIDI firmware and the board firmware stay as they are. To put QIDI's board firmware back, see [Board firmware](firmware.md). Installing again after an uninstall counts as a new install, which reads every board first.
