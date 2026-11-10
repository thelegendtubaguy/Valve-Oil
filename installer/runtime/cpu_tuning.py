from __future__ import annotations

from .errors import InstallerError


# QIDI's qidi-tuning.service runs /home/qidi/QIDI_Client/bin/tuning.sh, which pins processes to CPUs with taskset,
# renices nginx and ustreamer, and pins the toolhead serial port's interrupt (ttyS4) to CPU 2 and the USB host
# controllers' (usb2, usb3; the mainboard is on usb2) to CPU 3. The unit is Type=simple with Restart=always around a
# script that exits, so systemd runs it again every 35 s: 8,528 times in 3.5 days on the test Max 4. QIDI's
# irqbalance moves those interrupts again in between, after a Klipper restart within 25 s, putting the USB ones on
# Klipper's CPUs.
#
# The process placement and priorities are now systemd settings in each service's priority drop-in
# (service_priority.py). The qidi-tuning drop-in replaces QIDI's script with one run that pins the same interrupts, and
# the irqbalance drop-in tells irqbalance to leave them alone. A pinned interrupt keeps its CPU when Klipper closes and
# reopens the port.
TUNING_OPERATION = "qidi_tuning_replacement"
IRQBALANCE_OPERATION = "irqbalance_pinned_irqs"
TUNING_UNIT = "qidi-tuning.service"
IRQBALANCE_UNIT = "irqbalance.service"
TUNING_FILE = f"/etc/systemd/system/{TUNING_UNIT}.d/tltg-tuning.conf"
IRQBALANCE_FILE = f"/etc/systemd/system/{IRQBALANCE_UNIT}.d/tltg-tuning.conf"
# Package-created files start with a marker line so later package versions replace earlier ones.
MARKER = "# tltg-optimized:cpu-tuning\n"
# Interrupt number and CPU mask pairs, in shell. The port's interrupt is listed in /proc/interrupts only while it's
# open, so it comes from sysfs; QIDI's script matched the USB controllers by name the same way. systemd reads $$ as $.
_IRQS = (
    'set -- "$$(cat /sys/class/tty/ttyS4/irq 2>/dev/null)" 4; '
    'for irq in $$(grep -E "hcd:usb[23]" /proc/interrupts | cut -d: -f1); do set -- "$$@" $$irq 8; done; '
)
TUNING_CONTENT = MARKER + f"""\
# Replaces QIDI's tuning.sh, which ran again every 35 s, with one run that pins the toolhead serial and USB
# interrupts to CPUs 2 and 3. Each service's tltg-priority.conf sets its CPUs and priority. Installed by Valve Oil.
[Service]
Type=oneshot
RemainAfterExit=yes
Restart=no
ExecStart=
ExecStart=/bin/sh -c '{_IRQS}while [ $$# -ge 2 ]; do [ -n "$$1" ] && echo $$2 > /proc/irq/$$1/smp_affinity; shift 2; done; exit 0'
"""
# QIDI's ExecStart, with the pinned interrupts banned from balancing.
IRQBALANCE_CONTENT = MARKER + f"""\
# Keeps irqbalance from moving the toolhead serial and USB interrupts that qidi-tuning pins. Installed by Valve Oil.
[Service]
ExecStart=
ExecStart=/bin/sh -c '{_IRQS}bans=; while [ $$# -ge 2 ]; do [ -n "$$1" ] && bans="$$bans --banirq=$$1"; shift 2; done; exec /usr/sbin/irqbalance --foreground --policyscript=/etc/irqbalance.d $$bans'
"""
FILES = {TUNING_OPERATION: TUNING_FILE, IRQBALANCE_OPERATION: IRQBALANCE_FILE}
UNITS = {TUNING_OPERATION: TUNING_UNIT, IRQBALANCE_OPERATION: IRQBALANCE_UNIT}
CONTENT = {TUNING_OPERATION: TUNING_CONTENT, IRQBALANCE_OPERATION: IRQBALANCE_CONTENT}


def patch_dropin(operation: str):
    def patch(current: str) -> str:
        if current == "" or current.startswith(MARKER):
            return CONTENT[operation]
        raise InstallerError(f"Existing {FILES[operation]} is unrecognized and was preserved.")
    return patch
