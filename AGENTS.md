# AGENTS.md

The checks under **Checks** hold the project's rules; a failing check says what to fix.

## Where changes go

- `config/` is QIDI's stock config, mapped to the snapshot in `installer/stock/` (from `https://github.com/thelegendtubaguy/Qidi-Max4-Defaults` and its Plus 5 and Smart 5 siblings) by `openspec/contracts/stock-config.json`. Valve Oil's changes go elsewhere:
  - stock values: guarded `installer/package.yaml` `patches.*`, or a guarded host-file patch under `installer/runtime/` when QIDI generates the file with varying sections (e.g. `config/box.cfg`);
  - Klipper macros and modules: `installer/klipper/`; read its `README.md`;
  - slicer G-code: `orcaslicer_gcode/<printer>/` and `qidistudio_gcode/<printer>/`;
  - Valve Oil UI, served at `/valve-oil/` beside Fluidd: `ui/`; read `ui/README.md` (translations, and the `ui/src/qidi/` port of the Fluidd fork);
  - installer and runtime: `installer/`.
- Fluidd: the fork `https://github.com/thelegendtubaguy/fluidd`, branch `qidi`, rebased onto upstream `fluidd-core/fluidd`. `installer/package.yaml` `system_optimizations.fluidd_build` pins a fork release. `installer/runtime/fluidd_box_filaments.py` patches QIDI's own Fluidd only when that build can't be served, and takes fixes only. Fluidd's UI is documented in the fork.
- Docs: `README.md` (landing page) and `docs/` (index `docs/README.md`); new settings and commands in `docs/settings.md`, new files or services the installer touches in `docs/under-the-hood.md`. How to write them: `DEVELOPMENT.md`.
- Specs, contracts, and observations: `openspec/` (`config.yaml`, `contracts/README.md`, `observations/README.md`).
- Tests: `installer/tests/` (`README.md`).

## Before changing behavior, read

- QIDI's stock config for the printer and firmware: `installer/stock/qidi-<printer>-defaults/`.
- The spec for the area: `openspec/specs/<area>/spec.md`.
- For a G-code path (start print, end print, filament change, runout, homing, Z offset, fans): its contract and generated view in `openspec/contracts/gcode-paths/`, and `openspec/contracts/README.md` for changing them.
- Vendor behavior: `openspec/observations/` and the stand-ins in `installer/tests/gcode_flow/qidi.py`. To see what QIDI's Box code does on a live printer, use `scripts/box_trace/`.

## Checks

Run what applies to what you touched:

- Installer, macros, Klipper modules, slicer packs: `python3 scripts/run_installer_core_tests.py` (also runs the G-code path checks)
- G-code path contracts: `python3 scripts/check_gcode_paths.py --write`, then `python3 scripts/check_gcode_paths.py`
- Valve Oil macro `.cfg`: `python3 scripts/format_klipper_configs.py`
- Slicer G-code: `python3 scripts/check_slicer_macros.py`
- `config/`: `python3 scripts/check_stock_config.py`
- Any change: `python3 scripts/check_identifiers.py`
- Version, changelog, README printer tables, `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`: `python3 scripts/check_installer_known_versions.py`
- Valve Oil UI: `cd ui && npm ci && npm run typecheck && npm test && npm run build`; check screens against the fake Moonraker (`ui/README.md`)
- Launcher, bundle, or release plumbing: `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test`
- OpenSpec: `openspec validate --all --strict`
- THR firmware generator or patch manifest: `scripts/check_thr_firmware.py` against a fresh `scripts/build_thr_firmware.py` build (`firmware/README.md`)
- THR flashing tool or protocol: `scripts/check_thr_flashing.py` (with `--bootloader-backup` when available) and `scripts/check_thr_recovery.py` with a recoverable build's `--bootloader-backup` (`firmware/FLASHING.md`)

## Test printers

Install a checkout on a printer with `scripts/build_copy_dry_run.sh --host <printer-ip> --install`. It returns when the installer ends, stops it after `--install-timeout` seconds, and fails unless it installed. Don't run `install.sh` over SSH through your own wrapper for the password or waiting.

## Versions

`python3 scripts/bump_installer_version.py <version>` sets the package version, and `CHANGELOG.md` gets a section for it listing what changes for operators. A version not yet released can be renumbered or folded; the installer admits state from any version of this package. `installer/supported_upgrade_sources.yaml` is edited by hand when adding a guarded patch target or source-patch provenance.

## Git

Branch from the latest `origin/dev`; PRs target `dev` except release merges.
