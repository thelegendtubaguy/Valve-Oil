"""A fake printer for the USB hand-off's shell scripts (installer/usb/): QIDI's update folder, printer_data, a mounted
drive, and stand-ins for dpkg, dpkg-query, systemctl, systemd-run, and chown on PATH.

`offline_update` does what QIDI's offline update does with a printer zip from the drive (empty the update folder, unzip
it there, copy the SOC file to qidi.deb) and runs the package's postinst as dpkg would. `launch` then runs the unit the
postinst started, as systemd would, with the interpreter it execs recorded instead of run, and the starting screen's
drawing recorded instead of drawn.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from installer.tests.helpers import _JsonResponse, moonraker_http
from scripts import usb_drive_image

FAKE_COMMANDS = {
    # Installed versions live in $FAKE/version-<package>; the hand-off package is installed while $FAKE/handoff exists.
    "dpkg-query": """#!/bin/sh
echo "dpkg-query $*" >> "$FAKE/calls"
package=$(eval echo \\${$#})
case "$*" in
  *Version*) [ -f "$FAKE/version-$package" ] && exec cat "$FAKE/version-$package"; exit 1 ;;
  *Status*) [ -f "$FAKE/handoff" ] && { printf 'install ok installed'; exit 0; }; exit 1 ;;
esac
exit 1
""",
    "dpkg": """#!/bin/sh
echo "dpkg $*" >> "$FAKE/calls"
rm -f "$FAKE/handoff"
""",
    # The app's InvocationID is $FAKE/invocation; it is always active.
    "systemctl": """#!/bin/sh
echo "systemctl $*" >> "$FAKE/calls"
case "$1" in
  show) cat "$FAKE/invocation" ;;
  is-active) echo active ;;
esac
exit 0
""",
    "systemd-run": """#!/bin/sh
echo "systemd-run $*" >> "$FAKE/calls"
for argument in "$@"; do printf '%s\\n' "$argument"; done > "$FAKE/unit"
exit 0
""",
    "chown": """#!/bin/sh
exit 0
""",
    "fake-python": """#!/bin/sh
for argument in "$@"; do printf '%s\\n' "$argument"; done > "$FAKE/exec"
""",
    # Draws the starting screen: records the image and exits with $FAKE/screen-exit (0 when absent).
    "fake-screen": """#!/bin/sh
