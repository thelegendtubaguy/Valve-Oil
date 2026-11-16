from __future__ import annotations

from .errors import InstallerError


# QIDI's makerbase-wlan0.service runs wpa_supplicant on wlan0. It binds to the wlan0 device but isn't ordered after
# it, so systemd starts it as soon as boot reaches it, and with Restart=always and RestartSec=1 it fails once a second
# while wlan0 doesn't exist yet. systemd's default limit of 5 starts in 10 s then stops it for good. On the Smart 5 the
# AIC8800 Wi-Fi driver creates wlan0 about 15 s into boot, and after an install the service gave up at 14 s, leaving
# the printer off the network until it was started by hand. The drop-in orders the service after wlan0 appears and
# lifts the start limit, so wpa_supplicant starts once the interface exists however long the driver takes.
OPERATION = "wifi_wait_for_wlan0"
UNIT = "makerbase-wlan0.service"
FILE = f"/etc/systemd/system/{UNIT}.d/tltg-wait-for-wlan0.conf"
# Package-created files start with a marker line so later package versions replace earlier ones.
MARKER = "# tltg-optimized:wifi-wait-for-wlan0\n"
CONTENT = MARKER + """\
# Starts QIDI's wpa_supplicant only once wlan0 exists, and keeps retrying instead of giving up. Installed by Valve Oil.
[Unit]
After=sys-subsystem-net-devices-wlan0.device
StartLimitIntervalSec=0
"""


def patch_dropin(current: str) -> str:
    if current == "" or current.startswith(MARKER):
        return CONTENT
    raise InstallerError(f"Existing {FILE} is unrecognized and was preserved.")
