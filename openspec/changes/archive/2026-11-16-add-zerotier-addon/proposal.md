## Why

Operators asked for ZeroTier beside Tailscale, to reach the printer from anywhere over a network they already run. ZeroTier's agent has no userspace network stack: it needs a TUN device, and QIDI's kernels on the Max 4, Plus 5, and Smart 5 have none and ship no `tun` module. ZeroTier's own package installs system-wide and runs as root.

## What Changes

- Add ZeroTier 1.16.2 as a downloaded add-on. The printer fetches ZeroTier's Debian 11 arm64 package from `download.zerotier.com`, verifies its pinned sha256, and keeps only `zerotier-one`.
- Build Linux's `tun` module from the zram modules' pinned kernel tree, with `NET_CORE` and `TUN` added to the configuration, and ship it in the bundle. `scripts/build_zram_modules.py` becomes `scripts/build_kernel_modules.py` and the defconfig `kernel/qidi_kernel_defconfig`; the kernel image and the zram modules are unchanged.
- An add-on's manifest entry can pin kernel modules. They load only on `zram_swap`'s kernel builds; the install refuses any other kernel, and the modules and their loader are installed root-owned under `/usr/local/lib/tltg-addons/<id>/`, which the add-on's unit runs as root before the add-on starts.
- `zerotier-one` runs as `qidi` with only `CAP_NET_ADMIN` and `CAP_NET_RAW`.
- The add-ons service reports ZeroTier's node ID, whether it is online, and its networks, and joins and leaves networks by ID.
- The Fluidd fork's **Add-ons** section gains the ZeroTier panel; its release is pinned with this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: add-ons with kernel modules, the ZeroTier add-on, and ZeroTier's Fluidd panel.

## Impact

- `installer/package.yaml` (`addons:`), `installer/runtime/addons.py`, `models.py`, `manifest.py`, `installer/system/tltg_addons.py`, `installer/system/tun/tun.ko`, `scripts/lock_addon.py`, `scripts/build_kernel_modules.py`, `kernel/`.
- The Fluidd fork's Settings page.
- Release bundles grow by the 64 KB module. Installing ZeroTier needs the printer to be online.
- Devices on a ZeroTier network the printer joins reach its services as devices on the home network do; Moonraker trusts ZeroTier Central's automatic address ranges as part of QIDI's private-range `trusted_clients`.
- ZeroTier's build includes components under ZeroTier's source-available licence, free only for non-commercial use; the docs say so, and the bundle doesn't carry it.
- `docs/addons.md`, `docs/settings.md`, `docs/under-the-hood.md`, `docs/fluidd.md`, `docs/README.md`, `README.md`, `kernel/README.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`.
