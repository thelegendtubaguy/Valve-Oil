from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from installer.tests.klipper_fakes import NEVER, Reactor, load_extra


# Invented identifiers. None belongs to real hardware. Klipper names a board on USB after its chip id, whose length
# depends on the MCU family.
MAINBOARD_CHIP = "9091929394959697"
USB_BOX_CHIP = "A0A1A2A3A4A5A6A7A8A9AAAB"
SPARE_BOX_CHIP = "E0E1E2E3E4E5E6E7E8E9EAEB"
RS485_CHIP = bytes(range(0xB0, 0xBC))
LEASED_RS485_CHIP = bytes(range(0x80, 0x8C))
DAMAGED_RS485_CHIP = bytes(range(0x60, 0x6C))
SAVED_RS485_CHIP = bytes(range(0xC0, 0xCC)).hex()
RACK_ID = "d0d1d2d3d4d5d6d7"
ADAPTER_SERIAL = "FAKESN00A1"

BY_ID = "/dev/serial/by-id/"
MAINBOARD = "usb-Klipper_stm32f407xx_%s-if00" % MAINBOARD_CHIP
USB_BOX = "usb-Klipper_stm32f401xc_%s-if00" % USB_BOX_CHIP
SPARE_BOX = "usb-Klipper_stm32f401xc_%s-if00" % SPARE_BOX_CHIP
# QIDI's own adapter is a CH340, which has no serial number; other adapters carry one in their name.
QIDI_ADAPTER = "usb-1a86_USB_Serial-if00-port0"
FTDI_ADAPTER = "usb-FTDI_FT232R_USB_UART_%s-if00-port0" % ADAPTER_SERIAL

QIDI_RETRY = 2.
UUID_ACK = 2
MISSING_ADAPTER_LINE = "Can not find 485 device"


# ------------------------------------------------------------- shared fakes
class Status:
    def __init__(self, **status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class Gcode:
    """Klipper's gcode object, and the G-code command a handler is given: the console is one list."""

    def __init__(self):
        self.commands = {}
        self.console = []

    def register_command(self, name, callback, desc=None):
        self.commands[name] = callback

    def respond_info(self, message, log=True):
        self.console.append(message)

    def respond_raw(self, message):
        self.console.append(message)


class Printer:
    _RAISE = object()

    def __init__(self, log_file=None, objects=None):
        self.reactor = Reactor()
        self.gcode = Gcode()
        self.objects = {"gcode": self.gcode, **(objects or {})}
        self.event_handlers = {}
        self.start_args = {"log_file": str(log_file)} if log_file else {}
        self.state = "startup"

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return self.start_args

    def get_state_message(self):
        return "Printer is %s" % self.state, self.state

    def lookup_object(self, name, default=_RAISE):
        if name in self.objects:
            return self.objects[name]
        if default is self._RAISE:
            raise KeyError(name)
        return default

    def lookup_objects(self, module=None):
        return [(name, obj) for name, obj in self.objects.items()
                if module is None or name == module or name.startswith(module + " ")]

    def load_object(self, config, name, default=None):
        return self.objects.setdefault(name, object())

    def add_object(self, name, obj):
        self.objects[name] = obj

    def register_event_handler(self, event, callback):
        self.event_handlers.setdefault(event, []).append(callback)

    def send_event(self, event):
        # Klipper is in its new state by the time the handlers run.
        self.state = {"klippy:ready": "ready", "klippy:shutdown": "shutdown"}.get(event, self.state)
        for callback in list(self.event_handlers.get(event, [])):
            callback()

    def advance(self, seconds=0.):
        """Move the reactor clock, running each timer when it comes due."""
        reactor = self.reactor
        end = reactor.now + seconds
        while True:
            due = [timer for timer in reactor.timers if timer.waketime <= end]
            if not due:
                break
            timer = min(due, key=lambda timer: timer.waketime)
            reactor.now = max(reactor.now, timer.waketime)
            timer.waketime = timer.callback(reactor.now)
        reactor.now = end


class Config:
    """One section of printer.cfg, with sight of the others."""

    def __init__(self, printer, sections, name):
        self.printer, self.sections, self.name = printer, sections, name

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, option, default=None):
        return self.sections[self.name].get(option, default)

    def getint(self, option, default=None, minval=None):
        return int(self.get(option, default))

    def getfloat(self, option, default=None, minval=None):
        return float(self.get(option, default))

    def getboolean(self, option, default=None):
        return bool(self.get(option, default))

    def has_section(self, name):
        return name in self.sections

    def get_prefix_sections(self, prefix):
        return [Config(self.printer, self.sections, name) for name in self.sections if name.startswith(prefix)]


class KlippyLog(logging.Handler):
    """What reaches klippy.log."""

    def __init__(self):
        super().__init__()
        self.lines = []
        self.warnings = []
        self.failures = []

    def emit(self, record):
        line = record.getMessage()
        self.lines.append(line)
        if record.levelno >= logging.ERROR:
            self.failures.append(line)
        elif record.levelno >= logging.WARNING:
            self.warnings.append(line)


