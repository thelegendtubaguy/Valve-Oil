# Valve Oil QIDI Box firmware

`max4-box-valve-oil-v1` is built from QIDI's USB Box firmware `02.03.01.21`. It fixes how the Box handles a Klipper shutdown and adds two RFID reader commands.

## Shutdown fix

The fix changes how the USB QIDI Box handles a Klipper shutdown. In QIDI's firmware, every shutdown of the Box's processor, whether from an emergency stop, a heater safety limit, or any other Klipper MCU error, loops in QIDI's Box feeder shutdown code until the Box's watchdog restarts it. Klipper then reports `Lost communication with MCU 'mcu_boxN'` instead of the reason, and every other Box restarts the same way. With the fix, a Box that shuts down stays connected and Klipper shows the real reason, such as `Missed scheduling of next digital out event` when a Box heater goes 5 seconds without an update from Klipper.

The fix changes two instruction bytes. RS-485 Boxes have no Klipper processor and are not affected.

## RFID commands

Each USB Box has two FM17550 RFID readers, each serving two adjacent slots. QIDI's firmware can only read MIFARE Classic tags in one fixed way. The new commands let Klipper talk to any ISO14443A tag through those readers:

- `fm17550_select oid=%c` wakes the tag in the reader's field, including one QIDI's last read put to sleep, selects it, and answers `fm17550_select_return` with status (`0` no tag, `1` selected, `2` reader not responding) and the tag's ATQA, UID bytes, and SAK.
- `fm17550_transceive oid=%c data=%*s` runs one reader exchange with the selected tag. `data` is the reader command (`0x0c` Transceive or `0x0e` MIFARE login), a timeout, and up to 32 frame bytes. It answers `fm17550_transceive_return` with the reader's error flags, its Status2 register, the number of bits received, and up to 44 received bytes.

They replace QIDI's `fm17550_write_card_cb`, `fm17550_init_cb`, `fm17550_read_regs_cb`, and `fm17550_read_fifo_cb` test commands, which no QIDI software sends. QIDI's own tag read, `fm17550_read_card_cb`, is unchanged. The handlers are C in [`box-rfid/`](box-rfid/), compiled into the bodies of the replaced commands, so the firmware size doesn't change.

## Version

The firmware reports its version as `02.03.01.21-valve-oil.1`, which is QIDI's version, the project name, and a revision that goes up with each new Box firmware. You can see it in `klippy.log` (`Loaded MCU 'mcu_boxN' … (02.03.01.21-valve-oil.1 …`), in Fluidd's system information, and with `./box-firmware.sh status`. Earlier builds, `02.03.01.21-tltg-shutdownfix1` with only the shutdown fix and the development build `02.03.01.21-tltg-rfid1`, are replaced by the installer and by `install`.

## Commands

Every install and update, including one run with `--yes`, flashes each USB Box that needs it after the rest of the install finishes, without asking. It does what `install` below does, except for the `FLASH` prompt. If it can't flash a Box safely, for example because a heater is on, it leaves that Box as it is, says why, and the install still succeeds. Automatic updates never flash a Box.

Run these from the unpacked installer folder (`~/tltg-optimized-macros`) over SSH:

```sh
./box-firmware.sh status               # read-only: each USB Box and its firmware
./box-firmware.sh install              # flash the fix to every USB Box that needs it
./box-firmware.sh install --box 1      # only Box 1 (repeat --box for more)
./box-firmware.sh restore              # put QIDI's stock firmware back
./box-firmware.sh rescue --box 1       # recover a Box that is stuck in its bootloader
```

`install` and `restore` show each Box's firmware and what they will do, then ask you to type `FLASH`. They need an idle, ready printer with every heater target at zero. Klipper and the touchscreen stop for about four minutes per Box. Keep the printer and every Box powered on until the script finishes.

For each Box the script:

1. Reads the Box's bootloader and firmware through Klipper's connection to it, and admits them only if both match a validated build exactly.
2. Saves the read-back under `~/printer_data/.tltg_box_firmware_backups/`.
3. Builds the patched image from the Box's own stock firmware, flashes it, reads it back, and checks it matches exactly, with the bootloader unchanged.

A Box that already has the requested firmware is skipped. A Box with the earlier shutdown fix is reflashed with a new image built from QIDI's stock firmware, taken from the script's earlier backups or `~/update`; without one, `install` stops before changing anything. A Box whose bootloader or firmware the script does not recognize is not changed: the script saves a read-only capture and points to a shareable `report.json` in it. Attach only `report.json` to a GitHub issue; the `.bin` files stay private.

## If a flash fails

The Box's bootloader is never erased or written. If a transfer stops part way, the Box erases the start of its firmware and stays in its bootloader, waiting for a new transfer. The script then leaves Klipper and the touchscreen stopped, keeps `~/printer_data/.tltg_box_firmware_pending.json`, and prints the command to run:

```sh
./box-firmware.sh rescue --box N
```

Rescue waits for the Box's bootloader, flashes QIDI's stock firmware from the backup or from `~/update`, verifies it, and starts the services again. If the Box is not already waiting, turn that Box off and on again when the script asks. Until the pending record is cleared, the installer refuses other operations.

Rescue flashes only with a backup of a validated bootloader. If every backup of that Box is from a bootloader the script doesn't recognize, rescue changes nothing. If a pending record exists and the Box is still running its firmware, rescue starts Klipper and the touchscreen again and clears the record.

Uninstall and `restore.sh` do not change Box firmware. Use `./box-firmware.sh restore`. A QIDI firmware update that reflashes the Box also restores stock firmware. Either way, the next install flashes the Valve Oil Box firmware again.

## Checks for developers

`scripts/generate_box_firmware_manifest.py` rebuilds [`max4-box-valve-oil-v1.json`](max4-box-valve-oil-v1.json) from QIDI's stock image and the sources in `box-rfid/`. It needs `clang` with the Thumb target and the `zopfli` Python package; `--check` compares instead of writing. Installs use only the manifest.

`scripts/check_box_firmware.py` checks the patch against QIDI's stock image, including the data dictionary the Box serves to Klipper, which it runs in emulation. It also runs the RFID handlers in emulation, with QIDI's reader routines replaced by a model of an NTAG tag, a MIFARE Classic tag, no tag, and a reader that doesn't respond. Install `firmware/check-requirements.txt` first.

```sh
python3 scripts/check_box_firmware.py --stock-image QD_MAX4_BOX_02.03.01.21_20260124.bin \
  [--bootloader-backup bootloader.bin [--ram-dump ram.bin]]
```

With a captured bootloader, it runs the transfer tool against that bootloader's own code: stock and patched transfers, lost acknowledgements, an unacknowledged first packet, a non-erasing identity probe, and a bootloader already waiting. With a RAM dump read from a configured Box, it runs the firmware's shutdown handlers: stock must re-enter shutdown, the patched image must report the original reason. No bootloader or RAM binary is shipped in the repository.
