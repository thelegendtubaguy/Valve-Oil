# Kernel modules

QIDI's Max 4, Plus 5, and Smart 5 kernels can swap but ship without zram, so the installer's [zram swap](../docs/install.md#os-optimizations) brings its own modules: `lz4_compress`, `lz4_decompress`, `lz4`, `zsmalloc`, and `zram`. They are committed in [`installer/system/zram/`](../installer/system/zram/), and [`installer/package.yaml`](../installer/package.yaml) (`system_optimizations.zram_swap`) records their SHA-256 hashes, load order, and the kernel builds they load on:

```text
5.10.160 #4 SMP PREEMPT Wed Sep 3 15:21:57 CST 2025    (Max 4)
5.10.160 #3 SMP PREEMPT Sat Aug 23 12:12:00 CST 2025   (Plus 5)
5.10.160 #15 SMP PREEMPT Fri Apr 3 15:46:16 CST 2026   (Smart 5)
```

Both the installer and the boot script compare `/proc/sys/kernel/osrelease` and `/proc/sys/kernel/version` with those builds and load nothing on any other kernel.

The kernels have no TUN device either, which the [ZeroTier add-on](../docs/addons.md#zerotier) needs, so it brings the `tun` module, built from the same tree and configuration. It is committed in [`installer/system/tun/`](../installer/system/tun/), and the add-on's entry under `addons:` in `installer/package.yaml` records its hash. It loads on the same kernel builds: the add-on's install refuses any other kernel, and the script its service runs as root before ZeroTier starts loads nothing on one.

The modules are built for `#4`. The Plus 5's `#3` was checked against it from both printers' boot partitions and `/proc/kallsyms`. The decompressed `Image` files are the same size and differ in 173 bytes, all from the build string: the version banner and string, the build ID, and data and instructions shifted by the one-character-longer date. The built-in symbols, addresses included, and the 9,593 exported symbols are identical, and every symbol the modules import is exported. A QIDI-built Wi-Fi module with the same hash loads on both kernels and has the modules' 0x300-byte `struct module`.

The Smart 5's `#15`, on QIDI firmware `01.01.01.03` and `01.01.02.01`, was checked against the Plus 5's `#3` the same way. Its decompressed `Image` is the same size and differs in 76 bytes: the version banner, the version string, and the 20-byte build ID. The date strings are the same length, so nothing shifts, and the built-in symbols in `/proc/kallsyms`, all 42,585 of them, have the same names and addresses.

## Rebuild and check

With Docker on a development computer, from the repository root:

```bash
python3 scripts/build_kernel_modules.py --output-dir dist/kernel-modules
```

The output directory must not exist. The script builds in an amd64 Debian container from pinned inputs:

- Kernel source: [armbian/linux-rockchip](https://github.com/armbian/linux-rockchip) commit `709c51c64e1652d4f8c87b1815db86f56d188268` (branch `rk-5.10-rkr6`, Linux 5.10.160).
- Toolchain: Arm GNU Toolchain 10.3-2021.07 `aarch64-none-linux-gnu`, the compiler `/proc/version` names on the printer, checked against its SHA-256.
- Configuration: [`qidi_kernel_defconfig`](qidi_kernel_defconfig).

It strips debug information, prints each module's hash, and exits non-zero when any differs from `installer/package.yaml`. Each module's build ID hashes its debug information, which records the build paths, so the script always builds under `/w`. After an intentional change, `--install` copies the new modules to their paths under `installer/system/`; update the hashes in `installer/package.yaml` from its output.

## How the configuration matches QIDI's kernel

QIDI publishes neither its kernel source nor its configuration, and the printer has no `/proc/config.gz`. The configuration was rebuilt from the printer:

1. Start from Rockchip's `rk3308_linux_defconfig`, build built-in what it builds as modules, and add what the printer's `/proc/kallsyms` shows: swap, cgroups (`cpu`, `cpuacct`, `cpuset`, `devices`, `freezer`) with CFS bandwidth, CMA and `ZONE_DMA32`, CAN with `gs_usb`, SCSI and USB storage, the USB host controllers, USB serial (`ch341`), ACM, UVC, the DVB core, FUSE, autofs, the Goodix touchscreen, and dynamic debug. Tuners and DVB front ends are off.
2. Build `vmlinux` and compare its code symbols with `/proc/kallsyms`. With this configuration only three of about 37,000 differ, and the kernel image is within 66 bytes of the `Image` in the printer's boot partition (`/dev/block/by-name/boot`, an Android boot image holding an LZ4-compressed `Image`).
3. Turn on `BLK_DEV`, `ZSMALLOC`, `ZRAM`, `CRYPTO_LZ4`, and `CRYPTO_LZO` as modules. The kernel image stays byte-identical.
4. Turn on `NET_CORE`, which only opens the network driver menu, and `TUN` as a module. The kernel image stays byte-identical apart from its 20-byte build ID, which hashes the debug information, and the zram modules build to the same hashes.

Modules depend on more than symbol names, so the build was checked against the code QIDI's kernel runs:

- The kernel has no `MODVERSIONS`; loading checks only the vermagic `5.10.160 SMP preempt mod_unload aarch64`, which matches.
- Every symbol the modules import is exported by QIDI's kernel, read from `__ksymtab_strings` in its `Image`.
- The block-layer, gendisk, bio, crypto, slab, page-allocator, and swap functions zram calls compile to the same instructions as in QIDI's `Image`, apart from relocated addresses, so the structure layouts they share agree.
- `struct module` has the same size and `init` and `exit` offsets as a module QIDI built for this kernel.
- This Android-derived tree counts `NR_ZSPAGES` whether or not zsmalloc is configured, and the printer's `/proc/vmstat` has `nr_zspages`, so zsmalloc's page accounting matches.

On the test printer, the loaded modules round-tripped 64 MB of real printer files through `/dev/zram0` byte for byte with both lz4 and lzo-rle.

The `tun` module imports only symbols the kernel built from this configuration exports, which `modpost` checks against `vmlinux` when it builds. On the Plus 5 it loaded without a kernel warning, created `/dev/net/tun`, and ZeroTier created and configured its network interface through it.
