# Recovering a printer that won't boot

[← Back to If something goes wrong](troubleshooting.md)

This page is for a printer whose touchscreen stays on the boot logo, QIDI's or Valve Oil's, and that never joins the network over Wi-Fi or Ethernet, so there's no SSH, Fluidd, or ping. The printer's files, logs, and calibration are almost always still there. These steps get it booting again without wiping it, and save its logs on the way.

Everything here was done on a Max 4 on QIDI firmware `01.01.07.01`. The Plus 5 and Smart 5 use the same host board, but their rescue image has to be built from their own firmware; see [Plus 5 and Smart 5](#plus-5-and-smart-5).

## What you need

- A Windows computer with a USB-A port. A Mac or Linux computer works too, with `rkdeveloptool` in place of RKDevTool.
- A USB-C to USB-A cable, or a USB-C cable with a USB-A adapter. A USB-C to USB-C cable often isn't detected.
- [Android SDK Platform-Tools](https://dl.google.com/android/repository/platform-tools-latest-windows.zip) for `adb`, and the [Google USB Driver](https://developer.android.com/studio/run/win-usb).
- Rockchip's USB driver, [DriverAssistant](https://dl.radxa.com/tools/windows/DriverAssitant_v5.14.zip), and [RKDevTool](https://dl.radxa.com/tools/windows/RKDevTool_Release_v3.37.zip).
- The rescue kit for your printer and QIDI firmware version. Ask for it in a [GitHub issue](https://github.com/thelegendtubaguy/valve-oil/issues/new/choose). It holds:
  - `rk3308_loader_wo_ftl_v2.11.143.bin`, the loader RKDevTool sends to the board
  - `misc-boot-recovery.img` and `misc-clear.img`, which start the rescue system and turn it off again
  - `rescue-recovery.img`, the rescue system
  - `recovery-original.img`, QIDI's recovery partition, to put back at the end
  - `SHA256SUMS`

Take any USB drive or SD card out of the printer before you start. Recovery mode installs an `update.img` it finds on one.

## 1. Try ADB first

Valve Oil 26.11.15.1 and later keep ADB on the USB-C port of the board inside the printer. If the printer gets far enough into its boot, that's enough to get in.

1. Install the Google USB Driver and extract Platform-Tools.
2. Connect the board's USB-C port, marked `USB-OTG`, to a USB-A port on the computer.
3. Open PowerShell in the `platform-tools` folder and run `.\adb.exe devices`.

If the printer is listed, run `.\adb.exe shell` and go to [step 4](#4-save-the-logs), with `/` in place of `/mnt/root`. If Device Manager shows an unknown device, point it at the driver by hand. Choose **Update driver**, **Browse my computer**, **Let me pick**, and **Have Disk**, select `android_winusb.inf`, and pick **Android ADB Interface**.

If nothing shows up at all, the printer stops before it starts ADB. Go on to maskrom.

## 2. Maskrom

Maskrom is the chip's built-in USB mode. It works however broken the system on the eMMC is.

1. Run `DriverInstall.exe` from DriverAssistant as administrator and click **Install Driver**.
2. Extract RKDevTool and run `RKDevTool.exe`.
3. With the printer on and the cable connected, hold the **MODE** button on the board, tap **SOC-RST**, and let go of MODE after a second or two. RKDevTool shows **Found One MASKROM Device**.
4. On **Advanced Function**, select `rk3308_loader_wo_ftl_v2.11.143.bin` as **Boot** and click **Download**. Do this again whenever the board has been reset.

Never use **Upgrade Firmware**, **Upgrade**, **EraseAll**, **Erase Sectors**, or a ticked **Loader** row. They rewrite the board's own bootloader or wipe it.

This loader reads and writes only within the first 32 MB of the eMMC. That covers the partition table, U-Boot, misc, and recovery, but not the system itself. Everything after this goes through the rescue system.

## 3. Start the rescue system

QIDI's recovery partition has its own small Linux system. On the Max 4 it can't see the eMMC, because its device tree waits for a power-management chip the board doesn't have. The rescue image is that same recovery system started with the printer's normal kernel, which does see the eMMC. It also doesn't start QIDI's recovery program, which otherwise reboots the board every few seconds.

1. Load the loader as in [step 2](#2-maskrom).
2. On **Download Image**, untick every row. Then tick only these two:

   | Row | Address | Path |
   | --- | --- | --- |
   | Misc | `0x00004000` | `misc-boot-recovery.img` |
   | Recovery | `0x00004800` | `rescue-recovery.img` |

   If the table has no Misc or Recovery row, right-click it and choose **Add Item**.
3. Tick **Write by Address** and click **Run**. It ends with **Download image OK**.
4. On **Advanced Function**, click **Reset Device**.
5. In PowerShell, run `.\adb.exe wait-for-device shell "cat /proc/partitions"`.

The eMMC is the 29.1 GiB device with six partitions. On the Max 4 it's `mmcblk1`, with misc on `mmcblk1p3`, boot on `mmcblk1p5`, and the system on `mmcblk1p6`. The commands below use those names; change them if yours differ.

## 4. Save the logs

Run `.\adb.exe shell`, then mount the system read-only. `noload` keeps even the filesystem journal from being replayed, so nothing on the eMMC changes:

```sh
mkdir -p /mnt/root && mount -o ro,noload /dev/mmcblk1p6 /mnt/root && echo MOUNTED
```

Type `exit`, and from PowerShell copy the logs to the computer:

```powershell
.\adb.exe shell "cd /mnt/root/home/qidi/printer_data && tar czf /tmp/vo-state.tgz .vo_* .tltg_* config logs/valve-oil-* logs/vo_boot_animation.log logs/tltg_boot_animation.log 2>/dev/null; ls -la /tmp/vo-state.tgz"
.\adb.exe pull /tmp/vo-state.tgz
.\adb.exe pull /mnt/root/home/qidi/printer_data/logs vo-logs
.\adb.exe pull /mnt/root/var/log vo-varlog
```

`vo-varlog` includes the system journal, which these printers keep across reboots. The logs and `config` folder hold the printer's MCU IDs and network addresses, so send them privately rather than attaching them to a public issue.

## 5. Find and fix the problem

Back in `.\adb.exe shell`, check the three things that stop a boot this early:

```sh
ls -ld /mnt/root/sbin /mnt/root/bin /mnt/root/lib
ls -la /mnt/root/usr/sbin/init
e2fsck -n /dev/mmcblk1p6 2>&1 | tail -5
sha256sum /dev/mmcblk1p5
cat /mnt/root/home/qidi/printer_data/.vo_boot_logo.json /mnt/root/home/qidi/printer_data/.tltg_boot_logo.json 2>/dev/null
```

On a working printer:

- `sbin`, `bin`, and `lib` are links: `sbin -> usr/sbin`, `bin -> usr/bin`, and `lib -> usr/lib`.
- `/usr/sbin/init` is a link to `/lib/systemd/systemd`.
- `e2fsck -n` reports no errors.
- The boot partition's sha256 matches `written` in `.vo_boot_logo.json` (`.tltg_boot_logo.json` on releases before 26.11.22.3), or `original` if Valve Oil's boot logo isn't installed.

### `sbin` is a folder instead of a link

This is the cause found so far. QIDI's touchscreen app does it when **Format External Storage** is used on a printer that doesn't have the FAT formatting tools; see [Why `sbin` breaks](#why-sbin-breaks). The folder then holds only `fatlabel`, `fsck.fat`, `mkfs.fat`, and their aliases, and the printer can't find `/sbin/init` when it boots.

```sh
stat /mnt/root/sbin
umount /mnt/root
e2fsck -fy /dev/mmcblk1p6
mount /dev/mmcblk1p6 /mnt/root
mv /mnt/root/sbin /mnt/root/sbin.broken
ln -s usr/sbin /mnt/root/sbin
ls -ld /mnt/root/sbin
ls -la /mnt/root/sbin/init /mnt/root/sbin/start-stop-daemon
sync
```

The `stat` line records when the folder was made, which dates the change in the journal. `e2fsck -fy` checks and repairs the filesystem before anything is written to it. The broken folder is kept as `/sbin.broken`.

### `e2fsck -n` reports errors

Unmount it and repair it:

```sh
umount /mnt/root
e2fsck -fy /dev/mmcblk1p6
```

### The boot partition doesn't match

Valve Oil keeps QIDI's boot partition as `vo_system_backups/boot-partition-<hash>.img` (`tltg_optimized_system_backups/` on releases before 26.11.22.3). Check that the backup's sha256 is the `original` in `.vo_boot_logo.json`, then write it back:

```sh
B=$(ls /mnt/root/home/qidi/printer_data/vo_system_backups/boot-partition-*.img /mnt/root/home/qidi/printer_data/tltg_optimized_system_backups/boot-partition-*.img 2>/dev/null)
sha256sum "$B"
dd if="$B" of=/dev/mmcblk1p5 bs=1M conv=fsync
sha256sum /dev/mmcblk1p5
```

If none of these turns up a problem, send the logs from [step 4](#4-save-the-logs) in a GitHub issue before you change anything else.

## 6. Boot normally again

```sh
umount /mnt/root
dd if=/dev/zero of=/dev/mmcblk1p3 bs=512 count=128 && sync && reboot
```

That clears misc, so the next boot is a normal one. Without the rescue shell, write `misc-clear.img` at `0x00004000` in maskrom the same way.

The printer should show the boot animation, then QIDI's touchscreen, and join the network. Then:

1. SSH in and check `ls -ld /sbin` shows `/sbin -> usr/sbin`.
2. If `/sbin.broken` exists, install QIDI's FAT tools the way dpkg expects, so the touchscreen app never unpacks them over `/sbin` again:

   ```sh
   sudo dpkg -i /home/qidi/QIDI_Client/resource/mkfs_tools/dosfstools_4.2-1_arm64.deb
   ls -ld /sbin && command -v mkfs.fat fsck.fat
   ```

3. Run `~/valve-oil/install.sh` once Klipper is ready, to finish anything an earlier install left undone.

The rescue image can stay on the recovery partition; QIDI's own recovery system doesn't see the eMMC on a Max 4 anyway. To put QIDI's back, write `recovery-original.img` at `0x00004800` in maskrom.

## Why `sbin` breaks

QIDI's system is merged-/usr. `/sbin`, `/bin`, and `/lib` are links into `/usr`, and `/sbin/init`, the first program the kernel starts, is found through the `/sbin` link.

The touchscreen's **Format External Storage** button formats a USB drive as FAT32. Before it does, it looks for `mkfs.vfat` or `mkfs.fat` in `/usr/sbin`, `/sbin`, `/usr/bin`, and `/bin`. When neither is there, it runs `dpkg-deb -x /home/qidi/QIDI_Client/resource/mkfs_tools/dosfstools_4.2-1_arm64.deb /`. `dpkg-deb -x` unpacks the package like an archive, and the package's `sbin` folder replaces the `/sbin` link with a real folder holding only the FAT tools.

The printer keeps running, because everything it needs is already loaded, but the next time Klipper starts it stops with `No library named udev`. The next boot then stops at the logo with no network, whenever it comes: a power cut, a QIDI update, or an installer reboot.

Printers that came with the FAT tools installed never take this path. On those, `/usr/sbin` holds `mkfs.fat` and `/var/lib/dpkg/info/dosfstools.list` exists.

Valve Oil installs QIDI's FAT tools with `dpkg` when they're missing, puts a `/sbin`, `/bin`, or `/lib` folder back to a link when it finds one, and refuses to reboot the printer while `/sbin/init` can't be found. See [The files the printer starts from](install.md#the-files-the-printer-starts-from).

## Plus 5 and Smart 5

The Plus 5 and Smart 5 have the same RK3308 host board, MODE and SOC-RST buttons, and maskrom. Their systems are merged-/usr too, and their touchscreen apps carry the same `dosfstools` package and unpack it the same way, so `/sbin` breaks the same way on one that came without the FAT tools. On the Smart 5, the boot image sits at the same sector, `0xe800`, as on the Max 4. Their rescue image must be built from that printer's own boot and recovery partitions, and whether QIDI's own recovery system sees their eMMC hasn't been checked.

## Building the rescue kit

`scripts/build_rescue_kit.py` builds the images from a working printer on the same QIDI firmware:

```sh
python3 scripts/build_rescue_kit.py --boot boot-partition-<hash>.img --recovery recovery.img --output-dir rescue-kit
```

- `--boot` is QIDI's boot partition. On a printer with Valve Oil, use its backup in `~/printer_data/vo_system_backups/` (`tltg_optimized_system_backups/` on releases before 26.11.22.3). Valve Oil's boot logo doesn't change the kernel or device tree, but QIDI's own copy keeps the kit to stock bytes.
- `--recovery` is `/dev/block/by-name/recovery`, read with `sudo dd`.

The loader is built from Rockchip's [rkbin](https://github.com/rockchip-linux/rkbin) with `tools/boot_merger` and an RK3308 `MINIALL` file that uses `rk3308_ddr_589MHz_uart2_m1_v2.11.bin`, `rk3308_usbplug_wo_ftl_v1.46.bin`, and `rk3308_miniloader_wo_ftl_v1.43.bin`.
