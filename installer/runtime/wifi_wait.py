from __future__ import annotations

from .errors import InstallerError
from .naming import has_marker


# QIDI's makerbase-wlan0.service runs wpa_supplicant on wlan0. It binds to the wlan0 device but isn't ordered after
# it, so systemd starts it as soon as boot reaches it, and with Restart=always and RestartSec=1 it fails once a second
# while wlan0 doesn't exist yet. systemd's default limit of 5 starts in 10 s then stops it for good. On the Smart 5 the
# AIC8800 Wi-Fi driver creates wlan0 about 15 s into boot, and after an install the service gave up at 14 s, leaving
# the printer off the network until it was started by hand. The drop-in orders the service after wlan0 appears and
# lifts the start limit, so wpa_supplicant starts once the interface exists however long the driver takes.
#
# The drop-in alone still leaves Wi-Fi off in two cases, on every printer. When wlan0 goes away, as when the driver is
# reloaded (QIDI's wifibt-init.sh loads it on the Max 4 and Plus 5), the binding stops wpa_supplicant and nothing starts
# it when wlan0 comes back. And when wlan0 takes longer than systemd's 90 s wait for the device, the start fails and
# isn't retried. The udev rule makes wlan0 itself want the service, so wpa_supplicant starts whenever wlan0 appears.
# systemd reads no drop-ins for device units, so the rule, not a unit file, carries the dependency.
OPERATION = "wifi_wait_for_wlan0"
RULE_OPERATION = "wifi_start_on_wlan0"
UNIT = "makerbase-wlan0.service"
FILE = f"/etc/systemd/system/{UNIT}.d/vo-wait-for-wlan0.conf"
# Package-created files start with a marker line so later package versions replace earlier ones.
MARKER = "# vo:wifi-wait-for-wlan0\n"
CONTENT = MARKER + """\
# Starts QIDI's wpa_supplicant only once wlan0 exists, and keeps retrying instead of giving up. Installed by Valve Oil.
[Unit]
After=sys-subsystem-net-devices-wlan0.device
StartLimitIntervalSec=0
"""


RULE_FILE = "/etc/udev/rules.d/99-valve-oil-wifi-start.rules"
RULE_MARKER = "# valve-oil:wifi-start-on-wlan0\n"
RULE_CONTENT = RULE_MARKER + f"""\
# Starts QIDI's wpa_supplicant whenever wlan0 appears, as after a driver reload. Installed by Valve Oil.
ACTION=="add", SUBSYSTEM=="net", KERNEL=="wlan0", ENV{{SYSTEMD_WANTS}}+="{UNIT}"
"""


def patch_rule(current: str) -> str:
    if current == "" or has_marker(current, RULE_MARKER):
        return RULE_CONTENT
    raise InstallerError(f"Existing {RULE_FILE} is unrecognized and was preserved.")


def patch_dropin(current: str) -> str:
    if current == "" or has_marker(current, MARKER):
        return CONTENT
    raise InstallerError(f"Existing {FILE} is unrecognized and was preserved.")
