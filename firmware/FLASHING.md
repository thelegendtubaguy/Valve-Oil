# THR-only operations

`scripts/flash_thr_firmware.py` operates only on `/dev/ttyS4`, using a known THR application and an admitted bootloader profile. `--manifest` names the THR manifest in `firmware/` for the printer's THR image: `max4-thr-valve-oil-v1.json` (the default) for the Max 4 and Plus 5 `02.02.01.08`, `smart5-thr-valve-oil-v1.json` for the Smart 5 `02.02.01.09`. Pass it to every operation on a Smart 5; the installer always passes the printer's. It does not manage services, change printer configuration, run motion or heater commands, or update other boards. Run hardware operations on the printer with its existing `/home/qidi/klippy-env/bin/python`; retain the repository layout so the script can read its firmware manifest.

**Erase only through `transfer --recover-to-stock`, or the installer, which uses it.** What a bootloader does when a transfer stops partway, and how long it listens after power-on, differs between builds (see [Interrupted transfers](#interrupted-transfers)); with the board's power cut partway, only the `46358f30…` build can still be reached by software. Stock-image and bootloader backups do not by themselves establish physical recovery access.

## Installer

Every install and update, including `--yes` installs, automatic updates, and the QIDI firmware update the installer runs, brings the THR to the image pinned for the printer's target QIDI firmware (`targets` in [`max4-thr-valve-oil-v1.json`](max4-thr-valve-oil-v1.json): the patched image for both Max 4 `01.01.07.01` and Plus 5 `01.01.02.01`; in [`smart5-thr-valve-oil-v1.json`](smart5-thr-valve-oil-v1.json): the patched image for Smart 5 `01.01.02.01`), without a question. `--flash-thr-fifo` is accepted and ignored. On an older QIDI firmware the THR is left unchanged until the printer runs the target firmware. After a THR flash, rerun input shaper calibration before bed leveling or printing; the installer keeps the saved calibration, does not run calibration, and raises a printer warning until a new calibration is saved. `--dry-run` reports the proposed THR work without UART access or service changes.

The installer reads the actual application and bootloader before deciding whether to program. A new installation reads every board first and is refused, before any change, when the THR bootloader or application is not admitted. An already-patched application is verified without resetting or reflashing it. For stock firmware, the installer preserves recovery files, generates the hash-locked patch from the readback, transfers only THR, and verifies complete readback. For the earlier build of the fix (`9d2360ba…`), it generates the patch from QIDI's stock THR image in the THR firmware backups or `~/update`, and keeps that image with the readback bootloader in the run's `stock/` folder as the transfer's stock backup. Every transfer runs in recovery mode with a journal (`progress.json`); a bootloader without validated recovery timing is never erased. If the transfer falls back to the stock application, the installer verifies the stock readback, restores previous services without a firmware blocker, keeps input shaper calibration, and reports that the fix was not applied. Private evidence stays under `~/printer_data/.tltg_thr_firmware_backups/`; configuration restore and uninstall do not revert MCU firmware. On an existing installation, an unrecognized bootloader is captured without reset or programming, then previously active services are restored and readiness is checked. It does not leave a firmware blocker or invalidate the configuration install; the THR and mainboard keep their firmware, and a printer warning links to a prefilled support issue. Attach only the capture's `report.json` to a GitHub issue; keep the flash binaries private.

Once the installer has stopped services for THR work, the terminal's hangup and Ctrl-C no longer stop it, and the transfer tool ignores both from the reset to the end of the transfer.

From before the first erase until the THR is verified, `~/printer_data/.tltg_thr_firmware_pending.json` records the run's recovery directory, the previous service states, and what a rescue needs: the image, the stock backup, the journal, the manifest, and the target firmware. The installer also installs and enables `vo-thr-firmware.service` before the first erase. When a transfer fails, or its readback doesn't verify, the installer runs `rescue` at once; when the installer itself is stopped, the printer restarts, or power is lost, the unit runs `rescue` at the next boot, before Klipper and the touchscreen start, and the next install, update, or automatic update runs it first if the record is still there. A rescue that verifies the THR settles the record: a verified patched image counts as flashed and raises the input shaper warning; QIDI's stock image, or another admitted image the transfer never replaced, counts as not applied. Until a rescue verifies the THR, the record stays, the installer's own serial-owning services stay stopped, and every other installer operation stops at the record; turning the printer off and on runs the unit again. A record written before this release has no rescue in it; recover such a board by hand as below. Do not use the configuration recovery-sentinel command to bypass a firmware blocker.

## Ownership and backups

Before contacting the printer, query its print state. Stop if the state is unavailable or indicates an active or paused print. After confirming idle, check Klipper readiness and all heater targets, including the chamber heater; all targets must be zero.

Record which services are active. Stop the optimized update timer, touchscreen client, and Klipper before opening the UART:

```bash
sudo systemctl stop tltg-optimized-auto-update.timer
sudo systemctl stop qidi-client.service
sudo systemctl stop klipper.service
```

The tool refuses UART access unless all three are inactive. `--printer-idle-confirmed` is an operator assertion of the preceding checks, not an API check by the tool. Keep the printer attended and do not start a job during these operations.

From the repository root on the printer, create a new backup directory:

```bash
/home/qidi/klippy-env/bin/python scripts/flash_thr_firmware.py snapshot \
  --output-dir dist/thr-before --printer-idle-confirmed
```

Snapshot reads the bootloader and complete 35,056-byte application through the installed Klipper transport. It does not reset or write the MCU. Copy the directory to a separate computer before proceeding. The stock recovery directory must contain:

- `bootloader.bin`: 32,768 bytes, SHA-256 `46358f306156edbdee1822dcca17206f78241b01a7e96587215860c4b0d34fc6`, `31f8344db7b091df5429960bc9cd06065e2188c3b8092e43fb65eb3f2833723c`, or `0302f39c204e13a63fdc5c917085afee1f1b5354467c23f8ae2b9eca0dade3f0` (Max 4 and Plus 5), or `a21a3019004a4a3e50ec2e07882b8b39b7ff028161e692e83191144c14da7342` (Smart 5).
- `application.bin`: stock THR SHA-256 `1d34b4b0142f2a2047f08c2fae59d827bcee0adbd1563b4bd1de311dff8b2d62` (`02.02.01.08`, 35,056 bytes) or `4bcc5dea3fec9d114dc444c0b9b24933eca901adf0051932a9c5b6afca144b54` (Smart 5 `02.02.01.09`, 35,072 bytes).
- `snapshot.json`: the captured hashes.

The snapshot command also accepts the known patched application for post-transfer readback. Erasing operations admit only the exact validated bootloader hashes above, not identity replies alone.

Snapshot of an unrecognized bootloader saves `bootloader.bin`, `application.bin`, the complete `application-region.bin`, and a shareable `report.json`, then exits with status `3`. Files have mode `0600`; the summary contains hashes, per-page bootloader hashes, Klipper build information, and non-unique MCU ID registers, but no flash binaries or unique-device-ID reads. No reset, erase, or programming command is sent. The manual tool does not restore services; restore the recorded active services and confirm readiness after this no-change outcome. Unknown application bytes never authorize a transfer. Keep private backups out of Git.

## Non-erasing bootloader probe

With services still stopped and the stock backup preserved:

```bash
/home/qidi/klippy-env/bin/python scripts/flash_thr_firmware.py probe \
  --backup-dir dist/thr-before --reset --printer-idle-confirmed
```

`--reset` first reads and admits the installed application and bootloader, then sends only the THR application's `reset` command. Probe sends the basic identity request at 115,200 baud; it does not send the erasing start command or any firmware data. The expected response payload is `01020102` (model `02`, bootloader `1.2`) for the `46358f30…` build, `0102` for the `31f8344d…` and `0302f39c…` builds, and `01020103` (bootloader `1.3`) for the Smart 5's `a21a3019…` build. The `a21a3019…` build is the `46358f30…` code with one more call at startup, which sends that identity reply once without being asked; the tool accepts a reply only by its kind and payload, so the extra frame can't stand in for a start, data, or end acknowledgement.

After a successful probe, allow at least twelve seconds for the bootloader to return to the application. Take another snapshot in a new directory and verify it against the stock image to establish unchanged flash bytes. Do not resume normal service if the application does not return.

## Confirmed transfer and readback

Only after verifying non-erasing entry, application return, and independent recovery access, transfer an admitted image:

```bash
/home/qidi/klippy-env/bin/python scripts/flash_thr_firmware.py transfer \
  --image dist/max4-thr-valve-oil-v1/QD_MAX4_THR_02.02.01.08_20260303_VALVE_OIL_v1.bin \
  --backup-dir dist/thr-before --reset \
  --printer-idle-confirmed --confirm-write
```

Add `--firmware <version>`, the printer's target QIDI firmware, to have `transfer` refuse any image other than the one the manifest pins for that firmware or QIDI's stock image; the installer always passes it.

The start command erases the entire application region `0x08008000`–`0x0801ffff`, including bytes beyond the supplied image. The bootloader region is not an update target. The tool transfers only the pinned stock and patched images and validates acknowledgements against each packet's index and byte count. It stops rather than blindly retrying an ambiguous packet-zero acknowledgement: every admitted bootloader writes data at a running position, so packet zero sent again is written after itself, while any later packet sent again is skipped.

A successful transfer reports `transfer_acknowledged: true` and `verified: false`. After allowing the application to return, take and verify a complete readback:

```bash
/home/qidi/klippy-env/bin/python scripts/flash_thr_firmware.py snapshot \
  --output-dir dist/thr-after --printer-idle-confirmed
/home/qidi/klippy-env/bin/python scripts/flash_thr_firmware.py verify \
  --image dist/max4-thr-valve-oil-v1/QD_MAX4_THR_02.02.01.08_20260303_VALVE_OIL_v1.bin \
  --backup-dir dist/thr-after --original-backup-dir dist/thr-before
```

Verification is offline and requires the selected application bytes and an admitted bootloader. `--original-backup-dir` additionally compares the bootloader byte-for-byte with its pre-transfer backup; the installer always makes this comparison. Bytes beyond the application image are not installation evidence: different bootloaders can leave different final-packet padding. The version string the THR reports is not installation evidence.

Add `--recover-to-stock` to `transfer`, and `--progress <file>` for a journal a rescue can resume from. On a failed exchange the tool then resumes the requested image when the failed packet can be sent again; otherwise it lets the bootloader's session expire and programs the backup's stock application only after a new session is confirmed, except on the `0302f39c…` build (below). It stops with status `1` rather than send start into a session that may still hold data. Exit status `0` means the requested image was acknowledged, `4` that the stock fallback was programmed instead, `5` that every data packet was acknowledged but end was not, and `1` that recovery failed and the board state is unknown. Every status still needs the twelve-second wait, a snapshot, and `verify` against the image it names. The installer uses this mode for every build and treats status `4` as an unchanged board. Recovery mode is refused for a bootloader without validated timing. Its recovery paths run in emulation (`scripts/check_thr_recovery.py`). On a Max 4 with the `46358f30…` build they have also run on hardware: a transfer whose process was killed partway was resumed by the installer's rescue; transfers stopped partway by an immediate SoC reboot were finished by `vo-thr-firmware.service` at boot, before Klipper started; and a session left with a cut-off packet and a journal one packet behind was waited out and transferred again. Power was never cut during a transfer.

The journal records the image being written and the data packet about to be sent; each change reaches storage, its rename included, before that packet is sent, so after any crash the board holds the journal's packet or the one before it. Any other packet would be wrong to resume from: `46358f30…` and `a21a3019…` reject a packet older than the last, but `31f8344d…` and `0302f39c…` write it at the running position. Beside it, `progress.json.sent` records when the board last heard from the transfer, by boot, at most every half second and never synced, since only a rescue in the same boot uses it.

## Interrupted transfers

When a data session goes quiet for 10,000 bootloader ticks (about ten seconds), every admitted bootloader erases the first application page. Then:

- `46358f30…` and `a21a3019…` stay in the bootloader until power is lost, answering identity and start, so the stock image or the requested one can be sent again at any time;
- `31f8344d…` resets into its startup window and stays only for an identity and start that arrive in the first four seconds;
- `0302f39c…`, `31f8344d…` with one byte different, faults in its reset code and stays halted until power is lost. For this build, transfers never let a session go quiet: a session is kept open with identity requests, which a session neither answers nor writes, and a packet-zero acknowledgement that never arrives ends the transfer with the board state unknown, since nothing then tells whether packet zero was written.

After power-on, a bootloader stays only for an identity and start that arrive within its startup window: ten seconds for `46358f30…`, five for `a21a3019…`, and four for `31f8344d…` and `0302f39c…`; otherwise it jumps to whatever the application region holds. A data packet outside a session is ignored, and so is start without an identity.

`rescue` finishes a transfer another process started:

```bash
/home/qidi/klippy-env/bin/python scripts/flash_thr_firmware.py rescue \
  --image patched-thr.bin --backup-dir dist/thr-before --progress progress.json \
  --output-dir dist/thr-rescue --printer-idle-confirmed
```

With a journal from this boot, it resumes a session that is still open from the journal's packet, unless that is packet zero, or waits the session out and takes the bootloader's next session. Otherwise it sends identity first: a `46358f30…` or `a21a3019…` board that answers gets start only while its startup window must still be open (from the time since boot, allowing 2.5 s from power-on to the kernel's start; a Max 4's THR powers up 1.4 s ahead of it), or once any window must have ended, when only a board waiting after a quiet session answers. A `31f8344d…` or `0302f39c…` board that answers is left to jump. With no bootloader answering, a running application is read as it is; with none, it waits out any session and tries again, except on the `0302f39c…` build. After a transfer it waits twelve seconds and reads the board back into `--output-dir`. Exit status `0` means the board verifiably holds the requested image, `4` another admitted image, such as QIDI's stock image, and `1` that the board state is unknown. `--record <pending record>` takes the image, backup, journal, manifest, and firmware from the installer's record and writes the outcome to `rescue-result.json` in its recovery directory; that is how the installer and `vo-thr-firmware.service` run it.

At boot, `vo-thr-firmware.service` first runs a root-owned catch, `/usr/local/lib/vo_thr_catch.py` (from `installer/system/vo_thr_catch.py`), with Python's standard library only, because the THR tool takes about three seconds to start. The catch gives `/dev/ttyS4` the `dialout` group udev gives it later, sends identity requests for up to 1.5 seconds, and records any reply and when it came in `/run/vo-thr-catch.json`; it sends nothing else. An identity request that reaches a bootloader inside its startup window holds it for a session's time after the last one, in every admitted build. `rescue --at-boot` then treats that reply as its own: a `46358f30…` or `a21a3019…` board gets start if the reply came while its startup window must still have been open. `--at-boot` asks systemd nothing, since the unit is ordered before every service that could open the port.

What software can't recover: a board whose power is lost after its application was erased, unless it is a `46358f30…` board, whose window outlasts the printer's boot; a `0302f39c…` board whose session went quiet or whose packet-zero acknowledgement was lost; and a `31f8344d…` board whose transfer stopped and wasn't resumed or caught in its next startup window. Such a board needs the THR's SWD pins.

On a failure outside the installer, keep the serial-owning services stopped and run `rescue` with the same stock backup and journal. Do not resend a start or data packet from another program.

## Restore normal service

Restore only services that were active before the operation. After verified application return:

```bash
sudo systemctl start klipper.service
sudo systemctl start qidi-client.service
sudo systemctl start tltg-optimized-auto-update.timer
```

Confirm printer readiness and zero heater targets before any measurement. If print state cannot be determined, stop printer access and confirm idle state locally. A stationary accelerometer capture can then compare delivered sample rate and error counters; it requires no homing, heating, motion, or resonance sweep.

## Offline transfer checks

On a development computer, install `firmware/check-requirements.txt`, then run:

```bash
python3 scripts/check_thr_flashing.py \
  --stock-image stock-thr.bin \
  --patched-image dist/max4-thr-valve-oil-v1/QD_MAX4_THR_02.02.01.08_20260303_VALVE_OIL_v1.bin
```

Add `--bootloader-backup bootloader.bin` to execute a captured bootloader's actual parser, erase loop, programming bounds, and reply construction. Repeat that option to check each exact profile in `scripts/thr_bootloaders.py`; pass `--manifest smart5-thr-valve-oil-v1.json` with the Smart 5's images for `a21a3019…`. Every profile is checked for full transfers, what a quiet session does, duplicate packets, lost acknowledgements, non-erasing identity sessions, and admission boundaries. `46358f30…` leaves short-packet padding erased; `31f8344d…` programs zeros. UART output, flash-controller register operations, application jumps, and system resets are emulated; these checks do not validate electrical behavior or physical recovery access. No bootloader binary is shipped in the repository.

For a build with validated recovery timing, `scripts/check_thr_recovery.py` takes the same options, `--bootloader-backup` required and repeatable. It drives the captured bootloader's SysTick from the tool's clock and injects a dead link or lost replies at start, packets 0, 1, 17, and 34, and end, for 0.5 to 40 seconds. Each fault runs both `transfer` and `transfer_recovering`. It also runs the 3 s and 12 s faults with the bootloader's clock 8% and 3% slow and 3% and 10% fast, because the bootloader's oscillator is unverified. At the same points it kills the transferring process and runs a rescue 0.5 to 40 seconds later, and cuts power and runs a rescue at boot, 5.4 s (a Max 4's), 8 s, and 20 s after power-on. The check fails if a board is left with an unusable application in any case the build allows recovering from, and counts the others, listed under [Interrupted transfers](#interrupted-transfers), separately; it also fails if flash that was not erased is programmed. `--quick` runs only the 3 s and 12 s link faults; each build takes several minutes.
