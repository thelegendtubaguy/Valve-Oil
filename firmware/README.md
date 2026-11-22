# THR FIFO fix

`max4-thr-valve-oil-v1` patches the LIS2DW FIFO-empty decision in the Max 4 THR `02.02.01.08` image shipped with QIDI firmware `01.01.07.01`. It applies the behavior of [upstream Klipper fix `6269dda56b`](https://github.com/Klipper3d/klipper/commit/6269dda56bb7c08263153b18715d08e2d964bcab), without the separate FIFO batching change.

To undo the fix on a printer, see [Restore the stock THR firmware from backup](#restore-the-stock-thr-firmware-from-backup).

The generator changes five THR image bytes at two instruction sites and the THR's version, which becomes `02.02.01.08-valve-oil.1`. That is QIDI's version, the project name, and a revision that goes up with each new THR firmware, as for the Box and mainboard firmware. The version change recompresses the THR's data dictionary into its own space and updates the three `identify` length constants. Every other component payload, the firmware manifest, and internal filenames stay unchanged. The ZIP container is repacked. Supported input and output image hashes are recorded in [`max4-thr-valve-oil-v1.json`](max4-thr-valve-oil-v1.json).

## Generate the package

Use Python 3.11 or newer on a development computer. Download the exact [Max 4 release archive](https://github.com/thelegendtubaguy/Qidi-Max4-Defaults/releases/tag/qidi-max4-firmware-01.01.07.01-0cc97445e77295116adaa665ff463e77608d4929520bd7cd46e732fbb0a6ecf2), then run from the repository root:

```bash
python3 scripts/build_thr_firmware.py \
  --stock-archive QD_MAX4_01.01.07.01_20261016_Release.zip \
  --output-dir dist/max4-thr-valve-oil-v1
```

The output directory must not exist. The generator rejects unknown, modified, and already-patched archives. It leaves the original archive untouched and creates:

- `QD_MAX4_01.01.07.01_20261016_VALVE_OIL_THR_v1.zip`: the complete vendor package with the patched THR payload.
- `QD_MAX4_THR_02.02.01.08_20260303_VALVE_OIL_v1.bin`: the same patched THR image on its own.
- `max4-thr-valve-oil-v1.report.json`: input provenance, patches, and output hashes.
- A `.sha256` sidecar for each artifact.

The patched THR SHA-256 is `b6e39b1def0bad6c2379cad600c1284cece0c26caab77dbb659adf64d4f4172a`. The earlier build, `max4-thr-lis2dw-fifo-v1` (`9d2360ba47ebd720dbef07a0a4ebaf26004360bca090997d7b4a118c41460ac9`), had the same FIFO fix and kept QIDI's version `02.02.01.08`.

## Verify the package

Install the checking dependencies into a virtual environment:

```bash
python3 -m venv .venv-thr
.venv-thr/bin/python -m pip install -r firmware/check-requirements.txt
.venv-thr/bin/python scripts/check_thr_firmware.py \
  --stock-archive QD_MAX4_01.01.07.01_20261016_Release.zip \
  --patched-dir dist/max4-thr-valve-oil-v1
```

The checker compares every archive payload, runs the THR's `identify` handler in emulation to check that the dictionary it serves differs from stock only in its version, verifies artifact checksums and provenance, tests rejection boundaries, and emulates the stock and patched FIFO-query instructions across every FIFO status byte and two reporting-buffer states. SPI transfers, host reporting, and timer registration are stubbed; this does not measure live sampling rate or validate the printer's other firmware paths.

## Before flashing

Generation and checking do not contact or flash a printer. Valve Oil's installer flashes this THR image on every install and update, automatic ones included, without a question, on the QIDI firmware the manifest's `targets` pin it for: Max 4 `01.01.07.01` and Plus 5 `01.01.02.01`, whose THR board and stock image are the same. On an older Max 4 firmware it waits until the installer's QIDI firmware update has installed `01.01.07.01`. `--flash-thr-fifo`, which earlier releases required, is accepted and ignored. The installer checks actual application bytes and skips programming when the fix is already installed. A THR with the earlier build, `max4-thr-lis2dw-fifo-v1`, is updated from QIDI's stock THR image, taken from the THR firmware backups or `~/update`; without one, nothing is programmed. Bootloaders are admitted by exact validated build identity. An unrecognized build refuses a new installation before any change; on an existing installation it is captured without reset or programming, with a shareable `report.json` for a GitHub issue, its firmware remains unchanged, previous services are restored, and configuration installation is retained.

The package's `firmware_manifest.json` still lists QIDI's THR version. A completed touchscreen update is not proof that the patched THR was flashed. These build checks do not establish installation or physical recovery. See [THR-only operations](FLASHING.md) for the restricted tool, readback verification, and required recovery-entry validation.

The ZIP is a **full QIDI update**, including the unchanged SOC and other MCU components. Installing it can replace printer configuration and remove Valve Oil's macros, as a stock update does; while Valve Oil is installed, the touchscreen refuses to install it, as it does every QIDI package. Keep the original archive for recovery and back up printer configuration before any firmware installation. Use the standalone image only with a verified Max 4 THR flashing and recovery procedure.

## Smart 5 build

`smart5-thr-valve-oil-v1` applies the same fix to the Smart 5 THR `02.02.01.09` image shipped with QIDI firmware `01.01.02.01`, whose FIFO query moved into its own function. It changes two instruction bytes, `cbnz r6` to `cbz r6` and the scheduler store from `r6` to `r7`, which is zero for the whole function, and the version, which becomes `02.02.01.09-valve-oil.1`, in the same way as above. Its hashes are in [`smart5-thr-valve-oil-v1.json`](smart5-thr-valve-oil-v1.json); the patched THR SHA-256 is `aec05626791d939a8355e995a61f9c94c7d2524dbb7570e12552dceaf95de71b`. Build and check it from the [Smart 5 release archive](https://github.com/thelegendtubaguy/Qidi-Smart5-Defaults/releases/tag/qidi-smart5-firmware-01.01.02.01-5677f126716aaa0c084c02a787145bc911cd346badd87cca350cecaeebbc041e) with `--manifest`:

```bash
python3 scripts/build_thr_firmware.py \
  --stock-archive S5_01.01.02.01_20261016_Release.zip \
  --output-dir dist/smart5-thr-valve-oil-v1 --manifest smart5-thr-valve-oil-v1.json
.venv-thr/bin/python scripts/check_thr_firmware.py \
  --stock-archive S5_01.01.02.01_20261016_Release.zip \
  --patched-dir dist/smart5-thr-valve-oil-v1 --manifest smart5-thr-valve-oil-v1.json
```

The installer flashes it on the Smart 5 as it flashes the Max 4 build on the Max 4 and Plus 5. To restore the Smart 5's stock THR firmware, follow the procedure below with `S5_01.01.02.01_20261016_Release.zip`, `S5_THR_02.02.01.09_20260520.bin`, the stock hash `4bcc5dea3fec9d114dc444c0b9b24933eca901adf0051932a9c5b6afca144b54`, and `--manifest smart5-thr-valve-oil-v1.json` on every `flash_thr_firmware.py` command.

## Restore the stock THR firmware from backup

Use this procedure to remove the FIFO fix and put QIDI's stock THR application back. Configuration restore (`restore.sh`), uninstall, and Valve Oil's QIDI firmware update do not restore THR firmware. While Valve Oil is installed, the next install, or the next update check with automatic updates on, flashes the fix again. Run every command on the printer over SSH as `qidi`.

Restoring erases and reprograms the THR application, the same operation as flashing the fix. Keep printer power on until verification completes. If `~/printer_data/.vo_thr_firmware_pending.json` exists, an installer flash didn't finish: turn the printer off and on, or run `./install.sh`, and Valve Oil finishes it first (see [Interrupted transfers](FLASHING.md#interrupted-transfers)).

### 1. Find the stock backup

When the installer flashes a stock board, it first saves the stock application and bootloader in a `before/` folder under `~/printer_data/.vo_thr_firmware_backups/`. List the backups that contain the stock application:

```bash
grep -l 1d34b4b0142f2a2047f08c2fae59d827bcee0adbd1563b4bd1de311dff8b2d62 \
  ~/printer_data/.vo_thr_firmware_backups/thr-*/before/snapshot.json
```

Set `RECOVERY` to the `thr-…` folder of a listed file, for example:

```bash
RECOVERY=~/printer_data/.vo_thr_firmware_backups/thr-abcd1234
```

An installer run that found the fix already installed, or that updated the earlier build of the fix, has no stock application in `before/`, so it is not listed. If nothing is listed, build the stock backup from QIDI's [`QD_MAX4_01.01.07.01_20261016_Release.zip`](https://github.com/thelegendtubaguy/Qidi-Max4-Defaults/releases/download/qidi-max4-firmware-01.01.07.01-0cc97445e77295116adaa665ff463e77608d4929520bd7cd46e732fbb0a6ecf2/QD_MAX4_01.01.07.01_20261016_Release.zip). Copy the archive to the printer, then take the bootloader from any `thr-…` backup folder:

```bash
RECOVERY=~/thr-stock-recovery
mkdir -p "$RECOVERY/before"
unzip -p QD_MAX4_01.01.07.01_20261016_Release.zip QD_MAX4_THR_02.02.01.08_20260303.bin \
  > "$RECOVERY/before/application.bin"
cp ~/printer_data/.vo_thr_firmware_backups/thr-abcd1234/before/bootloader.bin "$RECOVERY/before/"
```

The tool refuses an application or bootloader whose SHA-256 is not pinned, so a wrong or damaged file fails before anything is erased.

### 2. Stop the printer services

Confirm no print is running or paused and every heater target, including the chamber, is zero. Note which services are active, then stop them:

```bash
systemctl is-active klipper.service qidi-client.service vo-auto-update.timer
sudo systemctl stop vo-auto-update.timer  # tltg-optimized-auto-update.timer before 26.11.22.1
sudo systemctl stop qidi-client.service
sudo systemctl stop klipper.service
```

### 3. Flash the stock application

```bash
/home/qidi/klippy-env/bin/python ~/valve-oil/scripts/flash_thr_firmware.py transfer \
  --image "$RECOVERY/before/application.bin" \
  --backup-dir "$RECOVERY/before" --reset \
  --printer-idle-confirmed --confirm-write
```

A successful transfer prints `"transfer_acknowledged": true` and `"verified": false`. The restore is not complete until step 4 passes.

### 4. Verify the restored firmware

Wait at least twelve seconds for the THR application to return, then read it back and compare it with the stock image:

```bash
sleep 12
/home/qidi/klippy-env/bin/python ~/valve-oil/scripts/flash_thr_firmware.py snapshot \
  --output-dir "$RECOVERY/restored" --printer-idle-confirmed
/home/qidi/klippy-env/bin/python ~/valve-oil/scripts/flash_thr_firmware.py verify \
  --image "$RECOVERY/before/application.bin" --backup-dir "$RECOVERY/restored" \
  --original-backup-dir "$RECOVERY/before"
```

`--output-dir` must not already exist; use a new name when repeating this step. The restore succeeded only when verify prints `"verified": true` and `"application_sha256": "1d34b4b0142f2a2047f08c2fae59d827bcee0adbd1563b4bd1de311dff8b2d62"`. If any step fails or the application does not return, keep the services stopped and follow [THR-only operations](FLASHING.md).

### 5. Restart the printer services

Start only the services that were active in step 2:

```bash
sudo systemctl start klipper.service
sudo systemctl start qidi-client.service
sudo systemctl start vo-auto-update.timer
```

Confirm Klipper is ready before printing. The restore does not change `printer.cfg`. Installer versions before 26.11.03.1 saved a `printer.cfg` in the `thr-…` folder, the copy from before they cleared input shaper calibration; do not copy it over the current file, because that discards every configuration change made since. Rerun `M901` if you want input shaper calibration measured with the stock firmware.
