## Why

A QIDI firmware update from the touchscreen wipes Valve Oil's configuration, replaces the files it patches, and can land a firmware this project doesn't support yet, leaving the printer on stock behavior until a release catches up. Valve Oil already mirrors each QIDI package, reinstalls itself after one, and flashes boards, so it can deliver QIDI firmware itself, only once a release supports it.

## What Changes

- **BREAKING** While Valve Oil is installed, QIDI's package updates from the touchscreen (online, USB, and the mandatory-update file) no longer change the printer, and the touchscreen's online-update notice is hidden. The touchscreen's own per-board update stays available.
- Valve Oil delivers QIDI firmware. Each printer has one target firmware per release. An install or update on an older supported firmware downloads the pinned package from this project's mirror, installs it, flashes the boards, and installs Valve Oil for the target firmware in one run.
- **BREAKING** Every printer gets this project's board firmware where one exists: THR and USB Box on both printers, mainboard on the Max 4. The THR fix is no longer opt-in and the mainboard fix no longer manual, and automatic updates flash them unattended.
- **BREAKING** A new install is refused, before any change, when the THR, mainboard, or a USB Box has a bootloader or firmware this release doesn't recognize. The refusal gives a prefilled support-issue link.
- An existing install with such a board keeps working as it does today, without the block, and shows a warning until a release recognizes the board.
- A USB Box connected later is flashed at the next automatic-update run when it is recognized, and otherwise shows a warning that says what to do.
- The hourly check runs on every install. Printers with automatic updates on are adopted and updated unattended when they pass the usual checks. Printers with them off get a warning that a release or firmware update is waiting and that the installer has to be run.
- Fluidd and Valve Oil UI get a Valve Oil section showing the release, the printer firmware, each board's firmware, and anything pending, with a switch for automatic updates.
- The touchscreen shows a "do not power off" message while an update has its app stopped, and an update interrupted by a power loss resumes.

## Capabilities

### New Capabilities
- `qidi-firmware-updates`: blocking QIDI's own package updates, delivering the target QIDI firmware and board firmware, board admission at install, and how pending work is shown.

### Modified Capabilities
- `installer-lifecycle`: the update check is scheduled on every install and automatic updates can be switched from Fluidd; board firmware is no longer per-run opt-in; USB Boxes connected between releases are flashed; an unrecognized Box is left alone only on an existing install.
- `box-firmware-transfer`: installs and automatic updates reach the mainboard tool, and transfers use the images pinned for the printer's target firmware.

## Impact

- Installer: `installer/package.yaml` (target firmware and package identity per printer), `installer/runtime/` (new firmware delivery, block, and admission modules; `auto_update.py`, `thr_firmware.py`, `box_firmware.py`, `mainboard_firmware.py`, `qidiclient_firmware_popup.py`, `preflight.py`, `uninstall.py`), `installer/system/` (a status and settings service beside the add-ons service), `installer/klipper/tltg/printer_warnings.py`.
- Firmware tooling: `scripts/flash_*_firmware.py` and `firmware/*.json` take images per target firmware; the Plus 5 mainboard is admitted for QIDI's stock image.
- UI: the Fluidd fork (`thelegendtubaguy/fluidd`, branch `qidi`) and `ui/`.
- Support: `.github/ISSUE_TEMPLATE/printer-support.yml` fields are filled from the refusal link.
- Docs: `README.md`, `docs/install.md`, `docs/firmware.md`, `docs/settings.md`, `docs/troubleshooting.md`, `docs/under-the-hood.md`, `docs/valve-oil-ui.md`, `firmware/*.md`, `CHANGELOG.md`.
- Evidence: `openspec/observations/qidi-platform.md`, Touchscreen firmware updates.
- Depends on the `Qidi-Max4-Defaults` and `Qidi-Plus5-Defaults` release mirrors staying available.