echo "draw ${5:-}" >> "$FAKE/calls"
exit "$(cat "$FAKE/screen-exit" 2>/dev/null || echo 0)"
""",
}


STARTING_PNG = b"starting screen"


def make_bundle(path: Path, members: dict[str, bytes] | None = None, *, symlink: str | None = None) -> Path:
    """A small bundle archive with valve-oil/install.sh and the bootstrap the launcher runs."""
    members = members if members is not None else {
        "valve-oil/install.sh": b"#!/bin/sh\n",
        "valve-oil/installer/runtime/bootstrap.py": b"raise SystemExit(0)\n",
    }
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(info)
    return path


class FakePrinter:
    def __init__(self, root: Path, *, printer_id: str = "max4", system_package: str = "qd-max4-system",
                 qidi_version: str | None = "01.01.07.01", drive_name: str = "USB drive"):
        self.root = root
        self.printer_id = printer_id
        self.home = root / "home/qidi"
        self.printer_data = self.home / "printer_data"
        self.update = self.home / "update"
        # A space in the mount point checks that the mount table's octal escapes are undone.
        self.drive = self.printer_data / "gcodes" / drive_name
        self.fake = root / "fake"
        self.bin = self.fake / "bin"
        self.run_dir = root / "run/valve-oil-usb"
        self.sudoers = root / "etc/sudoers.d"
        self.tmpfiles = root / "etc/tmpfiles.d"
        for directory in (self.printer_data / "logs", self.update, self.drive / "QD_Update", self.drive / "valve-oil",
                          self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        for name, script in FAKE_COMMANDS.items():
            (self.bin / name).write_text(script, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        if qidi_version is not None:
            (self.fake / f"version-{system_package}").write_text(qidi_version, encoding="utf-8")
        (self.fake / "invocation").write_text("before\n", encoding="utf-8")
        mount_point = str(self.drive).replace(" ", "\\040")
        (self.fake / "mounts").write_text(f"/dev/root / ext4 rw 0 0\n/dev/sda1 {mount_point} vfat rw 0 0\n",
                                          encoding="utf-8")

    @property
    def log(self) -> Path:
        return self.printer_data / "logs/valve-oil-usb-handoff.log"

    @property
    def status(self) -> dict[str, str]:
        path = self.home / ".cache/valve-oil/usb/restore"
        return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())

    @property
    def touchscreen_stopped(self) -> Path:
        """The launcher's record that it stopped the app for its starting screen."""
        return self.home / ".cache/valve-oil/usb/touchscreen-stopped"

    def cleanup(self) -> None:
        """The unit's ExecStopPost."""
        subprocess.run(self.unit_command()[:2] + ["cleanup"], env=self.env(), check=True)

    @property
    def cached_bundle(self) -> Path:
        return self.home / ".cache/valve-oil/usb/valve-oil.tar.gz"

    def calls(self) -> list[str]:
        path = self.fake / "calls"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def env(self, **extra: str) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE": str(self.fake),
                "VO_USB_UPDATE_DIR": str(self.update), "VO_USB_PRINTER_DATA": str(self.printer_data),
                "VO_USB_MOUNTS": str(self.fake / "mounts"), "VO_USB_RUN_DIR": str(self.run_dir),
                "VO_USB_SUDOERS_DIR": str(self.sudoers), "VO_USB_TMPFILES_DIR": str(self.tmpfiles),
                "VO_USB_PYTHON": str(self.bin / "fake-python"), "VO_USB_SCREEN_PYTHON": str(self.bin / "fake-screen"),
                **extra}

    def put_on_drive(self, zip_name: str, printer_zip: bytes, bundle: Path) -> None:
        (self.drive / "QD_Update" / zip_name).write_bytes(printer_zip)
        shutil.copy2(bundle, self.drive / "valve-oil/valve-oil.tar.gz")

    def offline_update(self, zip_name: str) -> subprocess.CompletedProcess:
        """QIDI's offline update of the drive's zip, up to dpkg's run of the package's postinst."""
        shutil.rmtree(self.update)
        self.update.mkdir()
        with zipfile.ZipFile(self.drive / "QD_Update" / zip_name) as archive:
            archive.extractall(self.update)
        soc = json.loads((self.update / "firmware_manifest.json").read_text(encoding="utf-8"))["SOC"]["file"]
        shutil.copy2(self.update / soc, self.update / "qidi.deb")
        members = usb_drive_image.read_deb((self.update / "qidi.deb").read_bytes())
        with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as control:
            postinst = control.extractfile("./postinst").read()
        script = self.fake / "postinst"
        script.write_bytes(postinst)
        (self.fake / "handoff").write_text("", encoding="utf-8")
        return subprocess.run(["/bin/sh", str(script), "configure"], env=self.env(), capture_output=True, text=True,
                              timeout=60)

    def stage_launch(self, bundle: Path) -> None:
        """What a postinst that restored the update folder and copied `bundle` leaves for the launcher."""
        self.cached_bundle.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle, self.cached_bundle)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(usb_drive_image.USB_SOURCE / "launch.sh", self.run_dir / "launch.sh")
        shutil.copy2(usb_drive_image.UPDATE_SCREEN, self.run_dir / "update_screen.py")
        (self.run_dir / "starting.png").write_bytes(STARTING_PNG)
        (self.fake / "handoff").write_text("", encoding="utf-8")
        sha256 = hashlib.sha256(bundle.read_bytes()).hexdigest()
        (self.fake / "unit").write_text("\n".join(["/bin/sh", str(self.run_dir / "launch.sh"), "launch",
                                                   str(self.printer_data), str(self.drive), sha256, "before", ""]),
                                        encoding="utf-8")

    def unit_command(self) -> list[str]:
        """The command the postinst's systemd-run started, after systemd-run's own options."""
        arguments = (self.fake / "unit").read_text(encoding="utf-8").split("\n")[:-1]
        return arguments[arguments.index("/bin/sh"):]

    def unit_options(self) -> list[str]:
        arguments = (self.fake / "unit").read_text(encoding="utf-8").split("\n")[:-1]
        return arguments[:arguments.index("/bin/sh")]

    def launch(self, *, moonraker_url: str, restarted: bool = True, **env: str) -> subprocess.CompletedProcess:
        """Run the unit the postinst started, as systemd would."""
        if restarted:
            (self.fake / "invocation").write_text("after\n", encoding="utf-8")
        settings = {"VO_USB_MOONRAKER_URL": moonraker_url, "VO_USB_POLL": "0.1", "VO_USB_CLIENT_TIMEOUT": "0",
                    "VO_USB_KLIPPER_TIMEOUT": "5", "VO_USB_KLIPPER_SETTLE": "0.2", "VO_USB_DPKG_TIMEOUT": "0", **env}
        return subprocess.run(self.unit_command(), env=self.env(**settings), capture_output=True, text=True,
                              timeout=60)

    def exec_arguments(self) -> list[str] | None:
        path = self.fake / "exec"
        return path.read_text(encoding="utf-8").split("\n")[:-1] if path.exists() else None


def moonraker(state: str = "ready"):
    """Moonraker's /printer/info with Klipper in `state`; yields its base URL."""
    return moonraker_http(lambda request, timeout=0: _JsonResponse({"result": {"state": state}}))
