from __future__ import annotations

from .errors import InstallerError
from .naming import has_marker


# QIDI's system image runs ADB from usbdevice.service, Rockchip's USB gadget script (/usr/bin/usbdevice). The script
# loads /etc/profile, whose /etc/profile.d/adbd.sh exports ADB_TCP_PORT=5555, and adbd then listens as root on every
# network the printer joins, with no authentication, besides serving the board's USB OTG port. adbd opens no network
# port when ADB_TCP_PORT is unset, so this file, read after QIDI's in /etc/profile.d, unsets it: ADB stays on the USB
# port, for recovery with a cable, and is off the network. Rockchip's /etc/usbdevice.d hooks aren't usable instead:
# the dash script sources them by bare name.
OPERATION = "adb_usb_only"
SERVICE = "usbdevice.service"
FILE = "/etc/profile.d/valve-oil-adb-usb-only.sh"
# Package-created files start with a marker line so later package versions replace earlier ones.
MARKER = "# vo:adb-usb-only\n"
CONTENT = MARKER + """\
# Keeps QIDI's ADB (usbdevice.service) on the USB port and off the network: adbd listens on TCP only when
# ADB_TCP_PORT is set, which /etc/profile.d/adbd.sh does. Installed by Valve Oil.
unset ADB_TCP_PORT
"""
# Service operations earlier package versions applied and this one no longer does. Install and update undo them once
# this operation's file is in place, and old ledgers that still hold them stay valid for restoration.
RETIRED_SERVICE_OPERATIONS = {f"service_{SERVICE}": OPERATION}


def patch_profile(current: str) -> str:
    if current == "" or has_marker(current, MARKER):
        return CONTENT
    raise InstallerError(f"Existing {FILE} is unrecognized and was preserved.")
