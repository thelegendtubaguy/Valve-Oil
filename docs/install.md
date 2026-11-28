# Installing, updating, and uninstalling

[← Back to the README](../README.md)

## Install

SSH into your printer as `qidi@<your printer's ip>` and run:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/install-latest.sh)"
```

Running it with `sudo` works the same: the installer switches to `qidi` and asks for `sudo` itself when it needs it, so every file it writes belongs to `qidi` either way. The same goes for `restore.sh`, `auto-update.sh`, and `box-firmware.sh`.

The installer checks which printer it's on and its firmware (see [Supported printers](../README.md#supported-printers)), the printer's state, and free space before writing anything, and makes a backup of your configuration first. On a [community-supported printer](community-printers.md), it says that releases may ship without being tested on it: over SSH before its questions and in its summary, and on the touchscreen beside the printer's name, with a screen of its own before a new install's questions. It needs Python 3.9 on the printer, which every supported printer has, and stops at once on an older Python.

A new install needs a printer that works on QIDI's firmware as it is: when Klipper isn't ready, such as stopped with a config error, the installer says so with Klipper's error and installs nothing. Fix the error first. See [Klipper is stopped with an error](troubleshooting.md#the-installer-stopped).

Before it changes anything, it reads the bootloader and firmware of the toolhead board (THR), the mainboard, and each USB QIDI Box, without changing them, to check that this release supports them. It reads them all at the same time, and says `Reading the THR, mainboard, and USB Box firmware without changing it. Klipper and the touchscreen stop for under a minute.` A new install reads every board. An update reads only the boards that aren't already on Valve Oil's firmware. A new install with a board this release doesn't support is refused; see [Boards Valve Oil doesn't support yet](#boards-valve-oil-doesnt-support-yet).

On a printer with an older QIDI firmware this release updates from, it updates the QIDI firmware first, without asking, and that update installs Valve Oil itself, as a `--yes` install does, without the questions below. See [QIDI firmware updates](#qidi-firmware-updates).

Before it changes anything, it asks every question that applies, shows a summary of your answers and of what it will do, and asks `Install now?`. Answer `N` and nothing is changed. After `Y`, it needs nothing from you until it finishes. It asks about:

- **Automatic updates**, while they're off. See [Automatic updates](#automatic-updates).
- **Turning off QIDI AI detection**, only while it's on: turns off QIDI's `algo_app` service. When the installer has finished its system changes, it reminds you to also turn off Spaghetti Detection and Foreign Object Detection in the touchscreen's Settings -> Printing Options, so the screen matches.

It doesn't ask about anything else:

- **QIDI Box support:** a new install that finds QIDI Boxes turns Box support on. Later installs and updates leave it as you set it, so switching to the external spool for prints started from Fluidd or a slicer stays your choice.
- **Tool-to-slot mappings:** a tool with no slot gets the slot with its number, and mappings you've set are left as they are.
- **Restarting Klipper:** every install ends by restarting Klipper once the printer is idle.
- **Rebooting:** when the [OS optimizations](#os-optimizations) need a reboot, the printer reboots about 5 seconds after the install finishes, once Klipper is ready.

The summary also lists the board firmware it will flash, whether it updates the QIDI firmware first, and, on a printer with configs copied in by hand from before the installer existed, that those are backed up and reset to QIDI's first.

### Install from a USB drive

You can install Valve Oil without SSH or a computer at the printer, from the touchscreen, with a USB drive:

1. Download `valve-oil-usb-<version>.zip` from the [latest release](https://github.com/thelegendtubaguy/valve-oil/releases/latest).
2. Extract it to the top folder of a USB drive formatted FAT32. The drive then has a `QD_Update` folder and a `valve-oil` folder. The same drive works on the Max 4, the Plus 5, and the Smart 5.
3. Plug the drive into the printer. On the touchscreen, open **Firmware → Offline Update** and choose the Valve Oil package. Each printer lists only its own.
4. QIDI's app installs the package and restarts. A few seconds later the touchscreen shows Valve Oil's installer, which starts, copies QIDI's system package back from the drive, and reads the printer's boards before it shows what it found. On the Smart 5 that takes about two minutes, and the screen says which of these it's doing.
5. Answer its questions, check the summary, and tap **Install**. Nothing changes before that tap, and **Quit** on any screen before it gives the touchscreen back to QIDI's app.
6. The install runs by itself and shows each step and the time so far. Keep the printer, and every QIDI Box, on.
7. When it's done, take the drive out. The printer restarts by itself. **Restart now** restarts it with the drive left in, which is harmless: the drive's package runs only when someone chooses it on the Offline Update page.

The installer saves its log on the drive as `valve-oil-install.log` before the finish screen or a failure screen shows, and on the printer in `~/printer_data/logs`. **Log** on any screen shows its last lines.

QIDI's Offline Update empties QIDI's update folder, `/home/qidi/update`, before it runs a package. The Valve Oil package puts QIDI's firmware manifest and board images back first, from copies on the drive for each QIDI firmware the release supports, checked by SHA-256, and the installer then puts back QIDI's system package from the drive, so the folder ends as QIDI left it whether you install or quit. On a QIDI firmware the drive has no copies for, the touchscreen says the folder was emptied and how to get QIDI's files back; nothing else changes.

On a printer with an older QIDI firmware this release updates from, the [QIDI firmware update](#qidi-firmware-updates) runs first, from QIDI's package on the drive, with no network needed. The drive carries each printer's QIDI package, which is why the download is about 600 MB.

Run on a printer that already has Valve Oil, the same drive offers:

- **Update**, with the install's current settings, when the drive has a newer release.
- **Change settings**, to answer the questions again.
- **Install again**, and **Restore a backup** of your configuration, when Valve Oil is up to date.

Uninstalling isn't on the drive; see [Uninstall](#uninstall).

### Logs

Every install, update, and uninstall saves a log in `~/printer_data/logs`, named for the run and when it started, such as `valve-oil-install-20261117-091402.log`, which you can download from Fluidd's **Logs**. It has what the installer found, decided, and did, in more detail than the installer shows. When a run fails, the installer names its log. The newest 10 logs are kept.

### The touchscreen at boot

From the moment the printer is switched on, the touchscreen shows the Valve Oil logo instead of QIDI's. A few seconds later an oil drop falls from the logo's canister and spreads along a line below it, again and again, until QIDI's touchscreen app starts. The logo is written into the printer's boot partition, which holds the image the printer shows before Linux starts; only that image and the checksum that covers it change, and QIDI's original is saved first. On a boot partition Valve Oil doesn't recognize, the installer says `Kept QIDI's boot logo:` and why, and the printer shows QIDI's logo until the animation starts. [Uninstall](#uninstall) puts QIDI's logo back.

### The touchscreen during an install

From the moment an install or update reads the boards' firmware, which stops Klipper, until it ends, the touchscreen shows Valve Oil's install screen and can't be used. Once the install starts, it shows the step it's on, such as **System changes** or **QIDI Box 1 firmware**, its place among the install's steps, the time so far, and whether the install was started over SSH, from a USB drive, or by automatic updates, with `Don't turn the printer off.` QIDI's touchscreen app starts again when the install ends, whether it worked or not. It isn't shown for `--dry-run`, while a print is running or paused or a heater is on, or when the touchscreen app isn't running. If the installer stops partway, for example because the SSH session closed, the touchscreen app still starts again. If a board's firmware write can't be verified, the touchscreen stays off and says the install didn't finish, until you run the rescue command the installer printed. A QIDI firmware update the install starts shows its own screen from when it stops the touchscreen, and starts the touchscreen app when it ends.

When an install or update changes Klipper's Python code, which is QIDI's `homing.py` and this project's own `klippy/extras/vo_*.py` and `vo_*.py` modules, it restarts the Klipper service itself once the printer is idle, without asking, and waits for Klipper to be ready again. Klipper keeps Python code it has loaded until its process ends, so the Restart button and `RESTART` reload the configuration but not that code.

Every install and automatic update ends by restarting the Klipper service once the printer is idle, without asking. If a print or a heater has started by then, it leaves Klipper alone and says it was not restarted.

At the end, without asking, it puts [Valve Oil's board firmware](firmware.md) on each board that doesn't have it yet: the THR, the Max 4's mainboard, and each USB Box. Klipper and the touchscreen stop while it does, about a minute for the mainboard and about a minute for the Boxes, which are flashed at the same time. Keep the printer and every Box powered on until the installer finishes. **After the THR is flashed, run input shaper calibration before bed leveling or printing.** On the Max 4 with firmware older than `01.01.07.01`, the THR and mainboard are flashed once the printer is on `01.01.07.01`.

While Valve Oil is installed, the touchscreen no longer offers QIDI's online firmware updates; Valve Oil delivers them instead. An update from a USB drive still works. See [QIDI's own firmware updates](#qidis-own-firmware-updates).

It applies the [OS optimizations](#os-optimizations) without asking. The system changes, including restarting Moonraker and the touchscreen, take a little over a minute on a first install; the installer names each one as it goes. When they need a reboot, the installer reboots the printer about 5 seconds after it finishes.

After installing, close every Fluidd tab and open Fluidd again.

### Boards Valve Oil doesn't support yet

Valve Oil only flashes board bootloader and firmware builds it recognizes. When it finds one it doesn't:

- **On a new install**, nothing is installed and no board is changed. The installer names the board, for example `THR: its bootloader is not supported yet (bootloader SHA-256 …). Nothing was installed and the board was not changed.`, and prints a link to a support issue with the printer's details and the board's report already filled in: `To get it supported, open this prefilled support issue: …`. Open it and submit it. A new install is also refused when a Box listed in the touchscreen's Box setup isn't connected (connect it, or remove it in the touchscreen's Box setup), or when the boards can't be read, for example because Klipper isn't ready; run the installer again once Klipper is ready and the printer is idle.
- **On an update** of an existing install, a THR or mainboard Valve Oil doesn't support keeps its firmware, and so does the other one of the two. Valve Oil updates everything else and doesn't update the QIDI firmware. QIDI's firmware updates stay switched off on the touchscreen, so a QIDI update can't move the printer to firmware Valve Oil doesn't support while the issue is open. A printer warning, `The THR can't get Valve Oil's firmware yet.` or `The mainboard can't get Valve Oil's firmware yet.`, links to the prefilled issue. Once a release supports the board, the next install or automatic update flashes it.
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

- Adding `1.1.1.1` and `8.8.8.8` as fallback DNS servers, on Max 4 firmware `01.01.06.03` only. From Max 4 `01.01.06.04`, and on the Plus 5, QIDI's firmware adds fallback DNS servers itself, so Valve Oil leaves DNS to it, and an update hands back DNS that an earlier install changed.
- Pointing the printer's package sources at Debian's official servers.
- Turning off services the printer doesn't use: `xl2tpd`, `bluetooth`, and QIDI AI detection if you choose.
- Taking [ADB](#adb) off the network, where it gives anyone a root shell on the printer without a password. It stays on the board's USB port, for recovery with a cable.
- Stopping a broken QIDI boot service from leaving the printer's storage in slow synchronous mode. Files then reach storage up to about 30 seconds after they are written, unless the program writing them waits for them. Klipper's `SAVE_CONFIG` and `SAVE_CONFIG_QD` wait for `printer.cfg` before Klipper restarts.
- Compressed swap in memory ([zram](#zram-swap)), so the printer doesn't run out of memory.
- Running the CPU at full speed, so Klipper's short bursts of work don't start at a third of it. With a 65 °C chamber, the processor ran about 1 °C warmer, 11 °C below where it starts slowing itself down.
- Still images instead of animations on the touchscreen.

When an install or update turns on zram swap, the CPU setting, the service priorities, or the storage repair, they take effect straight away, and the printer then reboots by itself, never during a print, to confirm they start at boot:

- An install reboots the printer about 5 seconds after it finishes, without asking, unless a print is running or paused or a heater is on. It checks again just before rebooting, and waits up to 5 minutes for a printer whose Klipper is still starting.
- No reboot follows a run that left Klipper not ready, such as stopped with an error or shut down, or still waiting to restart onto the installed files. The installer says `The printer was not restarted:` and why, and the reboot waits for the next install that finishes. A reboot you approve during an uninstall doesn't wait for Klipper.
- An automatic update reboots only once Klipper has been idle for 10 minutes: no print running or paused, no command running, the toolhead still, and every heater, including the Box dryers, off. Until then, each hourly check tries again.

### The files the printer starts from

QIDI's system image keeps its programs in `/usr`, and `/sbin`, `/bin`, and `/lib` are links into it. At power-on the kernel starts `/sbin/init`, which leads to `systemd`, and `systemd` starts everything else, from the network and SSH to Klipper and the touchscreen app.

QIDI's touchscreen app can break this. When you use **Format External Storage** on a printer that doesn't have the `mkfs.fat` formatting tool, the app unpacks its own copy of the `dosfstools` package over the whole system with `dpkg-deb -x`. That replaces the `/sbin` link with a folder holding only the formatting tools. The printer keeps running, but the next time it starts it stops at the boot logo, with no network, SSH, or ADB. Some printers ship with `dosfstools` installed and never hit this; others don't. If yours is already stuck at the boot logo, see [Recovering a printer that won't boot](recovering-an-unbootable-printer.md).

Every install and update checks these files before it changes anything else:

- When a folder has replaced `/sbin`, `/bin`, or `/lib`, the installer copies the folder's files into `/usr` without replacing any there, moves the folder aside as `/sbin.valve-oil-<date>`, and puts the link back. It says `Repaired /sbin` in its log.
- When the files are broken in a way it can't repair, such as a link that points somewhere else, it installs nothing and says what it found. Don't restart or switch off the printer then; follow [Recovering a printer that won't boot](recovering-an-unbootable-printer.md).
- When `mkfs.fat` is missing, or dpkg doesn't list `dosfstools` as installed (as after a repair), and the touchscreen app carries QIDI's `dosfstools` package, the installer installs that package properly with `dpkg`, so the app finds its tool and never unpacks it again.

A dry run says what it would repair or install. Uninstall undoes neither: the repaired links are how the system should be, and removing `dosfstools` would let the touchscreen break `/sbin` again.

No reboot that Valve Oil starts goes ahead while `/sbin/init` doesn't lead to `systemd`, whether it's after an install, an automatic update, a QIDI firmware update, the USB installer, or an uninstall. It says `The printer was not restarted, because it wouldn't start again:` and what it found; running the installer repairs it.

### Zram swap

The Max 4 has 486 MB of memory and no swap. QIDI's AI detection alone can hold about 190 MB during a print, and when memory runs out, Linux makes room by dropping program code that has to be read back from storage. Klipper then stalls for seconds at a time, and a stall during a print can end it with `MCU 'mcu' shutdown: Missed scheduling of next digital out event`, often right when a print is cancelled.

Zram gives Linux a compressed swap area in memory instead: 256 MB of swap that holds about 2.3 MB of idle data per MB of memory it uses. On a Max 4, with memory filled the way it was on a printer that hit this shutdown, Klipper's longest delay went from 405 ms to 4 ms, and the program that filled memory was no longer killed.

QIDI's kernel has no zram, so the package carries its own modules, built for the Max 4's kernel `5.10.160 #4 SMP PREEMPT Wed Sep 3 15:21:57 CST 2025`. The Plus 5's kernel, `5.10.160 #3 SMP PREEMPT Sat Aug 23 12:12:00 CST 2025`, is the same build with a different build date, so the modules load on it too. The Smart 5's kernel, `5.10.160 #15 SMP PREEMPT Fri Apr 3 15:46:16 CST 2026`, is the Plus 5's build with a different build date, so they load on it too. The modules load only on those three exact kernels; on any other, including after a QIDI update that changes it, the printer runs without zram until a package release supports that kernel. To check that zram is on, run `cat /proc/swaps` over SSH and look for `/dev/zram0`.

### ADB

QIDI's system image runs ADB (Android Debug Bridge) as `root`, with no authentication, on the USB-C port of the board inside the printer and on TCP port 5555 of every network the printer joins. Over the network, anyone who can reach the printer can run commands on it as root, and changing the `qidi` password doesn't stop them, since ADB doesn't use it. It comes from `usbdevice.service`, Rockchip's USB gadget service, which reads the port from `/etc/profile.d/adbd.sh`.

The installer adds `/etc/profile.d/valve-oil-adb-usb-only.sh`, which unsets that port, and restarts ADB if it's running, so ADB no longer listens on the network. ADB over the board's USB-C port keeps working: it's a way back onto a printer that has lost its network. Plug a cable from that port into a computer and run `adb shell`. A Mac's USB-C port may not see the board through a USB-C to USB-C cable; use a USB-A port, or a USB-C to USB-A adapter. Earlier versions turned ADB off entirely; an update turns it back on once the network port is closed.

To check that ADB is off the network, run `sudo ss -tlnp | grep 5555` over SSH; it should print nothing.

## Slicer start G-code

> [!TIP]
> In OrcaSlicer 2.4.0 or later, subscribe to the [OrcaCloud bundle](https://cloud.orcaslicer.com/b/49f26b9c2101), which covers the Max 4 and the Plus 5. Your slicer then gets updates by itself, with nothing to copy and paste.

Otherwise, copy the machine G-code into your slicer by hand from the folder for your slicer and printer:

| Slicer | QIDI Max 4 | QIDI Plus 5 | QIDI Smart 5 |
|---|---|---|---|
| OrcaSlicer | [`orcaslicer_gcode/max4/`](../orcaslicer_gcode/max4/) | [`orcaslicer_gcode/plus5/`](../orcaslicer_gcode/plus5/) | [`orcaslicer_gcode/smart5/`](../orcaslicer_gcode/smart5/) |
| QIDI Studio | [`qidistudio_gcode/max4/`](../qidistudio_gcode/max4/) | [`qidistudio_gcode/plus5/`](../qidistudio_gcode/plus5/) | [`qidistudio_gcode/smart5/`](../qidistudio_gcode/smart5/) |

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
| QIDI Max 4 | `01.01.07.02` | `01.01.06.03`, `01.01.06.04`, `01.01.06.05`, `01.01.07.01` |
| QIDI Plus 5 | `01.01.02.02` | `01.01.02.01` |
| QIDI Smart 5 | `01.01.02.01` | nothing older yet |

When you install or update Valve Oil on one of the older firmware versions, the installer updates the firmware without asking, so every printer runs the same QIDI firmware. It first says what it does:

```
QIDI firmware 01.01.07.02 is available for this QIDI Max 4, which runs 01.01.06.05.
Valve Oil downloads QIDI's package (262 MB) from this project's mirror, installs it, updates the board firmware that needs it (THR, Mainboard), and installs Valve Oil for firmware 01.01.07.02.
It updates the closed-loop X/Y motor drivers first, one at a time, if they run the firmware QIDI's older package installed; otherwise they keep their firmware. The X and Y motors turn off, so the printer homes again before its next print.
The printer can't be used while it runs, usually 10 to 30 minutes. Don't turn the printer off. It continues on its own if this session disconnects, and after a power loss it continues when the printer starts.
```

Interactive installs, `--yes` installs, **Update now**, and automatic updates all update it the same way.

**Before it starts,** the update checks that no print is running or paused, every heater target is 0, the printer has enough free space (about 2.1 GB in `/home/qidi` on the Max 4), the THR and mainboard are boards Valve Oil supports, no earlier installer or board operation needs recovery, and `sudo` accepts QIDI's default password (see below). It downloads QIDI's package only from this project's mirror ([Max 4](https://github.com/thelegendtubaguy/Qidi-Max4-Defaults/releases), [Plus 5](https://github.com/thelegendtubaguy/Qidi-Plus5-Defaults/releases), [Smart 5](https://github.com/thelegendtubaguy/Qidi-Smart5-Defaults/releases)) and checks it against the size and SHA-256 the release names. A download that stops part way picks up where it stopped next time. If anything fails, it says `The firmware update did not start:` and why, followed by `Nothing was changed; it is tried again at the next install or update check.`, and Valve Oil installs for the current firmware as usual. That includes a mirror that can't be reached.

**When free space is short,** the update first removes files that aren't yours and that the printer doesn't need, then checks again: the system journal beyond 50 MB, apt's download cache, `~/download/online_update.zip` left by the touchscreen's online update, and rotated logs in `~/printer_data/logs`, such as `klippy.log.2026-11-08`, more than two days old. It says how much it freed. Your prints, timelapses, current logs, and QIDI's `~/update` folder are left alone. If that isn't enough, see [The firmware update didn't start](troubleshooting.md#the-firmware-update-didnt-start).

**While it runs,** the printer can't be used. The update runs as its own service on the printer, so it carries on if your SSH session ends, which QIDI's package usually causes when it restarts the network. In order, it:

1. Unpacks the package and checks that it's the printer's firmware.
2. On the Max 4, when the package changes their firmware, updates the [closed-loop X/Y motor drivers](firmware.md#closed-loop-motor-drivers) with QIDI's driver firmware, one at a time, after stopping the touchscreen and Moonraker (from here the touchscreen shows the message below) and turning the X and Y motors off. A driver that isn't exactly as expected keeps its firmware, and so does the other one; a driver that still doesn't take its firmware after three tries stops the update before QIDI's package is installed.
3. Stops the touchscreen, Klipper, and Moonraker. Fluidd can't reach the printer until Moonraker starts again. The touchscreen shows Valve Oil's **Updating Firmware** screen, which says `Don't turn the printer off.` A screen that had gone to sleep is turned on for it.
4. Saves a [backup](troubleshooting.md#restore-a-backup) of your configuration, then installs QIDI's system package. As a QIDI update from the touchscreen does, the package replaces the config folder with QIDI's stock files, keeping only `saved_variables.cfg`, `MCU_ID.cfg`, `timelapse.cfg`, `fluidd.cfg`, and `motion_accuracy_calibration.json`. Valve Oil then puts back your saved calibration: the `SAVE_CONFIG` block at the end of `printer.cfg`, with bed mesh profiles, input shaper results, PID tuning, and probe and endstop offsets, over QIDI's own values, as Klipper's `SAVE_CONFIG` would write it. A saved section the new firmware's config no longer has, or an option Valve Oil's install sets, isn't carried over; the result names each one. If QIDI's package resets `saved_variables.cfg` to its defaults, which it does when `saved_variables.cfg.bak` is missing, your saved variables are put back too. When `saved_variables.cfg.bak` is there, QIDI's package instead adds the variables your file lacks from it without their quotes, as `last_load_slot = slot16` or `filepath = `, which stops Klipper from starting; Valve Oil writes each such value back quoted, as `last_load_slot = 'slot16'` or `filepath = ''`, leaves every other line as it is, and the result names the variables it repaired. Other changes you made to the config folder, such as your own macro files or edits to `printer.cfg`, `moonraker.conf`, or `crowsnest.conf`, aren't put back; they're in that backup. Valve Oil also keeps its record of what it changed in the printer's system, which QIDI's package would delete, so a later [uninstall](#uninstall) still undoes all of it, and your choices about the OS optimizations and AI detection stay as they were.
5. Writes the USB Boxes into `box.cfg` again, each with its number from before the update, then flashes the THR, the Max 4's mainboard, and each USB Box, with the services still stopped.
6. Starts Klipper and Moonraker, installs Valve Oil for the new firmware, then starts the touchscreen.
7. When the install's [OS optimizations](#os-optimizations) need a reboot, reboots the printer about 5 seconds after the update ends, whether or not your SSH session is still open. The install it runs doesn't restart Klipper at its end, since the reboot starts it. The touchscreen restarts Klipper a few times as it finds the boards, so the reboot waits up to 5 minutes for the printer to answer, and doesn't reboot while a print is running or paused or a heater is on.

Afterwards, home the printer and run input shaper calibration before bed leveling or printing.

**If the power goes off** during the update, it continues by itself when the printer starts again. If three starts in a row each stop part way, it stops trying at startup and leaves the printer running. Even so, keep the printer powered on: a board that loses power while it's being flashed may not come back by itself.

**Running `./install.sh` again** while an update is running follows it and shows its progress. If one stopped part way, it continues it, but only while nothing prints and no heater is on. If one finished after your session ended, it shows how it went. Until an update that stopped part way is finished, other installer commands stop with `A QIDI firmware update is in progress or did not finish; run ./install.sh to follow or finish it before anything else`. See [A QIDI firmware update stopped part way](troubleshooting.md#a-qidi-firmware-update-stopped-part-way).

**The `qidi` password.** The update runs without a terminal, so like automatic updates it uses `sudo` with QIDI's default password, `qiditech`. On a printer whose `qidi` password has been changed, the firmware update doesn't start: `the update needs sudo with QIDI's default password, as automatic updates do`.

**China-region printers.** The mirror holds QIDI's packages for its global (`NA`) region. On a printer that came with QIDI's China-region firmware, the update installs the `NA` package, including its network settings for the global region. Valve Oil doesn't handle China-region packages.

### QIDI's own firmware updates

While Valve Oil is installed, the touchscreen doesn't offer QIDI's online firmware updates, so an update offered by QIDI can't remove Valve Oil or put the printer on a firmware Valve Oil doesn't support yet. QIDI's **New SOC firmware available** pop-up and the firmware page's online update no longer appear. The touchscreen's notices for a single board and its **Update** for the boards, such as the closed-loop motor drivers, still work.

An update you start from a USB drive, or from a `QD_Mandatory_Update.zip` file, still installs QIDI's package. It's the way back onto a printer that can no longer be reached over the network. QIDI's package replaces the touchscreen app, Klipper, Moonraker, Fluidd, and the config folder, so it removes much of Valve Oil. Run the install command again afterwards. Valve Oil keeps a copy of its record of what it changed in the printer's system in `~/printer_data`, outside the config folder, so the install picks it up again and a later [uninstall](#uninstall) still undoes those changes. A printer that had a USB update while it ran a release before 26.11.26.1 has no such copy. The install then takes the record from the [configuration backups](troubleshooting.md#restore-a-backup), but only while one from before the USB update is still kept. Valve Oil keeps the three newest, so that works when Valve Oil had updated itself at least once before the USB update, and no more than two installs or updates ran after the USB update and before 26.11.26.1, the install right after it included. Without such a backup, uninstall still removes every service and file of Valve Oil's own, such as the boot animation, but leaves the QIDI files and settings Valve Oil changed as they are.

This applies to the touchscreen apps Valve Oil has reviewed: Max 4 firmware `01.01.07.01` and `01.01.07.02`, Plus 5 firmware `01.01.02.01` and `01.01.02.02`, and Smart 5 firmware `01.01.02.01`. On the Max 4's older firmware, and on a touchscreen app that has been replaced or changed, the touchscreen offers QIDI's online updates as it does without Valve Oil. Uninstalling Valve Oil puts QIDI's touchscreen app back, and with it QIDI's online updates.

### Automatic updates

Every install schedules Valve Oil's update check, 5 minutes after the printer starts or an install finishes, and then every hour. What it does depends on whether automatic updates are on:

- **Off:** it only looks. When a new Valve Oil release, a QIDI firmware update, or board firmware is waiting, the printer warning says `Automatic updates are off, so run the Valve Oil installer to install it.` It installs, stops, and flashes nothing. Install what's waiting by running the install command, or with **Update now**.
- **On:** it installs what's waiting by itself while the printer is idle: Klipper ready, no print running or paused, and every heater target 0. That includes a new Valve Oil release, the [QIDI firmware update](#qidi-firmware-updates), and Valve Oil's board firmware, such as for a USB Box connected since the last release. To read or flash a board, it stops Klipper and the touchscreen for a few minutes, and for longer to flash a Box or update the QIDI firmware. Each update runs the same checks as a normal install, applies the [OS optimizations](#os-optimizations), and reboots the printer when they need it, once Klipper has been idle for 10 minutes. Keep the printer and every Box powered on while an update runs. When something can't install yet, the warning says why: `Automatic updates are on, but it hasn't been installed yet:` followed by the reason, such as a print running or a heater on.

A board the update check tried and couldn't bring to Valve Oil's firmware isn't tried again by later checks until it reports another firmware version; the next install tries again.

#### Update window

Set an **Update window** to choose the hours automatic updates start in, such as 02:00 to 05:00. Outside it, the hourly check still looks and shows what's waiting, but installs, restarts, and flashes nothing, and the printer warning says `Automatic updates are on, so it will be installed during the update window, 02:00–05:00 America/Chicago, while the printer is idle.` Hours are whole hours, up to but not including the end hour, and a window can run past midnight, such as 22:00 to 04:00. The check runs once an hour, so an update starts within the window's first hour and may run past the window's end. Without a window, automatic updates start at any hour, as before. Each check does one step: installing a release, starting the QIDI firmware update, or flashing boards. When a release, QIDI firmware, and board firmware are all waiting, a window shorter than three hours can spread them over several nights. **Update now** and `./install.sh` ignore the window.

The printer's clock runs in UTC, so the window is read in your timezone. The first install with this release looks it up from the printer's internet address, by asking `ipapi.co`, or `worldtimeapi.org` if that doesn't answer; they see the printer's public address and nothing else. Setting a window in Fluidd or Valve Oil UI saves your browser's timezone when none is saved yet, and **Use <timezone>** there switches to the browser's. A timezone that's saved is never looked up again. Without one, the window is read in UTC.

Set the window with the **Update window** setting under the **Automatic updates** switch in Fluidd's **Valve Oil** settings or on Valve Oil UI's [Updates](valve-oil-ui.md#updates) screen, or over SSH:

```bash
~/valve-oil/auto-update.sh --window 2-5                 # 02:00 to 05:00
~/valve-oil/auto-update.sh --window 22-4                # 22:00 to 04:00, past midnight
~/valve-oil/auto-update.sh --window any                 # any hour
~/valve-oil/auto-update.sh --timezone America/Chicago   # the timezone the window is read in
```

`--window` and `--timezone` also go with `--enable` and `--disable`. Each prints the same as `--status`, which includes the window.

#### The chamber light

Stock `printer.cfg` turns the chamber light on whenever Klipper starts, and an update restarts Klipper, or the whole printer for a QIDI firmware update. When the light is off as an automatic update starts, Valve Oil turns it off again each time Klipper starts during the next 3 hours, which covers the update and the restart that can follow it. A light that's on is left alone. It may come on for a few seconds while Klipper starts. This starts with the update after the one that installs this release, since the update check runs from the release already installed.

Turn automatic updates on or off with the **Automatic updates** switch in Fluidd's **Valve Oil** settings or on Valve Oil UI's [Updates](valve-oil-ui.md#updates) screen, by answering the installer's question, or over SSH. It needs no password and takes effect at the next check. Printers that had automatic updates on with an earlier release keep them on.

```bash
~/valve-oil/auto-update.sh --enable    # turn automatic updates on
~/valve-oil/auto-update.sh --disable   # turn them off
~/valve-oil/auto-update.sh --status    # whether they're on, the update window, the last check, and what's waiting
~/valve-oil/auto-update.sh --run       # check now; installs only with automatic updates on
```

`--enable-systemd` and `--disable-systemd`, the names earlier releases used, still work. Automatic updates use `sudo` with QIDI's default password, `qiditech`, so on a printer whose `qidi` password has been changed they fail wherever they need `sudo`, which includes installing a release and the QIDI firmware update.

## Uninstall

If `~/valve-oil/` is still on the printer (`~/tltg-optimized-macros/` with releases up to 26.11.12.2):

```bash
~/valve-oil/install.sh --uninstall --plain
```

Or fetch the latest installer and uninstall with it:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/install-latest.sh)" -- --uninstall
```

Valve Oil UI's [Updates](valve-oil-ui.md#updates) screen also has **Uninstall**, which runs the same uninstall without the questions: it undoes the OS optimizations unless you turn that off, and then restarts the printer if undoing them needs it.

Uninstall always puts back everything an install changes in QIDI's own files: Fluidd goes back to QIDI's Fluidd, the Printer screen service is removed, QIDI's Moonraker and Klipper files are restored, and QIDI's touchscreen app is put back, so the touchscreen offers and installs QIDI's firmware updates again. It removes the update check and turns automatic updates off. It asks whether to also undo the [OS optimizations](#os-optimizations); `--keep-system-optimizations` keeps them without asking. When undoing them needs a reboot, it also asks, with the other questions, whether to restart the printer at the end; `--reboot-host` answers yes. Every question comes before anything is removed. Files you changed after installing are left alone and listed.

Uninstall can't run while a [QIDI firmware update](#qidi-firmware-updates) is unfinished. It doesn't change firmware: the QIDI firmware and the board firmware stay as they are. To put QIDI's board firmware back, see [Board firmware](firmware.md). Installing again after an uninstall counts as a new install, which reads every board first.
