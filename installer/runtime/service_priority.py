from __future__ import annotations

from collections.abc import Callable

from .errors import InstallerError


# QIDI runs Klipper, Moonraker, and the touchscreen app at the same CPU priority. Keeping a print alive comes first,
# so Klipper gets the CPU before Moonraker, and both before the touchscreen app.
#
# QIDI's qidi-tuning.service (see cpu_tuning.py) also pinned these processes to CPUs with taskset every 35 s: Klipper
# on CPUs 1-2, and Moonraker, nginx, and ustreamer on CPU 0, with nginx and ustreamer at nice 1 and 2. systemd now
# applies the same placement whenever each service starts, except Moonraker, which also gets CPU 3: on the test Max 4,
# Moonraker on CPU 0 alone was preempted about 45 times a second by the touchscreen app and the camera, and about 18
# times on CPUs 0 and 3, and its status updates reached clients in a median 8.7 ms instead of 12.5 ms. With algo_app
# running on CPU 3 the gain mostly went (about 42 preemptions a second instead of 47), and nothing got worse. The touchscreen
# app keeps the CPU 0 its own start script gives it. algo_app keeps the CPU 3 and nice 19 of QIDI's unit.
KLIPPER_OPERATION = "klipper_priority"
MOONRAKER_OPERATION = "moonraker_priority"
QIDICLIENT_OPERATION = "qidiclient_priority"
NGINX_OPERATION = "nginx_priority"
CROWSNEST_OPERATION = "crowsnest_priority"
ALGO_APP_OPERATION = "algo_app_priority"
SERVICES = {
    KLIPPER_OPERATION: "klipper.service",
    MOONRAKER_OPERATION: "moonraker.service",
    QIDICLIENT_OPERATION: "qidi-client.service",
    NGINX_OPERATION: "nginx.service",
    # ustreamer, the camera stream, runs under crowsnest.
    CROWSNEST_OPERATION: "crowsnest.service",
    ALGO_APP_OPERATION: "algo_app.service",
}
NICE = {
    KLIPPER_OPERATION: -10,
    MOONRAKER_OPERATION: -5,
    QIDICLIENT_OPERATION: 5,
    NGINX_OPERATION: 1,
    CROWSNEST_OPERATION: 2,
    ALGO_APP_OPERATION: 19,
}
CPU_AFFINITY = {
    KLIPPER_OPERATION: "1 2",
    MOONRAKER_OPERATION: "0 3",
    NGINX_OPERATION: "0",
    CROWSNEST_OPERATION: "0",
    ALGO_APP_OPERATION: "3",
}
# Settings apply when the service starts. Restarting algo_app would start it when AI detection is off, so it picks
# them up at its next start; they match QIDI's unit anyway. The camera may be missing, so its restart can fail.
RESTART = {operation: service for operation, service in SERVICES.items() if operation != ALGO_APP_OPERATION}
OPTIONAL_RESTART = frozenset({CROWSNEST_OPERATION})
FILES = {operation: f"/etc/systemd/system/{service}.d/tltg-priority.conf" for operation, service in SERVICES.items()}
# Package-created files start with a marker line so later package versions replace earlier ones.
MARKER = "# tltg-optimized:priority\n"


def content(operation: str) -> str:
    affinity = f"CPUAffinity={CPU_AFFINITY[operation]}\n" if operation in CPU_AFFINITY else ""
    return MARKER + f"""\
# Sets this service's CPU priority and CPUs. Installed by Valve Oil.
[Service]
Nice={NICE[operation]}
{affinity}"""


def patch_dropin(operation: str) -> Callable[[str], str]:
    def patch(current: str) -> str:
        if current == "" or current.startswith(MARKER):
            return content(operation)
        raise InstallerError(f"Existing {FILES[operation]} is unrecognized and was preserved.")
    return patch
