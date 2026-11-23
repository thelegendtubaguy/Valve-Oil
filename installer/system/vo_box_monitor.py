# vo:box-monitor
"""Valve Oil's Box monitor: writes box.cfg for the USB QIDI Boxes connected, and restarts Klipper for them when idle.

Installed by Valve Oil as /usr/local/lib/vo_box_monitor.py and run by vo-box-monitor.service, which a udev rule
starts when a USB Box connects or disconnects, and which also runs once at boot. Valve Oil's patch of QIDI's touchscreen
app stops the app from writing box.cfg (installer/runtime/qidiclient_firmware_popup.py); APP_BUILDS, filled in at
install, says where that patch is in each reviewed build, and with any other app the monitor does nothing.

Once the Boxes found have stayed the same for SETTLE_SECONDS, which outlasts a Box board reset, the monitor writes
box.cfg in the layout QIDI's app writes, only when the file would change, and restarts Klipper only while no print runs
or is paused and no heater has a target. Each Box keeps its number from a record in printer_data, seeded from box.cfg,
so moving a Box to another USB port doesn't renumber the Boxes. It waits while an installer run holds its lock, a QIDI
firmware update runs, or a board operation awaits its rescue, and while the printer is busy it says so in a status file
Klipper's printer warnings read.

The installer's firmware update loads this file to write box.cfg after QIDI's package resets it (box_config.py).
"""
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request

APP_BUILDS = None

PRINTER_DATA = "/home/qidi/printer_data"
APP = "/home/qidi/QIDI_Client/bin/qidiclient"
SERIAL_BY_ID = "/dev/serial/by-id"
MOONRAKER = "http://127.0.0.1:7125"
BOX_CFG = "config/box.cfg"
# Box serial (its /dev/serial/by-id path) to number, from 1. box.cfg lists the first MAX_BOXES connected.
RECORD = ".vo_box_numbers.json"
# A Box change waiting for the printer to be idle, for Klipper's printer warnings.
STATE = ".vo_box_monitor.json"
# box.cfg as it was before the monitor last wrote it.
PREVIOUS = ".vo_box_cfg.previous"
INSTALLER_LOCK = ".vo_installer.lock"
# A firmware update, or a board operation that has to be rescued, owns the boards until it ends.
WAIT_FOR = (".vo_firmware_update_pending.json", ".vo_recovery_required",
            ".vo_thr_firmware_pending.json", ".vo_box_firmware_pending.json",
            ".vo_mainboard_firmware_pending.json")
