# Mainboard firmware

`max4-mcu-valve-oil-v1` changes how the Max 4 mainboard (firmware `02.01.01.12`) passes the closed-loop X and Y drivers' replies to Klipper. QIDI's firmware forwards a reply as soon as it ends only for status (`0x37`) and version (`0x40`). Every other reply, including encoder (`0x31`), position error (`0x39`), temperature (`0x3b`), and the acknowledgement of every setting QIDI sends, waits until the drivers' bus has been silent for 50 ms, so each takes about 70 ms. With the fix, the mainboard knows the length of those replies and forwards each one at its last byte. Replies with any other function code are handled exactly as before, including the 50 ms wait.

| On the test printer, 30 reads per motor | Stock | Fixed |
| --- | --- | --- |
| Status (`0x37`), median | 6.1–6.7 ms | 6.7–7.5 ms |
| Position error (`0x39`), median | 68.8–69.0 ms | 7.7 ms |
| Temperature (`0x3b`), median | 67.8–73.3 ms | 6.9–7.0 ms |
| Encoder (`0x31`), median | 68.0–72.5 ms | 7.8–7.9 ms |
| Position error, back to back | 60.6–62.2 ms each | 7.6–8.9 ms each |

`G28 X Y` took 12.5–12.7 s on stock and 12.8 s with the fix.

The fixed firmware reports its version as `02.01.01.12-valve-oil.1`, which is QIDI's version, the project name, and a revision that goes up with each new mainboard firmware. You can see it in `klippy.log` (`Loaded MCU 'mcu' … (02.01.01.12-valve-oil.1 …`), in Fluidd's system information, and with `./mainboard-firmware.sh status`. The earlier build, `02.01.01.12-tltg-clreplies1`, has the same fix; `install` replaces it.

The fix changes four instructions, the version string, and the space the shorter data dictionary frees, which holds the new reply-length table. Nothing else about the mainboard changes.

The manifest's `targets` pin an image for each printer's target QIDI firmware: the fix for Max 4 `01.01.07.01`, and QIDI's stock `02.01.01.12` for Plus 5 `01.01.02.01`. The Plus 5 ships the same mainboard image, but its motors aren't closed-loop, so it keeps QIDI's.

The manifest also lists QIDI's `02.01.01.11` (`33375f0d…`), from Max 4 `01.01.06.03` and the first `01.01.06.04` package, as a predecessor: it is admitted as the firmware a mainboard runs, so it can be backed up and replaced, but it is never programmed. The Klipper of later packages looks up a command `02.01.01.11` doesn't have, so a mainboard on it is replaced only once the printer runs the target firmware, with the fix built from QIDI's stock `02.01.01.12`. Until then the installer and the script say `the mainboard will be updated after the QIDI firmware update to 01.01.07.01` and change nothing.

## Commands

Every install and update, including `--yes` installs, automatic updates, and the QIDI firmware update the installer runs, brings the mainboard to its pinned image, as `install` below does, without the `FLASH` prompt. A mainboard already on it is left alone. Restore and rescue run only from the script.

Run these from the unpacked installer folder (`~/tltg-optimized-macros`) over SSH:

```sh
./mainboard-firmware.sh status    # read-only: the mainboard's firmware
./mainboard-firmware.sh install   # flash the pinned image: the fix on the Max 4, QIDI's on the Plus 5
./mainboard-firmware.sh restore   # put QIDI's stock firmware back
./mainboard-firmware.sh rescue    # recover a mainboard that is stuck in its bootloader
```

`install` and `restore` show the mainboard's firmware and what they will do, then ask you to type `FLASH`. They need an idle, ready printer with every heater target at zero. Klipper and the touchscreen stop for about three minutes, and the printer can't move or heat until they start again. Keep the printer powered on until the script finishes.

The script:

