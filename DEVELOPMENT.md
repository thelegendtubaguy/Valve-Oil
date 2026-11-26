# Running the dev branch

Every push to `dev` publishes a fresh installer to the [`prerelease`](https://github.com/thelegendtubaguy/valve-oil/releases/tag/prerelease) release. It hasn't had a release's testing, so something may be half-finished or broken. If you only want a printer that works, use the [README](README.md) install instead.

> [!CAUTION]
> I refuse to be responsible for your printer. PLEASE watch prints start ensuring the printer doesn't do something horrible like gouge your bed and be ready to cut power at a moment's notice. That goes double on dev.

## Install

1. SSH into your printer: `ssh qidi@<your printer's ip>`
2. Run this. It's one command on two lines, so copy both. Add `-- --dry-run` to the end to preview without changing anything.

   ```bash
   VO_INSTALLER_ARCHIVE_URL=https://github.com/thelegendtubaguy/valve-oil/releases/download/prerelease/valve-oil-dev.tar.gz \
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/thelegendtubaguy/valve-oil/dev/installer/release/install-latest.sh)"
   ```

3. **Answer No to automatic updates.** They follow stable releases and would replace your dev install. If they're already on, run `~/valve-oil/auto-update.sh --disable-systemd`.
4. Copy the slicer G-code from your printer's folder (`max4`, `plus5`, or `smart5`) on the `dev` branch: [`orcaslicer_gcode/`](https://github.com/thelegendtubaguy/valve-oil/tree/dev/orcaslicer_gcode) or [`qidistudio_gcode/`](https://github.com/thelegendtubaguy/valve-oil/tree/dev/qidistudio_gcode).

Run both again to pick up newer dev changes.

**Going back to stable:** run the [README](README.md) install command, then turn automatic updates back on if you want them. To remove everything, see [Uninstall](docs/install.md#uninstall). Backups and restore work as on a release; see [If something goes wrong](docs/troubleshooting.md).

**Tell me how it went,** good or bad: see [TESTING.md](TESTING.md) or open an issue.

## Working on the code

- **Try a local build on your printer:** `scripts/build_copy_dry_run.sh --host <printer-ip>` builds a dev bundle from your checkout, copies it over, and runs a dry run. Add `--install` to install it after the dry run; the script returns when the installer ends and fails unless it installed. Options: `--user <name>` (default `qidi`), `--password '<password>'` (needs `sshpass`), `--sudo-password '<password>'` (the `qidi` user's, when it isn't QIDI's default; `--password` is used when this isn't given), `--install-timeout <seconds>` (default 3600), `--debug`.
- **Before opening a PR,** run the checks for what you touched, listed under **Checks** in [AGENTS.md](AGENTS.md#checks).
- **Docs:** the [README](README.md) is the operator's landing page: install, then a line per feature linking to [`docs/`](docs/README.md), where the detail goes. Describe behavior for every supported printer, and name the printer a position, firmware version, piece of hardware, or measurement applies to. Adding a printer: [community-supported printers](docs/community-printers.md).
- **Run slicer G-code without a printer:** `installer/tests/gcode_flow/` installs Valve Oil into each printer's stock config and runs the slicer packs' G-code through Klipper's own G-code and macro code, with stand-ins for the hardware and QIDI's Box code, then checks where the printer moved, purged, and cut. `python3 scripts/run_installer_core_tests.py` runs it with the other tests.
- **Trace QIDI's Box code:** `scripts/box_trace/box_trace.sh install qidi@<printer-ip>` adds a log-only tracer that records each QIDI Box call, the settings it reads, its G-code, and the Box motor positions during loads and unloads. `fetch` copies the log back and `remove` takes the tracer off. See [`scripts/box_trace/README.md`](scripts/box_trace/README.md).
- **THR firmware:** building and checking the FIFO fix is in [`firmware/README.md`](firmware/README.md). It never flashes a printer.
- **Fake printer:** `VO_PRINTER_DATA_ROOT`, `VO_FIRMWARE_MANIFEST`, and `VO_MOONRAKER_URL` point the installer at a fixture directory instead of a live printer, as `installer/tests/helpers.py` and `scripts/smoke_test_installer_bundle.py` do.
