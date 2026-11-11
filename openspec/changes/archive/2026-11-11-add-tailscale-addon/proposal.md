## Why

Operators asked to reach the printer from anywhere over their own Tailscale network, with one click (issue 257). Tailscale's packages install system-wide as root and need a TUN device, netfilter, and policy routing, none of which QIDI's kernel has, so they don't work on a QIDI printer. Tailscale's build is 38 MB, most of the size of a release bundle, and most operators won't install it.

## What Changes

- Add Tailscale 1.104.6 as an add-on. `tailscaled` runs as the `qidi` user in its userspace networking mode, which needs no kernel support and changes no routes, firewall rules, or DNS settings.
- An add-on can be pinned as a download: its file is not in the bundle, and the printer fetches it when the add-on is installed or updated, keeping it only if it matches the pinned sha256. Such an add-on needs no Python packages and gets no Python environment.
- The add-ons service reports Tailscale's state and offers log in, which returns a sign-in address, and log out.
- Tailscale is not a remote-access add-on for exclusivity; it installs alongside OctoEverywhere or Obico.
- Removing an add-on can first run a command of the add-on's; Tailscale's logs the printer out of the tailnet.
- The Fluidd fork's **Add-ons** section gains the Tailscale panel; its release is pinned with this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: downloaded add-ons, the Tailscale add-on, and Tailscale's Fluidd panel.

## Impact

- `installer/package.yaml` (`addons:`), `installer/runtime/addons.py`, `models.py`, `manifest.py`, `installer/system/tltg_addons.py`, `scripts/lock_addon.py`, `scripts/build_installer_bundle.py`.
- The Fluidd fork's Settings page.
- Release bundles do not grow. Installing Tailscale needs the printer to be online.
- Devices on the operator's tailnet reach the printer's services as loopback connections, so Moonraker trusts them and loopback-only services answer them.
- `docs/addons.md`, `docs/settings.md`, `docs/under-the-hood.md`, `README.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`.