SCHEMA = 1
MAX_BOXES = 5
SETTLE_SECONDS = 5.0
POLL_SECONDS = 0.5
WAIT_SECONDS = 10.0
# USB devices are still appearing this soon after boot.
BOOT_SECONDS = 30.0
NO_BOXES = "# No Box Config\n"
# QIDI's touchscreen app writes box.cfg from these, with {B} the Box's index from 0, {M} its number from 1, and
# {SLOTS} its four slots.
BOX_TEMPLATE = """\
[box_config box{B}]
runout_pin_0: mcu_box{M}:PA0
step_pin_0: mcu_box{M}:PC14
dir_pin_0: mcu_box{M}:PC13
enable_pin_0: !mcu_box{M}:PC15
white_pin_0: mcu_box{M}:PA1
red_pin_0: mcu_box{M}:PA2
runout_pin_1: mcu_box{M}:PB3
step_pin_1: mcu_box{M}:PB9
dir_pin_1: mcu_box{M}:PB8
enable_pin_1: !mcu_box{M}:PC0
white_pin_1: mcu_box{M}:PB4
red_pin_1: mcu_box{M}:PB5
runout_pin_2: mcu_box{M}:PA13
step_pin_2: mcu_box{M}:PC12
dir_pin_2: mcu_box{M}:PC11
enable_pin_2: !mcu_box{M}:PD2
white_pin_2: mcu_box{M}:PA14
red_pin_2: mcu_box{M}:PA15
runout_pin_3: mcu_box{M}:PA7
step_pin_3: mcu_box{M}:PC8
dir_pin_3: mcu_box{M}:PB2
enable_pin_3: !mcu_box{M}:PC10
white_pin_3: mcu_box{M}:PC4
red_pin_3: mcu_box{M}:PC5
microsteps: 16
rotation_distance: 13.6
step_pulse_duration:0.000000100
heater_pin_heater_generic: mcu_box{M}:PA3
sensor_type_heater_generic: AHT20_F
i2c_bus_heater_generic: i2c3
i2c_mcu_heater_generic: mcu_box{M}
i2c_address_heater_generic: 56
control_heater_generic: pid
pid_Kp_heater_generic: 63.418
pid_Ki_heater_generic: 1.342
pid_Kd_heater_generic: 749.125
min_temp_heater_generic: -100
max_temp_heater_generic: 100
target_max_temp_heater_generic: 90
max_error_verify_heater: 400
check_gain_time_verify_heater: 600
is_box_heater_verify_heater: True
sensor_type_temperature_sensor_0: NTC 100K MGB18-104F39050L32
sensor_pin_temperature_sensor_0: mcu_box{M}:PC1
min_temp_temperature_sensor_0: -100
max_temp_temperature_sensor_0: 130
sensor_type_temperature_sensor_1: NTC 100K MGB18-104F39050L32
sensor_pin_temperature_sensor_1: mcu_box{M}:PC2
min_temp_temperature_sensor_1: -100
max_temp_temperature_sensor_1: 130
pin_box_heater_fan_0: mcu_box{M}:PA4
heater_box_heater_fan_0: heater_box{M}
heater_temp_box_heater_fan_0: 35
stepper_box_heater_fan_0:
idle_timeout_box_heater_fan_0: 60
pin_box_heater_fan_1: mcu_box{M}:PA5
heater_box_heater_fan_1: heater_box{M}
heater_temp_box_heater_fan_1: 35
stepper_box_heater_fan_1:
idle_timeout_box_heater_fan_1: 60
pin_controller_fan: mcu_box{M}:PA6
heater_controller_fan: heater_box{M}
stepper_controller_fan: {SLOTS}
cs_pin_box_rfid_0: mcu_box{M}:PC6
cs_pin_box_rfid_1: mcu_box{M}:PC7

"""
EXTRAS_TEMPLATE = """\
[box_extras]
b_button_pin : ^mcu_box{N}:PB1
b_endstop_pin : mcu_box{N}:PA9
e_endstop_pin : mcu_box{N}:PA10

[box_autofeed]
limit_pin:^!mcu_box{N}:PB0
v_feed: 100
lmax: 120
dir: 0

"""
MCU_TEMPLATE = "[mcu mcu_box{M}]\nserial: {SERIAL}\nrestart_method : command\n\n"


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def generate(serials):
    """box.cfg for the Boxes in order, each a /dev/serial/by-id path, as QIDI's touchscreen app writes it."""
    serials = list(serials)[:MAX_BOXES]
    if not serials:
        return NO_BOXES
    text = "".join(BOX_TEMPLATE.replace("{B}", str(index)).replace("{M}", str(index + 1)).replace(
        "{SLOTS}", ", ".join("slot%d" % (4 * index + slot) for slot in range(4))) for index in range(len(serials)))
    text += EXTRAS_TEMPLATE.replace("{N}", str(len(serials)))
    return text + "".join(MCU_TEMPLATE.replace("{M}", str(number)).replace("{SERIAL}", serial)
                          for number, serial in enumerate(serials, 1))


def parse(text):
    """The Boxes, in order, of a box.cfg in QIDI's layout, or None for a file in another layout, which is left alone.
    A file of comments only, such as the one QIDI's firmware package installs, lists no Box."""
    if all(not line.strip() or line.lstrip().startswith("#") for line in text.splitlines()):
        return []
    serials, number = [], None
    for line in text.splitlines():
        if line.startswith("[mcu mcu_box") and line.endswith("]"):
            number = line[len("[mcu mcu_box"):-1]
        elif number is not None and line.startswith("serial: "):
            if number != str(len(serials) + 1):
                return None
            serials.append(line[len("serial: "):])
            number = None
    return serials if serials and generate(serials) == text else None