1. Reads the mainboard's bootloader and firmware through Klipper's connection to it, and admits them only if both match a validated build exactly.
2. Saves the read-back under `~/printer_data/.tltg_mainboard_firmware_backups/`.
3. Builds the patched image from the mainboard's own stock firmware, flashes it, reads it back, and checks it matches exactly, with the bootloader unchanged. Once the services stop, closing the SSH session or pressing Ctrl-C doesn't stop it.

A mainboard that already has the requested firmware is skipped. A mainboard with the earlier build, or with QIDI's `02.01.01.11`, is reflashed with a new image built from QIDI's stock firmware, taken from the script's earlier backups or `~/update`; without one, `install` stops before changing anything. If the script doesn't recognize the bootloader or firmware, nothing is changed: it saves a read-only capture and points to a shareable `report.json` in it. A new Valve Oil install is refused in that case, and an existing one keeps the mainboard and THR firmware as they are, with a printer warning and `tltg_mainboard_firmware.json` in Fluidd's **Logs**. Attach only `report.json` to a [support issue](https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml); the `.bin` files stay private.

## If a flash fails

The mainboard's bootloader is never erased or written. If a transfer stops part way, the bootloader erases the first 4 KiB of the firmware ten seconds later and keeps waiting for a new transfer, also after the printer is turned off and on. The script then leaves Klipper and the touchscreen stopped, keeps `~/printer_data/.tltg_mainboard_firmware_pending.json`, and prints the command to run:

```sh
./mainboard-firmware.sh rescue
```

Rescue flashes QIDI's stock firmware from the backup or from `~/update` through the waiting bootloader, verifies it, and starts the services again. If the transfer failed before anything was erased, the old firmware is still running, and rescue resets the mainboard into its bootloader first. If it reports no reply from the bootloader, wait 15 seconds and run it again. Until the pending record is cleared, the installer refuses other operations.

Rescue flashes only with a backup of a validated bootloader. If every backup is from a bootloader the script doesn't recognize, rescue changes nothing. If a pending record exists and the mainboard is still running its firmware, rescue starts Klipper and the touchscreen again and clears the record. Before 26.11.09.1, rescue on such a mainboard stopped the services and then kept saying the firmware was unverified, though the mainboard was never touched.

**Keep the printer powered on while it flashes.** If power is lost during the transfer, the bootloader may start the partly written firmware at the next power-on instead of waiting, because the printer's computer isn't up within the bootloader's 2-second window. No recovery path for that case has been verified.

During a QIDI firmware update, the update stops with only Moonraker running and says to run rescue; rescue can run once the update has stopped. Then run `./install.sh` to finish the update.

Uninstall and `restore.sh` do not change mainboard firmware. Use `./mainboard-firmware.sh restore`. While Valve Oil is installed, the next install, or the next update check with automatic updates on, flashes the pinned image again.

## Checks for developers

The script runs the transfer tool, `scripts/flash_mcu_firmware.py`, with `transfer --firmware <version>`, the printer's target QIDI firmware, so the tool refuses any image other than the one the manifest pins for it or QIDI's stock image.

`scripts/check_mcu_firmware.py` checks the patch against QIDI's stock image. It runs the firmware's own closed-loop reply parser in emulation, comparing stock and patched, and the data dictionary the mainboard serves to Klipper. Install `firmware/check-requirements.txt` first.

```sh
python3 scripts/check_mcu_firmware.py --stock-image QD_MAX4_MCU_02.01.01.12_20260609.bin \
  [--predecessor-image QD_MAX4_MCU_02.01.01.11_20260402.bin] [--bootloader-backup bootloader.bin]
```

`--predecessor-image QD_MAX4_MCU_02.01.01.11_20260402.bin` (repeat it for each predecessor the manifest lists) checks each predecessor's size and hash, that its own data dictionary reports its version, and that the transfer tool admits it as a board's firmware, finds it in a snapshot, and never programs it.

With a captured bootloader of either admitted build, it runs the transfer tool against that bootloader's own code: stock and patched transfers, lost acknowledgements, a resident bootloader, an unacknowledged first packet, and a non-erasing identity probe. No bootloader binary is shipped in the repository.
