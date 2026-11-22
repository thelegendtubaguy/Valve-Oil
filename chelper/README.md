# Klipper C helper fixes

Klipper's step generation, serial queues, and homing sync are C code that Klipper loads as `klippy/chelper/c_helper.so`. QIDI's `klippy/chelper` is upstream Klipper commit `ca815f52c8420225f93f90698768a5c4714654a9` (2024-08-09), file for file apart from line endings, and QIDI's `c_helper.so` is that code built on Makerbase's build host. The installer replaces it with the same code built with the patches in this directory, each a backport of a later upstream fix:

| Patch | Upstream | Fix |
|---|---|---|
| [`0001-kin_extruder-free-expired-pa_params.patch`](0001-kin_extruder-free-expired-pa_params.patch) | [`af81d23fe`](https://github.com/Klipper3d/klipper/commit/af81d23fec795ba64882d2206bc9cbc019e4501b) | Frees the pressure advance entries step generation no longer needs. Each `SET_PRESSURE_ADVANCE` that changed the value leaked one. |
| [`0002-trdispatch-signed-timeout-comparison.patch`](0002-trdispatch-signed-timeout-comparison.patch) | [`29d4ec2d6`](https://github.com/Klipper3d/klipper/commit/29d4ec2d6d840ee839333306003a400da186d41d) | Extends a board's homing timeout only to a later time. A signed difference compared with an unsigned threshold sent an earlier one. |
| [`0003-stepcompress-out-of-bounds-write.patch`](0003-stepcompress-out-of-bounds-write.patch) | [`56a9c605e`](https://github.com/Klipper3d/klipper/commit/56a9c605e121c6560f5c8b6c0e33bdda86de69a1) | Grows the step queue for a step far in the future instead of writing past its end. |
| [`0004-serialqueue-close-pipe-fds.patch`](0004-serialqueue-close-pipe-fds.patch) | [`6d43f8b3d`](https://github.com/Klipper3d/klipper/commit/6d43f8b3ddbfab679d1a64cb6f9f7adbe851ee82) | Closes the two pipe descriptors of each serial queue Klipper frees, at every board disconnect, restart, and failed connection attempt. |

The first two apply to QIDI's code unchanged. Upstream's step queue and serial queue changed after QIDI's snapshot, so the last two make the same change to QIDI's version of the code.

Every other upstream change to `klippy/chelper` since QIDI's snapshot belongs to the 2025 step generation and serial queue rework, fixes a bug that rework introduced, or is for kinematics these printers don't use. Upstream `867d73f0b` keeps commands further inside a board's 32-bit clock range; Valve Oil's [fan and pin fix](../docs/fixes.md#fans-and-lights-switching-at-the-wrong-time) already holds those commands to 3 seconds ahead.

## How the installer uses it

[`installer/runtime/klipper_chelper.py`](../installer/runtime/klipper_chelper.py) lists QIDI's library by SHA-256 with the package's build, and both files are in [`installer/system/klipper_chelper/`](../installer/system/klipper_chelper/). Install and update back up QIDI's library and rename the package's into place, which leaves Klipper running on the old file until the restart that follows. A library with any other hash is left alone. Uninstall puts QIDI's back.

QIDI's `.c` and `.h` files stay as they are. Klipper rebuilds `c_helper.so` at startup only when one of them, or `chelper/__init__.py`, is newer than the library, and writing or restoring the library never leaves it older than them, so Klipper loads it as it is. Patching the sources as well would make that depend on the order files are written and restored.

## Rebuild and check

With Docker on a development computer, from the repository root:

```bash
python3 scripts/build_klipper_chelper.py --output-dir dist/klipper-chelper
```

The output directory must not exist. The script fetches upstream Klipper at `ca815f52c`, applies these patches to a copy, and builds both trees in an arm64 Debian bullseye container with the packages QIDI's printers have installed, `gcc-10 10.2.1-6`, `libc6-dev 2.31-13+deb11u7`, and `linux-libc-dev 5.10.205-2`, from `snapshot.debian.org`. It uses `klippy/chelper/__init__.py`'s compiler flags and source order. The library's debug information records the build directory, which QIDI's names as `/home/mks/klipper`, and the source paths, so the script builds there with absolute paths.

It prints both hashes and exits non-zero unless the unpatched build is QIDI's library byte for byte and the patched build matches `installer/runtime/klipper_chelper.py`. After an intentional change to the patches, `--install` copies both libraries into `installer/system/klipper_chelper/`; update the patched hash and size in `installer/runtime/klipper_chelper.py` from its output.

## Valve Oil's Klipper

[`klipper/`](../klipper/) is upstream Klipper v0.14 with QIDI's changes, which installs beside QIDI's tree (see [`klipper/README.md`](../klipper/README.md)). Its `klippy/chelper` is v0.14's, with one addition, `trapq_get_queued_end`, which QIDI's Box code needs. QIDI's printers have no compiler, so the library ships prebuilt as [`installer/system/klipper_tree/c_helper.so`](../installer/system/klipper_tree/c_helper.so). `scripts/build_klipper_chelper.py` builds it in the same container run, from `klipper/klippy/chelper` with that tree's source list and flags, in `/home/qidi/vo-klipper` where the installer puts it, and checks it, and a digest of its sources, against [`installer/runtime/klipper_tree.py`](../installer/runtime/klipper_tree.py). After changing those sources, run it with `--install` and update the hash, size and sources digest there from its output. The installer writes the library after the sources, so it is the newest file and Klipper loads it as it is.