def listed(text):
    """Each Box's number in a box.cfg of any layout QIDI's touchscreen apps have written: [mcu mcu_box<N>] sections
    with their serial."""
    numbers, number = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            name = line[1:-1].strip() if line.endswith("]") else ""
            digits = name[len("mcu mcu_box"):] if name.startswith("mcu mcu_box") else ""
            number = int(digits) if digits.isdigit() and int(digits) >= 1 else None
        elif number is not None and line.replace(" ", "").startswith("serial:"):
            serial = line.split(":", 1)[1].strip()
            if serial and serial not in numbers and number not in numbers.values():
                numbers[serial] = number
            number = None
    return numbers


def is_box(name):
    """Whether a /dev/serial/by-id name is a USB QIDI Box, as the touchscreen app tells: a Klipper device whose USB
    model starts with the Box board's number or names a QIDI Box."""
    prefix = "usb-Klipper_"
    return name.startswith(prefix) and (name[len(prefix):].startswith("02.03") or "QIDI_BOX_V" in name)


def connected(by_id=SERIAL_BY_ID):
    try:
        names = os.listdir(by_id)
    except OSError:
        return []
    return sorted(os.path.join(by_id, name) for name in names if is_box(name))


def number_boxes(record, boxes):
    """The record with every connected Box numbered, and the connected Boxes in number order. A Box keeps its number;
    a new one takes the lowest number no connected Box has, which a disconnected Box gives up."""
    numbers = dict(record)
    used = {numbers[box] for box in boxes if box in numbers}
    for box in sorted(box for box in boxes if box not in numbers):
        number = next(number for number in range(1, len(boxes) + 2) if number not in used)
        numbers = {other: taken for other, taken in numbers.items() if taken != number}
        numbers[box] = number
        used.add(number)
    return numbers, sorted(boxes, key=numbers.get)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else None


def _write(path, text, mode=0o644):
    temporary = "%s.%d.tmp" % (path, os.getpid())
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def load_record(printer_data):
    """The recorded Box numbers, or None when there is no valid record."""
    value = _read_json(os.path.join(printer_data, RECORD))
    boxes = value.get("boxes") if value else None
    if not isinstance(boxes, dict) or not all(
            isinstance(box, str) and isinstance(number, int) and number >= 1
            for box, number in boxes.items()) or len(set(boxes.values())) != len(boxes):
        return None
    return boxes


def save_record(printer_data, numbers):
    # Box serial numbers stay on the printer, readable by its own user only.
    _write(os.path.join(printer_data, RECORD), json.dumps({"schema": SCHEMA, "boxes": numbers}, sort_keys=True) + "\n",
           mode=0o600)


def seed(printer_data):
    """The Box record, taken from box.cfg when there is none yet, so the Boxes keep the numbers they have."""
    record = load_record(printer_data)
    if record is None:
        record = listed(read_text(os.path.join(printer_data, BOX_CFG)))
        save_record(printer_data, record)
    return record


def plan(printer_data, boxes):
    """(the record, box.cfg as it is, box.cfg for these Boxes, or None to leave a file in another layout)."""
    record, ordered = number_boxes(seed(printer_data), boxes)
    current = read_text(os.path.join(printer_data, BOX_CFG))
    return record, current, (generate(ordered) if parse(current) is not None else None)


def write_config(printer_data, boxes):
    """Write box.cfg for these Boxes when it would change, and return whether it did."""
    record, current, wanted = plan(printer_data, boxes)
    save_record(printer_data, record)
    if wanted is None or wanted == current:
        return False
    path = os.path.join(printer_data, BOX_CFG)
    if current:
        _write(os.path.join(printer_data, PREVIOUS), current, mode=0o600)
    _write(path, wanted)
    return True


