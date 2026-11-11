## Why

Operators want remote-access and similar programs on their printers, starting with OctoEverywhere (issue 38). Their own installers are written for many printers. On a QIDI printer, OctoEverywhere's installer installs system packages, replaces NTP, sets the clock, and adds a Moonraker update-manager section. That section turns on Moonraker's update manager, whose system updater upgrades Debian packages, and QIDI's wiki warns against updating after installing OctoEverywhere. None of that is needed to run the program. Operators also want to install such programs from Fluidd instead of over SSH.

## What Changes

- Add optional add-ons: programs the package pins in `installer/package.yaml` (`addons:`), ships prebuilt in the release bundle, and installs into their own directories under `~/tltg-addons/` with their own Python environments and systemd units, changing nothing else on the host.
- Add `addons.sh` (`list`, `install`, `update`, `remove`), and a package-owned add-ons service with a Fluidd nginx route that Fluidd's new **Add-ons** settings section uses to do the same with one click.
- Install and update bring installed add-ons to the release's pinned builds, restoring the previous build when the new one does not keep running; uninstall removes them.
- First add-on: OctoEverywhere 5.4.1, with its status and account linking shown in Fluidd.
- The Fluidd fork gains the **Add-ons** settings section; its release is pinned with this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: optional add-ons, their Fluidd settings, and their lifecycle across install, update, and uninstall.

## Impact

- `installer/package.yaml` (`addons:`), `installer/runtime/addons.py`, `addons_cli.py`, `addons_service.py`, `installer/system/tltg_addons.py`, `installer/release/addons.sh`, the Fluidd nginx block in `remote_screen.py`, and install and uninstall hooks.
- `scripts/build_installer_bundle.py` downloads and verifies the add-on files; `scripts/lock_addon.py` prints an add-on release's pins. Release bundles grow by about 16 MB.
- The Fluidd fork's Settings page.
- `README.md`, `docs/addons.md`, `docs/settings.md`, `docs/under-the-hood.md`, `docs/fluidd.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`.
