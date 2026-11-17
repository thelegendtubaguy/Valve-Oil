## Why

Installing Valve Oil starts with SSH: finding the printer's IP address, opening a terminal, and pasting a command. That is the hardest step for most owners, and the prompts that follow arrive at five different points in the run, two of them after the installer has already said `Installed.` QIDI's touchscreen has an Offline Update page that installs a package from a USB drive, checking only the file's name and extension; a probe package ran through it as root on the Max 4, Plus 5, and Smart 5 (`openspec/observations/qidi-platform.md`, "Touchscreen firmware updates"). A Valve Oil package on a USB drive can therefore start an installer on the printer's own screen, with no computer after the download.

## What Changes

- Each release publishes a USB installer: a zip the owner extracts to a USB drive, plugs into the printer, and starts from the touchscreen's Offline Update page.
- The USB installer runs on the touchscreen. It shows what it found, asks every question before changing anything, shows one summary, then installs without further input and ends on a finish screen. `screens.html` in this change holds the mockups.
- It restores QIDI's firmware manifest and board images in `/home/qidi/update/`, which QIDI's Offline Update deletes before it runs a package.
- One drive image serves every supported printer, and carries QIDI's target firmware packages, so a printer on older supported firmware is updated and installed with no network.
- Run on a printer that already has Valve Oil, the same drive updates, changes the install's settings, or restores a backup. Uninstalling stays where it is today.
- Every install, update, and uninstall run saves a log on the printer, and the USB installer also saves it to the drive.
- The SSH installer moves to the same flow: every question first, one summary, then an unattended run, with the touchscreen showing its progress.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: installing from a USB drive on the touchscreen, answers collected before any change, and a saved log for each run.

## Impact

- `installer/runtime/`: the install flow takes its answers up front; a touchscreen front end beside the terminal reporter; a log file for each run.
- `scripts/build_installer_bundle.py` and the release workflow: the USB zip and its package.
- `installer/package.yaml`: stock firmware manifests and board images per supported firmware, with their hashes.
- `README.md`, `docs/install.md`, `docs/troubleshooting.md`, `docs/under-the-hood.md`, and `CHANGELOG.md`.
- Depends on vendor behavior recovered statically for Max 4 `01.01.07.01` only; nothing ships until it is confirmed on a printer for each supported firmware.
