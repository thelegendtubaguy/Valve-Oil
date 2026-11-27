# vo:usb-mount
"""Valve Oil's USB drive mounter, in place of QIDI's touchscreen app's and QIDI's makerbase-automount.

Installed by Valve Oil as /usr/local/lib/vo_usb_mount.py and run as root by vo-usb-mount@<action>-<device>.service,
which Valve Oil's udev rule starts when a partition on a USB disk is added or a USB partition or disk is removed. Valve
Oil's patch of the reviewed touchscreen app builds (installer/runtime/qidiclient_firmware_popup.py) stops the app from
mounting: it takes a drive already mounted at gcodes/USB as ready when udev reports the partition added, rescans it,
and looks for its offline update there. QIDI's makerbase-automount, which mounted on every add even over a mount that
was already there, is switched off by Valve Oil's rule of the same name.

On an add, unless something is already mounted at gcodes/USB or the device is listed in /etc/fstab, this mounts the
partition there as the app did: vfat with the app's options, NTFS through ntfs-3g with none (the app's lookup found no
options for it), and anything else with the filesystem type left to mount. It then repeats udev's add event for the
partition, so the app takes the drive as ready even when it saw the first event before the mount. The repeated event
starts this again, which finds the drive mounted and does nothing. On a remove, it unmounts gcodes/USB when the device
mounted there is the one removed or no longer exists, lazily if the drive was pulled with files open, and removes the
empty mount point, as makerbase did.

Only one drive is mounted at a time, as with the app; the first partition to be mounted stays until it is removed.

APP_BUILDS, filled in at install, says how to tell the patched app: {size: {"site": [file offset, patched bytes as
hex], ...}}. With any other app on disk, as after a QIDI firmware update until Valve Oil installs again, the app
mounts drives itself and this does nothing.
"""
import os
import subprocess
import sys

APP_BUILDS = None

APP = "/home/qidi/QIDI_Client/bin/qidiclient"
MOUNT_POINT = "/home/qidi/printer_data/gcodes/USB"
FSTAB = "/etc/fstab"
NTFS_3G = "/sbin/mount.ntfs-3g"
# The app's options for each filesystem udev reports (ID_FS_TYPE); others mount with "users" and type auto.
VFAT_OPTIONS = "errors=remount-ro,relatime,utf8,flush,dmask=000,fmask=111,uid=qidi,gid=netdev"
NTFS_OPTIONS = "errors=remount-ro,relatime,utf8,users,flush"
HFSPLUS_OPTIONS = "ro,users,relatime"


def log(message):
    print(f"vo_usb_mount: {message}", flush=True)


def app_took_over(app=APP, builds=None):
    """Whether the touchscreen app is a reviewed build whose USB mounting Valve Oil's patch has taken over."""
    builds = APP_BUILDS if builds is None else builds
    try:
        offset, patched = (builds or {})[str(os.stat(app).st_size)]["site"]
        with open(app, "rb") as handle:
            data = os.pread(handle.fileno(), len(patched) // 2, offset)
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return data == bytes.fromhex(patched)


def mount_type(fs_type, *, ntfs_3g=NTFS_3G):
    """(mount type, options) for a filesystem as udev names it, as QIDI's app chose them; options may be empty."""
    if fs_type == "vfat":
        return "vfat", VFAT_OPTIONS
    if fs_type == "ntfs":
        if os.path.exists(ntfs_3g):
            return "ntfs-3g", ""
        return "ntfs", NTFS_OPTIONS
    if fs_type == "hfsplus":
        return "hfsplus", HFSPLUS_OPTIONS
    return "auto", "users"


def device_properties(device, *, run=subprocess.run):
    """udev's properties of a block device, such as ID_BUS, DEVTYPE, and ID_FS_TYPE."""
    result = run(["udevadm", "info", "--query=property", f"--name={device}"], capture_output=True, text=True)
    properties = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key:
            properties[key] = value
    return properties


def in_fstab(device, *, fstab=FSTAB):
    try:
        with open(fstab, encoding="utf-8", errors="replace") as handle:
            return any(line.split()[:1] == [device] for line in handle if not line.lstrip().startswith("#"))
    except OSError:
        return False


def mounted_source(mount_point=MOUNT_POINT, *, mounts="/proc/mounts"):
    """The device mounted at mount_point, or None."""
    source = None
    try:
        with open(mounts, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 1 and fields[1].replace("\\040", " ") == mount_point:
                    source = fields[0]  # the last mount there is the one in use
    except OSError:
        return None
    return source


def add(name, *, run=subprocess.run, properties=None, mount_point=MOUNT_POINT, fstab=FSTAB, mounts="/proc/mounts",
        ntfs_3g=NTFS_3G):
    device = f"/dev/{name}"
    properties = device_properties(device, run=run) if properties is None else properties
    if properties.get("ID_BUS") != "usb" or properties.get("DEVTYPE") != "partition":
        return "not a USB partition"
    if in_fstab(device, fstab=fstab):
        return "listed in /etc/fstab"
    if mounted_source(mount_point, mounts=mounts) is not None:
        return "a drive is already mounted"
    fs_type, options = mount_type(properties.get("ID_FS_TYPE", ""), ntfs_3g=ntfs_3g)
    created = False
    if not os.path.isdir(mount_point):
        os.makedirs(mount_point, mode=0o755, exist_ok=True)
        created = True
    command = ["mount", "-t", fs_type] + (["-o", options] if options else []) + [device, mount_point]
    result = run(command, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"mounting {device} ({fs_type}) failed: {(result.stderr or '').strip()}")
        if created:
            try:
                os.rmdir(mount_point)
            except OSError:
                pass
        return "mount failed"
    log(f"mounted {device} ({fs_type}) at {mount_point}")
    # The touchscreen app takes a mounted drive as ready when udev reports it added.
    run(["udevadm", "trigger", "--action=add", f"/sys/class/block/{name}"], capture_output=True, text=True)
    return "mounted"


def remove(name, *, run=subprocess.run, mount_point=MOUNT_POINT, mounts="/proc/mounts"):
    source = mounted_source(mount_point, mounts=mounts)
    if source is None:
        return "nothing mounted"
    device = f"/dev/{name}"
    removed = source == device or (source.startswith(device) and source[len(device):].isdigit())
    if not removed and os.path.exists(source):
        return "another drive is mounted"
    result = run(["umount", mount_point], capture_output=True, text=True)
    if result.returncode != 0:
        result = run(["umount", "-l", mount_point], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"unmounting {mount_point} failed: {(result.stderr or '').strip()}")
        return "unmount failed"
    log(f"unmounted {source} from {mount_point}")
    try:
        os.rmdir(mount_point)
    except OSError:
        pass
    return "unmounted"


def main(argv):
    if len(argv) != 2 or "-" not in argv[1]:
        log("usage: vo_usb_mount.py add-<device>|remove-<device>")
        return 2
    action, _, name = argv[1].partition("-")
    if not name or "/" in name:
        return 2
    if not app_took_over():
        log(f"{action} {name}: the touchscreen app mounts USB drives itself")
        return 0
    outcome = add(name) if action == "add" else remove(name) if action == "remove" else None
    if outcome is None:
        return 2
    log(f"{action} {name}: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
