## Why

Operators asked for Obico alongside OctoEverywhere, through Obico's cloud or their own Obico server. Obico's installer installs system packages, adds a Moonraker update-manager section, and keeps its settings in `printer_data/config`, and the running plugin adds an include to `printer.cfg` whenever Klipper lacks its macros. On a QIDI printer the plugin also finds no Janus build for the board, so it streams no video, and it shows its link code only through a Klipper macro. Two remote-access add-ons on one printer relay Fluidd and the camera twice for no benefit on a host with under 500 MB of memory.

## What Changes

- Add Obico (moonraker-obico 2.2.4) as an add-on. Valve Oil's launcher runs the plugin without its `printer.cfg` include, with the Raspberry Pi Debian 11 Janus build, with its link code written to a status file instead of a macro, and with its snapshot-based camera stream running only while someone views the printer.
- Add-ons can pin pure-Python packages that PyPI publishes only as source; the printer builds them offline after the wheels. Same-directory symlinks in an add-on's source archive are extracted as copies.
- Add `addons.sh relink <id> [--server URL]`, and a matching Fluidd action, to forget an add-on's account link, optionally move it to another server, and restart it so it can be linked again.
- OctoEverywhere and Obico can't be installed together; the add-ons list says which installed add-on blocks an install.
- The Fluidd fork's **Add-ons** section gains the Obico panel and the blocked-install hint; its release is pinned with this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: source-only add-on packages, exclusive remote-access add-ons, relinking, and Obico's Fluidd panel.

## Impact

- `installer/package.yaml` (`addons:`), `installer/runtime/addons.py`, `addons_cli.py`, `models.py`, `manifest.py`, `installer/system/tltg_obico.py`, `installer/system/tltg_addons.py`, `scripts/lock_addon.py`, `scripts/build_installer_bundle.py`.
- The Fluidd fork's Settings page.
- Release bundles grow by about 26 MB.
- `docs/addons.md`, `docs/settings.md`, `docs/under-the-hood.md`, `README.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`.
