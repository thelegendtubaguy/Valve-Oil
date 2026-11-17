## 1. Kernel module

- [x] 1.1 Build `tun` from the zram modules' tree with `NET_CORE` and `TUN` on; confirm the kernel image differs only in its build ID and the zram modules keep their hashes.
- [x] 1.2 Rename the build script and defconfig; have the script build and check every kernel module the manifest pins.

## 2. Package and runtime

- [x] 2.1 Let an add-on pin kernel modules that load on `zram_swap`'s kernel builds; install them and their loader root-owned, and remove them with the add-on.
- [x] 2.2 Extract a downloaded Debian package's named files; teach `scripts/lock_addon.py` to pin ZeroTier.
- [x] 2.3 Add the ZeroTier definition and unit.
- [x] 2.4 Add ZeroTier's status, join, and leave to the add-ons service.
- [x] 2.5 Extend the add-on lifecycle and service tests.

## 3. Fluidd

- [x] 3.1 Add the ZeroTier panel to the fork.
- [ ] 3.2 Release the fork and pin the build in `installer/package.yaml`.

## 4. Docs

- [x] 4.1 `docs/addons.md`, README, settings, under-the-hood, Fluidd, kernel README, changelog, and third-party notices.