def app_patched(app=APP, builds=None):
    """Whether the touchscreen app is a reviewed build with Valve Oil's patch of its Box detector."""
    builds = APP_BUILDS if builds is None else builds
    try:
        size = os.stat(app).st_size
        site = (builds or {}).get(str(size))
        if site is None:
            return False
        with open(app, "rb") as handle:
            data = os.pread(handle.fileno(), len(site[1]) // 2, site[0])
    except OSError:
        return False
    return data == bytes.fromhex(site[1])


def board_work(printer_data):
    """What owns the boards now, or None: an installer run, a firmware update, or a board operation to rescue."""
    for name in WAIT_FOR:
        if os.path.lexists(os.path.join(printer_data, name)):
            return name
    try:
        handle = open(os.path.join(printer_data, INSTALLER_LOCK), "rb")
    except OSError:
        return None
    with handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return INSTALLER_LOCK
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return None


def _get(url, timeout=5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))["result"]


def printer_busy(moonraker=MOONRAKER):
    """Why Klipper can't be restarted now, or None. A Klipper that isn't running or has stopped can't print."""
    state = _get(moonraker + "/server/info").get("klippy_state")
    if state == "startup":
        return "Klipper is starting"
    if state != "ready":
        return None
    status = _get(moonraker + "/printer/objects/query?print_stats=state&heaters=available_heaters")["status"]
    if status.get("print_stats", {}).get("state") in ("printing", "paused"):
        return "a print is running"
    heaters = status.get("heaters", {}).get("available_heaters") or []
    if heaters:
        query = "&".join(urllib.request.quote(name, safe="") + "=target" for name in heaters)
        targets = _get(moonraker + "/printer/objects/query?" + query)["status"]
        if any((targets.get(name) or {}).get("target") for name in heaters):
            return "a heater is on"
    return None


def restart_klipper(moonraker=MOONRAKER):
    request = urllib.request.Request(moonraker + "/machine/services/restart?service=klipper", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=30):
        pass


def publish_waiting(printer_data, reason, boxes):
    _write(os.path.join(printer_data, STATE),
           json.dumps({"schema": SCHEMA, "waiting": {"reason": reason, "boxes": len(boxes)}}) + "\n")


def settle(boxes_now, *, clock, sleep):
    """The Boxes, once the list has stayed the same for SETTLE_SECONDS."""
    boxes, since = boxes_now(), clock()
    while clock() - since < SETTLE_SECONDS:
        sleep(POLL_SECONDS)
        found = boxes_now()
        if found != boxes:
            boxes, since = found, clock()
    return boxes


def run(printer_data=PRINTER_DATA, *, boxes_now=connected, patched=app_patched, busy=printer_busy,
        restart=restart_klipper, clock=time.monotonic, sleep=time.sleep, uptime=None, say=log):
    """Bring box.cfg to the Boxes connected; return what was done."""
    if not patched():
        return "the touchscreen app manages box.cfg"
    if uptime is not None:
        sleep(max(0.0, BOOT_SECONDS - uptime()))
    waiting = None
    while True:
        work = board_work(printer_data)
        if work:
            if work != waiting:
                say("Waiting for %s to end." % work)
                waiting = work
            sleep(WAIT_SECONDS)
            continue
        boxes = settle(boxes_now, clock=clock, sleep=sleep)
        record, current, wanted = plan(printer_data, boxes)
        if wanted is None:
            save_record(printer_data, record)
            _remove(os.path.join(printer_data, STATE))
            return "box.cfg isn't in QIDI's layout, so it was left alone"
        if wanted == current:
            save_record(printer_data, record)
            _remove(os.path.join(printer_data, STATE))
            return "box.cfg already lists the Boxes"
        try:
            reason = busy()
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            reason = "Moonraker can't be reached (%s)" % exc
        if reason:
            if reason != waiting:
                say("A Box change waits: %s." % reason)
                waiting = reason
                publish_waiting(printer_data, reason, boxes)
            sleep(WAIT_SECONDS)
            continue
        # An installer run or board operation may have started while the printer was checked.
        if board_work(printer_data) or boxes_now() != boxes:
            continue
        write_config(printer_data, boxes)
        _remove(os.path.join(printer_data, STATE))
        restart()
        return "box.cfg now lists %d Box%s; Klipper restarted" % (len(boxes), "" if len(boxes) == 1 else "es")


def _uptime():
    with open("/proc/uptime", encoding="ascii") as handle:
        return float(handle.read().split()[0])


def main():
    print(run(uptime=_uptime))
    return 0


if __name__ == "__main__":
    sys.exit(main())