def klippy_log(test):
    """Gives a test the root logger, which Klipper, QIDI's code, and these modules all log through. A module that
    logs a failure of its own fails the test."""
    root = logging.getLogger()
    saved = root.level, root.handlers[:], root.filters[:]

    def restore():
        root.setLevel(saved[0])
        root.handlers[:], root.filters[:] = saved[1], saved[2]

    test.addCleanup(restore)
    log = KlippyLog()
    root.handlers[:], root.filters[:] = [log], []
    root.setLevel(logging.INFO)
    test.addCleanup(lambda: test.assertEqual(log.failures, []))
    return log


class Dmesg:
    """The module's `subprocess` on a printer whose kernel log holds these lines."""

    PIPE = DEVNULL = None

    def __init__(self, lines=()):
        self.lines = list(lines)
        self.runs = 0

    def run(self, command, **kwargs):
        self.runs += 1
        return types.SimpleNamespace(stdout="\n".join(self.lines).encode())


def new_home(test, prefix):
    home = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, home, ignore_errors=True)
    return home


def files_under(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


# ----------------------------------------------------------- box485_monitor
def frame(address, command, payload=b""):
    """QIDI's RS-485 frame: AA 55, address, command, payload length, payload, CRC-16/ARC."""
    body = b"\xaa\x55" + bytes([address, command, len(payload)]) + payload
    crc = 0
    for byte in body:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return body + crc.to_bytes(2, "big")


class SerialPort:
    """pyserial's Serial, as box485 uses the adapter."""

    def __init__(self):
        self.written = bytearray()
        self.incoming = bytearray()
        self.timeout = 0.

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        self.written += data
        return len(data)

    def read(self, size=1):
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data


class PrinterDev:
    """The module's `os`, with the printer's /dev and /sys in a temporary directory, so the test decides which USB
    serial devices the printer has."""

    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.scans = 0
        self.path = self.Paths(self)

    class Paths:
        def __init__(self, dev):
            self.dev = dev

        def __getattr__(self, name):
            return getattr(os.path, name)

        def realpath(self, path):
            return os.path.realpath(self.dev.host(path))

        def exists(self, path):
            return os.path.exists(self.dev.host(path))

    def __getattr__(self, name):
        return getattr(os, name)

    def host(self, path):
        path = str(path)
        if path.startswith(self.root) or not path.startswith(("/dev", "/sys")):
            return path
        return self.root + path

    def listdir(self, path):
        if str(path) == "/dev":
            self.scans += 1
        return os.listdir(self.host(path))

    def plug(self, node, by_id=None):
        links = Path(self.host(BY_ID))
        links.mkdir(parents=True, exist_ok=True)
        Path(self.host("/dev/" + node)).touch()
        if by_id and not (links / by_id).is_symlink():
            (links / by_id).symlink_to("../../" + node)


class WallClock:
    """The module's `time`, with a time of day the test moves."""

    def __init__(self):
        self.now = 1800000000.

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return self.now


def box485_manager(serial_ports, said, reconnect_timer=True):
    """QIDI's Box485Manager: it opens its adapter with pyserial at klippy:ready. When that fails it says so once and
    retries from a reactor timer every 2 s, logging "Can not find 485 device" each time. `serial_ports` are the ports
    pyserial can open; what it logs is added to `said`."""

    def log(message):
        said.append(message)
        logging.info(message)

    class Box485Manager:
        def __init__(self, config):
            self.printer = config.get_printer()
            self.reactor = self.printer.get_reactor()
            self.port = config.get("serial_port")
            self.ser = None
            self.addr_to_dev = {}
            self.attempts = 0
            self.printer.register_event_handler("klippy:ready", self.connect)

        def _open(self):
            self.attempts += 1
            self.ser = serial_ports.get(self.port)
            return self.ser is not None

        def connect(self):
            if self._open():
                return
            log("【通信-串口】无法打开串口 %s: could not open port, 稍后重试..." % self.port)
            if reconnect_timer:
                self.reactor.register_timer(self._on_reconnect_timer, self.reactor.monotonic() + QIDI_RETRY)

        def _on_reconnect_timer(self, eventtime):
            if self._open():
                return self.reactor.NEVER
            log(MISSING_ADAPTER_LINE)
            return eventtime + QIDI_RETRY

        def get_status(self, eventtime=None):
            return {"box_count": len(self.addr_to_dev), "boxes": [], "buffer": {}}

    if not reconnect_timer:
        del Box485Manager._on_reconnect_timer
    return Box485Manager


class Rs485Printer:
    """A printer on firmware that carries QIDI's RS-485 stack, with the monitor loaded. Its mainboard and one USB Box
    are Klipper MCUs on USB; it has no RS-485 hardware until the test adds some."""

    def __init__(self, test, *, box485_port=BY_ID + QIDI_ADAPTER, variables=None, options=None, home=None,
                 box485_loads_first=True, exported=True, reconnect_timer=True):
        self.home = home or new_home(test, "vo-box485-")
        self.printer_data = self.home / "printer_data"
        self.logs = self.printer_data / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.klippy = klippy_log(test)
        self.dev = PrinterDev(self.home / "host")
        self.serial_ports = {}
        self.variables = dict(variables or {})
        self.said = []
        self.sections = {
            "mcu": {"serial": BY_ID + MAINBOARD},
            "mcu THR": {"serial": "/dev/ttyS4"},
            "mcu mcu_box1": {"serial": BY_ID + USB_BOX},
            "box485": {"serial_port": box485_port},
            "tltg_box485_monitor": dict(options or {}),
        }
        self.load_order = ["box485", "tltg_box485_monitor"]
        if not box485_loads_first:
            self.load_order.reverse()
        self.plug("ttyACM0", MAINBOARD)
        self.plug("ttyACM1", USB_BOX)
        # A fresh copy each time, as in a new Klipper process: the module keeps process-wide state.
        self.module = load_extra("box485_monitor", "extras.tltg_box485_monitor")
        self.module.os = self.dev
        self.module.time = self.clock = WallClock()
        self.module.subprocess = Dmesg()
        # QIDI's firmware manifest is under /home/qidi; this printer has none.
        self.module.FIRMWARE_MANIFEST = str(self.home / "host" / "firmware_manifest.json")
        self.manager_class = box485_manager(self.serial_ports, self.said, reconnect_timer)
        extras = types.ModuleType("extras")
        extras.__path__ = []
        if exported:
            extras.box485_multicolor = types.SimpleNamespace(Box485Manager=self.manager_class)
        qidi_modules = mock.patch.dict(sys.modules, {"extras": extras})
        qidi_modules.start()
        test.addCleanup(qidi_modules.stop)
        test.addCleanup(lambda: self.send_event("klippy:disconnect"))
        self.start()

    def start(self):
        """Klipper reads printer.cfg and builds its printer objects, as at startup and at RESTART."""
        self.printer = Printer(self.logs / "klippy.log", {"save_variables": Status(variables=self.variables)})
        loaders = {"box485": self.manager_class, "tltg_box485_monitor": self.module.load_config}
        for name in self.load_order:
            self.printer.objects[name] = loaders[name](Config(self.printer, self.sections, name))
        self.manager = self.printer.objects["box485"]
        self.monitor = self.printer.objects["tltg_box485_monitor"]
        self.console, self.reactor = self.printer.gcode.console, self.printer.reactor
        self.advance, self.send_event = self.printer.advance, self.printer.send_event

    def ready(self):
        self.send_event("klippy:ready")
        self.advance()

    def restart(self):
        self.send_event("klippy:disconnect")
        self.start()
        self.ready()

    def plug(self, node, by_id=None):
        """Connect a USB serial device that pyserial can open."""
        self.dev.plug(node, by_id)
        port = SerialPort()
        self.serial_ports["/dev/" + node] = port
        if by_id:
            self.serial_ports[BY_ID + by_id] = port
        return port

    def plug_adapter(self):
        return self.plug("ttyUSB0", QIDI_ADAPTER)

    def qidi_logs(self, message):
        self.said.append(message)
        logging.info(message)

    def run(self, command):
        self.printer.gcode.commands[command](self.printer.gcode)

    def written(self):
        return files_under(self.printer_data)

    def log(self):
        path = self.logs / "vo_box485.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def report(self):
        return json.loads((self.logs / "tltg_box485_report.json").read_text(encoding="utf-8"))

    def shared(self):
        """Everything in the logs folder, which Fluidd serves and the operator is asked to attach to an issue."""
        return "\n".join(path.read_text(encoding="utf-8") for path in sorted(self.logs.iterdir()))

    def told(self):
        """The console, plus klippy.log without the lines the test logged as QIDI."""
        return "\n".join(self.console + [line for line in self.klippy.lines if line not in self.said])


class Box485MonitorTest(unittest.TestCase):
    def test_box485_never_opens_a_klipper_mcu_serial_port(self) -> None:
        # Owners quiet QIDI's "Can not find 485 device" by giving [box485] a USB Box's id. box485 polls its port for
        # reads, so it would take that MCU's replies from Klipper and the Box would drop mid-print.
        ports = {
            "the Box's id from box.cfg": BY_ID + USB_BOX,
            "the Box's tty": "/dev/ttyACM1",
            "a Klipper board printer.cfg leaves out": BY_ID + SPARE_BOX,
        }
        for label, port in ports.items():
            # A [box485] section ahead of the monitor has already bound QIDI's connect to klippy:ready.
            for box485_loads_first in (True, False):
                with self.subTest(port=label, box485_loads_first=box485_loads_first):
                    printer = Rs485Printer(self, box485_port=port, box485_loads_first=box485_loads_first)
                    printer.plug("ttyACM2", SPARE_BOX)

                    printer.ready()
                    printer.advance(120.)
                    retry = printer.manager._on_reconnect_timer(printer.reactor.now)

                    self.assertEqual(printer.manager.attempts, 0)
                    self.assertIsNone(printer.manager.ser)
                    self.assertEqual(retry, NEVER)
                    self.assertTrue(printer.monitor.get_status(0.)["port_conflict"])
                    self.assertEqual(len(printer.console), 1)
                    for chip in (USB_BOX_CHIP, SPARE_BOX_CHIP):
                        self.assertNotIn(chip.lower(), (printer.told() + printer.shared()).lower())

    def test_nothing_is_written_without_rs485_hardware(self) -> None:
        # Every printer on this firmware loads box485, and almost none has RS-485 hardware: QIDI's code fails to
        # open its adapter, keeps retrying, and USB Boxes go on logging Box lines.
        printer = Rs485Printer(self)

        printer.ready()
        printer.qidi_logs("box1 slot 2 filament loaded")
        printer.advance(3600.)
        printer.send_event("klippy:shutdown")
        printer.send_event("klippy:disconnect")

        self.assertGreater(printer.manager.attempts, 1)
        self.assertEqual(printer.written(), [])
        self.assertEqual(printer.console, [])
        self.assertFalse(printer.monitor.get_status(0.)["detected"])

    def test_each_sign_of_rs485_hardware_starts_the_recording_and_asks_the_operator_once(self) -> None:
        signs = {
            "box485 opened its adapter": lambda printer: printer.plug_adapter(),
            "a device registered with box485": lambda printer: printer.manager.addr_to_dev.update({1: "multicolor"}),
            "a saved RS-485 Box id": lambda printer: printer.variables.update(box0_mcu_id=SAVED_RS485_CHIP),
            "saved RS-485 address leases": lambda printer: printer.variables.update(
                box485_leases={SAVED_RS485_CHIP: 1}),
            "a USB serial adapter that is no Klipper MCU": lambda printer: printer.dev.plug("ttyUSB0", FTDI_ADAPTER),
            "a USB serial port with no by-id name": lambda printer: printer.dev.plug("ttyACM5"),
        }
        # The operator may run VO_RS_BOX_REPORT the moment the hardware is there, before the monitor looks again.
        for label, sign in signs.items():
            for when in ("before Klipper starts", "while Klipper runs", "with VO_RS_BOX_REPORT run at once"):
                with self.subTest(sign=label, when=when):
                    printer = Rs485Printer(self)
                    if when == "before Klipper starts":
                        sign(printer)
                    printer.ready()
                    printer.advance(10.)
                    if when != "before Klipper starts":
                        sign(printer)
                    if when == "with VO_RS_BOX_REPORT run at once":
                        printer.run("VO_RS_BOX_REPORT")
                    printer.advance(60.)
                    # More of it turning up is not news.
                    printer.dev.plug("ttyUSB3")
                    printer.advance(30.)

                    self.assertTrue(printer.monitor.get_status(0.)["detected"])
                    self.assertTrue(printer.log())
                    self.assertTrue(printer.report())
                    self.assertEqual(len([line for line in printer.console if "open an issue" in line]), 1)

    def test_frames_through_the_adapter_are_recorded_from_the_first_and_reach_qidi_unchanged(self) -> None:
        printer = Rs485Printer(self)
        printer.ready()
        printer.advance(120.)
        # The adapter is plugged in with Klipper running; QIDI's next retry opens it.
        port = printer.plug_adapter()
        printer.advance(60.)
        ser = printer.manager.ser
        discovery = frame(0, 1, bytes.fromhex("0badf00d"))
        reply = frame(1, 32, bytes.fromhex("c0ffee"))

        self.assertEqual(ser.write(discovery), len(discovery))
        # box485 polls, so a frame can arrive split between two reads.
        port.incoming += reply[:6]
        self.assertEqual(ser.in_waiting, 6)
        self.assertEqual(ser.read(64), reply[:6])
        port.incoming += reply[6:]
        self.assertEqual(ser.read(64), reply[6:])
        ser.timeout = .25

        self.assertEqual(bytes(port.written), discovery)
        self.assertEqual(port.timeout, .25)
        self.assertIn("0badf00d", printer.log())
        self.assertIn("c0ffee", printer.log())

        # A device answers each poll with the same frame until something changes.
        for _ in range(40):
            port.incoming += reply
            self.assertEqual(ser.read(64), reply)
        port.incoming += frame(1, 32, bytes.fromhex("decade"))
        ser.read(64)

        self.assertEqual(printer.log().count("c0ffee"), 1)
        self.assertIn("decade", printer.log())

    def test_hardware_identifiers_never_reach_the_log_report_console_or_klippy_log(self) -> None:
        # The operator is asked to attach the log and report to a public issue.
        printer = Rs485Printer(self, box485_port=BY_ID + FTDI_ADAPTER, variables={
            "box1_mcu_id": SAVED_RS485_CHIP, "box485_leases": {LEASED_RS485_CHIP.hex(): 1}})
        printer.manager.rack_uuid = RACK_ID
        # The adapter is plugged in after Klipper starts, so QIDI's code first logs the by-id name it couldn't open,
        # serial number and all. The recording starts with that line.
        printer.ready()
        port = printer.plug("ttyUSB0", FTDI_ADAPTER)
        printer.advance(60.)
        ser = printer.manager.ser

        # QIDI's code addresses a device it holds a lease for by its chip id, before that device has answered.
        ser.write(frame(1, 3, b"\x05" + LEASED_RS485_CHIP + b"\x00"))
        # A device answers discovery with its chip id, and QIDI's code addresses it by that id from then on.
        port.incoming += frame(0, UUID_ACK, RS485_CHIP + b"\x01")
        ser.read(64)
        ser.write(frame(2, 3, b"\x05" + RS485_CHIP + b"\x00"))
        # Another device's answer is damaged on the line: any byte of it, the command and length too, may be wrong.
        for payload in (DAMAGED_RS485_CHIP, DAMAGED_RS485_CHIP + b"\x01"):
            damaged = frame(0, UUID_ACK, payload)
            port.incoming += damaged[:-1] + bytes([damaged[-1] ^ 0xFF])
            ser.read(64)
        printer.qidi_logs("【通信】 UUID %s at addr 1, lease kept for 02%s00"
                          % (RS485_CHIP.hex().upper(), SAVED_RS485_CHIP.upper()))
        printer.advance(700.)
        printer.run("VO_RS_BOX_REPORT")
        printer.send_event("klippy:shutdown")
        printer.send_event("klippy:disconnect")

        everything = (printer.shared() + printer.told()).lower()
        for identifier in (RS485_CHIP.hex(), LEASED_RS485_CHIP.hex(), DAMAGED_RS485_CHIP.hex(), SAVED_RS485_CHIP,
                           RACK_ID, ADAPTER_SERIAL, USB_BOX_CHIP, MAINBOARD_CHIP):
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier.lower(), everything)
        # Only the identifiers go: the adapter's make, QIDI's lines, and the damaged answers are still there to read.
        self.assertIn("usb-FTDI_FT232R_USB_UART_", printer.shared())
        self.assertIn("无法打开串口", printer.log())
        self.assertIn("lease kept for", printer.log())
        self.assertEqual(printer.log().count("crc=bad"), 2)
        self.assertTrue(printer.console)

    def test_id_codes_stay_the_same_on_one_printer_and_differ_between_printers(self) -> None:
        # A code that follows a device from printer to printer would identify it as well as its id does.
        def box_port_code(printer):
            printer.plug_adapter()
            printer.ready()
            return printer.report()["mcu_serials"]["mcu mcu_box1"]

        first = Rs485Printer(self)
        code = box_port_code(first)
        first.send_event("klippy:disconnect")
        after_restart = box_port_code(Rs485Printer(self, home=first.home))
        elsewhere = box_port_code(Rs485Printer(self))

        self.assertNotIn(USB_BOX_CHIP.lower(), code.lower())
        self.assertEqual(after_restart, code)
        self.assertNotEqual(elsewhere, code)
        # Fluidd serves the logs folder; what undoes the codes must not be in it.
        self.assertEqual(sorted(os.listdir(first.logs)), ["tltg_box485_report.json", "vo_box485.log"])

    def test_qidis_missing_adapter_line_reaches_klippy_log_once_an_hour_with_its_count(self) -> None:
        printer = Rs485Printer(self)

        def missing_adapter_lines():
            return [line for line in printer.klippy.lines if MISSING_ADAPTER_LINE in line]

        # QIDI logs it every 2 s for as long as no adapter is attached.
        for _ in range(1800):
            logging.info(MISSING_ADAPTER_LINE)
            logging.info("Stats %.1f: gcodein=0", printer.clock.now)
            printer.clock.now += 2.
        self.assertEqual(len(missing_adapter_lines()), 1)
        logging.info(MISSING_ADAPTER_LINE)

        self.assertEqual(len(missing_adapter_lines()), 2)
        self.assertIn("1799", missing_adapter_lines()[1])
        self.assertEqual(len(printer.klippy.lines), 1800 + 2)

    def test_adapter_search_and_hardware_checks_slow_down_only_until_hardware_is_seen(self) -> None:
        # Both run in Klipper's main thread, on every printer, for an adapter almost none has.
        def looks_in_ten_minutes(printer):
            printer.ready()
            attempts, scans = printer.manager.attempts, printer.dev.scans
            printer.advance(600.)
            return printer.manager.attempts - attempts, printer.dev.scans - scans

        qidi_idle, monitor_idle = looks_in_ten_minutes(Rs485Printer(self))
        # An RS-485 Box has been on this printer; its adapter is unplugged for now.
        qidi_seen, monitor_seen = looks_in_ten_minutes(Rs485Printer(self, variables={"box0_mcu_id": SAVED_RS485_CHIP}))

        self.assertEqual(qidi_seen, 600. / QIDI_RETRY)
        self.assertLess(qidi_idle, qidi_seen / 5)
        self.assertGreater(qidi_idle, 0)
        self.assertLess(monitor_idle, monitor_seen / 3)
        self.assertGreater(monitor_idle, 0)

    def test_qidis_rs485_log_lines_are_recorded_from_before_detection_on(self) -> None:
        printer = Rs485Printer(self)
        # QIDI's code reports the adapter it couldn't open here, long before any hardware is seen.
        printer.ready()
        # USB Boxes log Box lines too, so these mean nothing until RS-485 hardware is there.
        printer.qidi_logs("box1 slot 2 filament loaded")
        printer.advance(120.)
        printer.plug_adapter()
        printer.advance(60.)

        printer.qidi_logs("slot 3 filament runout")
        printer.qidi_logs("heater_bed approaching new target")

        log = printer.log()
        self.assertIn("无法打开串口", log)
        self.assertIn("slot 3 filament runout", log)
        self.assertNotIn("slot 2 filament loaded", log)
        self.assertNotIn("heater_bed", log)

    def test_after_a_klipper_restart_the_new_printer_is_recorded_once(self) -> None:
        # RESTART builds new printer objects in the same process, where QIDI's class is already patched and the
        # log hooks are already installed.
        printer = Rs485Printer(self)
        port = printer.plug_adapter()
        printer.ready()

        printer.restart()
        port.incoming += frame(1, 32, bytes.fromhex("c0ffee"))
        printer.manager.ser.read(64)
        printer.qidi_logs("【通信】 addr 1 online")
        printer.advance(10.)

        self.assertEqual(printer.log().count("c0ffee"), 1)
        self.assertEqual(printer.log().count("addr 1 online"), 1)
        self.assertIn("traffic", printer.monitor.get_status(0.)["signals"])

    def test_klipper_starts_and_box485_runs_when_qidis_code_is_not_what_the_monitor_expects(self) -> None:
        # QIDI's modules change between firmware versions; a monitor must not be why Klipper fails to start.
        changes = {
            "box485_multicolor is gone from extras": {"exported": False},
            "Box485Manager has no reconnect timer": {"reconnect_timer": False},
        }
        for label, change in changes.items():
            with self.subTest(change=label):
                printer = Rs485Printer(self, **change)
                port = printer.plug_adapter()

                printer.ready()
                printer.advance(120.)

                self.assertIs(printer.manager.ser, port)
                self.assertTrue(printer.monitor.get_status(0.)["detected"])
                # The port guard is off, and klippy.log says so.
                self.assertTrue(printer.klippy.warnings)

    def test_full_log_moves_to_a_single_backup(self) -> None:
        # The smallest cap the option takes. An earlier session left the log one line short of it.
        cap = 256 * 1024
        printer = Rs485Printer(self, options={"max_log_bytes": cap})
        log = printer.logs / "vo_box485.log"
        log.write_text("earlier session\n" * (cap // 16 - 1), encoding="utf-8")
        port = printer.plug_adapter()

        printer.ready()
        for number in range(8):
            port.incoming += frame(1, 32, bytes([number]) * 4)
            printer.manager.ser.read(64)
        printer.send_event("klippy:disconnect")

        self.assertEqual(sorted(os.listdir(printer.logs)),
                         ["tltg_box485_report.json", "vo_box485.log", "vo_box485.log.1"])
        self.assertIn("earlier session", (printer.logs / "vo_box485.log.1").read_text(encoding="utf-8"))
        self.assertLess(log.stat().st_size, cap)
        self.assertIn("07070707", printer.log())


# --------------------------------------------------------- box_link_monitor
ENABLE = "vo_box_link_monitor"
HEALTHY_LINK = {
    "bytes_retransmit": 9, "bytes_invalid": 0, "send_seq": 500, "receive_seq": 500, "retransmit_seq": 2,
    "ready_bytes": 0, "upcoming_bytes": 0, "rto": .025, "srtt": .001, "rttvar": 0., "mcu_awake": .002,
    "freq": 84000000, "adj": 84000010,
}
USB_DISCONNECT = "usb 1-1.2: USB disconnect, device number 5"


class Mcu:
    """Klipper's MCU as the link monitor uses it. Each MCU numbers its object ids from 0."""

    def __init__(self, name, has_reset_reason=True):
        self.stats = dict(HEALTHY_LINK)
        self.has_reset_reason = has_reset_reason
        self.oids = []
        self.queries = []
        self.responses = {}
        self.history = "%s: seq: 1f, get_clock" % name
        self._serial = types.SimpleNamespace(dump_debug=self.dump_debug)

    def create_oid(self):
        self.oids.append(len(self.oids))
        return self.oids[-1]

    def get_status(self, eventtime=None):
        return {"last_stats": dict(self.stats)}

    def try_lookup_command(self, msgformat):
        if not self.has_reset_reason:
            return None
        return types.SimpleNamespace(send=lambda data=(), **kwargs: self.queries.append(list(data)))

    def register_response(self, callback, name, oid=None):
        self.responses[name, oid] = callback

    def answer(self, reason):
        self.responses["reset_reason_return", self.oids[0]]({"oid": self.oids[0], "reason": reason.encode()})

    def dump_debug(self):
        if self.history is None:
            raise OSError("serial port closed")
        return self.history


class LinkPrinter:
    """A printer with two USB Boxes and the link monitor loaded, mid-print."""

    def __init__(self, test, variables, options=None):
        self.home = new_home(test, "vo-box-link-")
        self.logs = self.home / "printer_data" / "logs"
        self.logs.mkdir(parents=True)
        self.klippy_log = self.logs / "klippy.log"
        self.klippy_log.write_text("Stats 100.0: gcodein=0 mcu: mcu_awake=0.002\n", encoding="utf-8")
        klippy_log(test)
        self.mainboard, self.thr = Mcu("mcu"), Mcu("THR", has_reset_reason=False)
        self.box1, self.box2 = Mcu("mcu_box1"), Mcu("mcu_box2")
        self.mcus = [self.mainboard, self.thr, self.box1, self.box2]
        objects = {"mcu": self.mainboard, "mcu THR": self.thr, "mcu mcu_box1": self.box1, "mcu mcu_box2": self.box2,
                   "print_stats": Status(state="printing")}
        self.variables = variables
        if variables is not None:
            objects["save_variables"] = Status(variables=variables)
        self.printer = Printer(self.klippy_log, objects)
        self.advance, self.send_event = self.printer.advance, self.printer.send_event
        self.module = load_extra("box_link_monitor")
        self.module.subprocess = self.dmesg = Dmesg(["eth0: Link is Up - 100Mbps", USB_DISCONNECT])
        section = "tltg_box_link_monitor"
        self.monitor = self.module.load_config(Config(self.printer, {section: dict(options or {})}, section))
        test.addCleanup(lambda: self.send_event("klippy:disconnect"))

    def klippy_writes(self, *lines):
        with open(self.klippy_log, "a", encoding="utf-8") as handle:
            handle.write("".join(line + "\n" for line in lines))

    def log(self):
        path = self.logs / "vo_box_link.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""


class BoxLinkMonitorTest(unittest.TestCase):
    def test_nothing_is_written_and_no_mcu_is_asked_anything_until_the_saved_variable_is_on(self) -> None:
        for variables in (None, {}, {ENABLE: 0}, {ENABLE: "0"}, {ENABLE: False}, {ENABLE: "off"}):
            with self.subTest(variables=variables):
                printer = LinkPrinter(self, variables)

                printer.send_event("klippy:connect")
                printer.advance(600.)
                printer.box1.stats["bytes_retransmit"] = 4242
                printer.klippy_writes("Lost communication with MCU 'mcu_box1'")
                printer.advance(5.)
                printer.send_event("klippy:shutdown")
                printer.send_event("klippy:disconnect")

                self.assertEqual(os.listdir(printer.logs), ["klippy.log"])
                self.assertEqual([mcu.queries for mcu in printer.mcus], [[], [], [], []])
                self.assertEqual(printer.dmesg.runs, 0)

    def test_saved_variable_turns_recording_on_and_off_without_a_restart(self) -> None:
        for value in (1, "1", True, "true"):
            with self.subTest(value=value):
                printer = LinkPrinter(self, {ENABLE: 0})
                printer.send_event("klippy:connect")
                printer.advance(5.)

                printer.variables[ENABLE] = value
                printer.advance(2.)
                self.assertTrue(printer.log())

                printer.variables[ENABLE] = 0
                printer.advance(2.)
                stopped = printer.log()
                printer.box1.stats["bytes_retransmit"] = 4242
                printer.klippy_writes("Lost communication with MCU 'mcu_box1'")
                printer.advance(600.)
                printer.send_event("klippy:shutdown")
                self.assertEqual(printer.log(), stopped)

    def test_link_counter_changes_are_recorded_for_the_mcu_they_happen_on(self) -> None:
        printer = LinkPrinter(self, {ENABLE: 1})
        printer.advance(3.)
        steady = printer.log()
        printer.advance(5.)
        self.assertEqual(printer.log(), steady)

        printer.box2.stats["bytes_retransmit"] = 4242
        printer.advance(1.)

        added = printer.log()[len(steady):]
        self.assertIn("4242", added)
        self.assertIn("mcu_box2", added)
        self.assertNotIn("mcu_box1", added)

    def test_first_link_failure_of_a_connection_saves_each_boxes_message_history_and_the_usb_kernel_lines(self) -> None:
        # Klipper writes an MCU's message history to klippy.log only when that MCU reports a shutdown, which a Box
        # that dropped off USB never does.
        failures = (
            "Got EOF when reading from device",
            "Timeout with MCU 'mcu_box1' (eventtime=1234.5)",
            "Lost communication with MCU 'mcu_box1'",
            "MCU 'mcu_box1' shutdown: Timer too close",
        )
        for failure in failures:
            with self.subTest(failure=failure):
                printer = LinkPrinter(self, {ENABLE: 1})
                histories = (printer.box1.history, printer.box2.history)
                printer.send_event("klippy:connect")
                printer.advance(3.)
                self.assertNotIn(printer.box1.history, printer.log())

                printer.klippy_writes("Stats 104.0: gcodein=0 mcu_box1: bytes_retransmit=9", failure)
                printer.advance(1.)
                log = printer.log()
                self.assertIn(failure, log)
                self.assertEqual([log.count(history) for history in histories], [1, 1])
                self.assertIn(USB_DISCONNECT, log)
                self.assertNotIn("gcodein", log)
                self.assertNotIn("eth0", log)

                # What the Boxes exchange after the first failure is not what led to it.
                printer.klippy_writes("Lost communication with MCU 'mcu_box2'")
                printer.advance(1.)
                log = printer.log()
                self.assertIn("'mcu_box2'", log)
                self.assertEqual([log.count(history) for history in histories], [1, 1])

                printer.send_event("klippy:connect")
                printer.klippy_writes(failure)
                printer.advance(1.)
                self.assertEqual([printer.log().count(history) for history in histories], [2, 2])

    def test_each_mcu_is_asked_its_reset_reason_once_after_connecting_and_answers_under_its_own_name(self) -> None:
        printer = LinkPrinter(self, {ENABLE: 1})
        asked = (printer.mainboard, printer.box1, printer.box2)
        # An MCU allocates only the object ids made while printer.cfg loads.
        self.assertEqual([mcu.oids for mcu in printer.mcus], [[0], [0], [0], [0]])
        # MCU command dictionaries are loaded only once Klipper has identified the MCUs.
        printer.advance(3.)
        self.assertEqual([mcu.queries for mcu in printer.mcus], [[], [], [], []])

        printer.send_event("klippy:connect")
        printer.advance(60.)
        self.assertEqual([mcu.oids for mcu in printer.mcus], [[0], [0], [0], [0]])
        self.assertEqual([mcu.queries for mcu in asked], [[[0]], [[0]], [[0]]])
        # THR's firmware has no such command.
        self.assertEqual(printer.thr.queries, [])

        # Both Boxes hold the same object id.
        printer.box2.answer("IndependentWatchdog")
        printer.box1.answer("PowerOn")
        lines = printer.log().splitlines()
        watchdog = [line for line in lines if "IndependentWatchdog" in line]
        power_on = [line for line in lines if "PowerOn" in line]
        self.assertEqual(len(watchdog), 1)
        self.assertIn("mcu_box2", watchdog[0])
        self.assertEqual(len(power_on), 1)
        self.assertIn("mcu_box1", power_on[0])

    def test_full_log_moves_to_a_single_backup_while_klipper_runs(self) -> None:
        # Klipper can run for weeks between restarts, and every MCU's message counters move each second.
        cap = 64 * 1024  # the smallest the option takes
        printer = LinkPrinter(self, {ENABLE: 1}, options={"max_log_bytes": cap})
        printer.send_event("klippy:connect")
        for _ in range(600):
            for mcu in printer.mcus:
                mcu.stats["send_seq"] += 1
                mcu.stats["receive_seq"] += 1
            printer.advance(1.)

        self.assertEqual(sorted(os.listdir(printer.logs)), ["klippy.log", "vo_box_link.log", "vo_box_link.log.1"])
        for name in ("vo_box_link.log", "vo_box_link.log.1"):
            self.assertLess((printer.logs / name).stat().st_size, cap + 1024)
        self.assertIn("send_seq", printer.log())

    def test_shutdown_records_the_final_link_counters_and_failure_even_if_the_lost_box_cannot_be_read(self) -> None:
        printer = LinkPrinter(self, {ENABLE: 1})
        printer.send_event("klippy:connect")
        printer.advance(3.)
        before = printer.log()

        # Klipper shuts down the moment it loses the Box, before the monitor samples again.
        printer.box1.stats["bytes_retransmit"] = 4242
        printer.box1.history = None
        printer.klippy_writes("Lost communication with MCU 'mcu_box1'")
        printer.send_event("klippy:shutdown")
        printer.send_event("klippy:disconnect")

        added = printer.log()[len(before):]
        self.assertIn("4242", added)
        self.assertIn("Lost communication with MCU 'mcu_box1'", added)
        self.assertIn(printer.box2.history, added)
        self.assertIn(USB_DISCONNECT, added)


# ------------------------------------------------------ box_mcu_temperature
class SensorConfig:
    """Klipper's configfile.ConfigWrapper over a section a module wrote itself."""

    def __init__(self, printer, fileconfig, access_tracking, section):
        self.section = section
        self.options = dict(fileconfig.items(section))

    def get_name(self):
        return self.section


def box_chip_sensors(*mcus):
    """The temperature sensors the module adds to a printer configured with these MCUs, by name."""
    extras = types.ModuleType("extras")
    extras.__path__ = []
    # Klipper's temperature_sensor builds the sensor from its config section; the section stands in for it here.
    extras.temperature_sensor = types.SimpleNamespace(load_config_prefix=lambda config: config)
    configfile = types.SimpleNamespace(ConfigWrapper=SensorConfig)
    module = load_extra("box_mcu_temperature", "extras.tltg_box_mcu_temperature",
                        stand_ins={"extras": extras, "configfile": configfile})
    printer = Printer(objects={name: object() for name in mcus})
    section = "tltg_box_mcu_temperature"
    module.load_config(Config(printer, {section: {}}, section))
    return {name: sensor.options for name, sensor in printer.lookup_objects("temperature_sensor")}


class BoxMcuTemperatureTest(unittest.TestCase):
    def test_each_configured_box_mcu_gets_a_chip_temperature_sensor(self) -> None:
        # QIDI rewrites box.cfg for however many Boxes it finds, so the Box MCUs differ from printer to printer.
        printers = (
            (("mcu", "mcu THR"), {}),
            (("mcu", "mcu THR", "mcu mcu_box1"), {"temperature_sensor Box1_MCU": "mcu_box1"}),
            (("mcu", "mcu mcu_box2", "mcu mcu_box1", "mcu THR", "mcu mcu_box4"),
             {"temperature_sensor Box1_MCU": "mcu_box1", "temperature_sensor Box2_MCU": "mcu_box2",
              "temperature_sensor Box4_MCU": "mcu_box4"}),
        )
        for mcus, expected in printers:
            with self.subTest(mcus=mcus):
                sensors = box_chip_sensors(*mcus)

                self.assertEqual({name: options["sensor_mcu"] for name, options in sensors.items()}, expected)
                self.assertEqual({options["sensor_type"] for options in sensors.values()},
                                 {"temperature_mcu"} if expected else set())

    def test_box_chip_sensors_have_no_temperature_limits(self) -> None:
        # Klipper shuts down when a sensor reads outside its min_temp and max_temp.
        for options in box_chip_sensors("mcu", "mcu mcu_box1", "mcu mcu_box2").values():
            self.assertNotIn("min_temp", options)
            self.assertNotIn("max_temp", options)


if __name__ == "__main__":
    unittest.main()
