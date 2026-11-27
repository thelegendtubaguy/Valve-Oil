"""Valve Oil's agent answers QIDI's file and filament requests and announces the printer on the LAN as QIDI's app
did, and in LAN-only mode Valve Oil keeps the printer from reaching QIDI and the services QIDI's software uses."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import socket
import sqlite3
import unittest
import zipfile

from installer.tests.helpers import REPO_ROOT, temp_path

SYSTEM = REPO_ROOT / "installer/system"
STOCK_FILAMENTS = REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares/01.01.07.01/config/officiall_filas_list.cfg"
SERIAL = "ABC0123456789DEF"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", SYSTEM / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AGENT = _load("vo_qidi_agent")
FILES = _load("vo_qidi_files")
LAN = _load("vo_qidi_lan")


class Moonraker:
    """The calls the helpers make: QIDI's config table."""

    def __init__(self, config=None):
        self.config = dict(config or {})

    async def call(self, method, params=None, timeout=None):
        if method == "server.database.config.select":
            key = f"{params['section']}.{params['key']}"
            return {"status": "success", "result": {key: self.config.get(key, params.get("default"))}}
        raise AGENT.MoonrakerError(f"unexpected call {method}")


def context(printer_data="/home/qidi/printer_data", name="QD_MAX4", moonraker=None):
    result = AGENT.Context(name, printer_data=str(printer_data), version="01.01.07.01")
    result.serial_number = SERIAL
    result.connection = moonraker
    return result


def dump(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# ---- Files ----------------------------------------------------------------------------------------------------------

# Entries of QIDI's app's list captured on a Smart 5 (01.01.02.01), printer_data at /home/qidi/printer_data.
CAPTURED = [
    '{"extension":".3mf","filename":"Little+Stitch.gcode.3mf","filepath":"/home/qidi/printer_data/.cache/Little+Stitch.gcode.3mf","plate_count":"1","plates":[{"bed_type":"cool_plate","filament_colour":"#898F9B","filament_type":"PLA","filament_weight":"4g","nozzle_diameter":"0.400000","plate_index":"1","print_time":"33m","print_time_seconds":1999,"used_extruders":"0"}],"print_objects":[{"identify_id":4022,"name":"D128B7DB3B17D597A3333AF582665470.OBJ_ID_0_COPY_0","skipped":false}],"show_filament_weight":"4g","show_print_time":"33m","thumbnails":[{"data_size":68773,"height":1,"width":-1}]}',  # noqa: E501
    '{"extension":".3mf","filename":"Campfire lamp.gcode.3mf","filepath":"/home/qidi/printer_data/gcodes/Campfire lamp.gcode.3mf","plate_count":"3","plates":[{"bed_type":"cool_plate","filament_colour":"#804040","filament_type":"PLA","filament_weight":"76g","nozzle_diameter":"0.400000","plate_index":"1","print_time":"2h54m","print_time_seconds":10487,"used_extruders":"0"},{"bed_type":"cool_plate","filament_colour":"#804040","filament_type":"PLA","filament_weight":"3g","nozzle_diameter":"0.400000","plate_index":"2","print_time":"21m","print_time_seconds":1286,"used_extruders":"0"},{"bed_type":"cool_plate","filament_colour":"#FF0000","filament_type":"PLA","filament_weight":"82g","nozzle_diameter":"0.400000","plate_index":"3","print_time":"5h39m","print_time_seconds":20342,"used_extruders":"1"}],"print_objects":[{"identify_id":122,"name":"TEALIGHT_CAMP_FIRE_LAYYYYYYYMP.STL_1_ID_0_COPY_0","skipped":false},{"identify_id":166,"name":"TEALIGHT_CAMP_FIRE_LAMCCAPP.STL_ID_0_COPY_0","skipped":false},{"identify_id":144,"name":"TEALIGHT_CAMP_FIRE_LAYYYYYYYMP.STL_2_ID_0_COPY_0","skipped":false}],"show_filament_weight":"76g","show_print_time":"2h 54m","thumbnails":[{"data_size":55874,"height":1,"width":-1},{"data_size":7666,"height":2,"width":-2},{"data_size":61542,"height":3,"width":-3}]}',  # noqa: E501
    '{"extension":".3mf","filename":"Foldable_carrot.gcode.3mf","filepath":"/home/qidi/printer_data/gcodes/Foldable_carrot.gcode.3mf","plate_count":"1","plates":[{"bed_type":"supertack_plate","filament_colour":"#FF8000;#00FF00","filament_type":"PLA;PLA","filament_weight":"26g","nozzle_diameter":"0.400000","plate_index":"1","print_time":"1h29m","print_time_seconds":5395,"used_extruders":"0;1"}],"print_objects":[{"identify_id":428,"name":"MARCHEWKA_V8.STEP_ID_0_COPY_0","skipped":false}],"show_filament_weight":"26g","show_print_time":"1h 29m","thumbnails":[{"data_size":39702,"height":1,"width":-1}]}',  # noqa: E501
    '{"extension":".3mf","filename":"Little+Stitch.gcode.3mf","filepath":"/home/qidi/printer_data/gcodes/Little+Stitch.gcode.3mf","plate_count":"1","plates":[{"bed_type":"cool_plate","filament_colour":"#898F9B","filament_type":"PLA","filament_weight":"4g","nozzle_diameter":"0.400000","plate_index":"1","print_time":"33m","print_time_seconds":1999,"used_extruders":"0"}],"print_objects":[{"identify_id":4022,"name":"D128B7DB3B17D597A3333AF582665470.OBJ_ID_0_COPY_0","skipped":false}],"show_filament_weight":"4g","show_print_time":"33m","thumbnails":[{"data_size":68773,"height":1,"width":-1}]}',  # noqa: E501
]

# What the slicer wrote into each of those files, as QIDI Studio writes a .gcode.3mf: plate metadata and objects in
# slice_info.config, bed type and nozzle in each plate's JSON, and a plate PNG.
SLICED = {
    "Campfire lamp.gcode.3mf": [
        {"prediction": 10487, "weight": "76.95", "bed": "cool_plate", "png": 55874,
         "filaments": [(1, "PLA", "#804040")], "objects": [(122, "Tealight_camp_fire_layyyyyyymp.stl_1_id_0_copy_0")]},
        {"prediction": 1286, "weight": "3.41", "bed": "cool_plate", "png": 7666,
         "filaments": [(1, "PLA", "#804040")], "objects": [(166, "Tealight_camp_fire_lamccapp.stl_id_0_copy_0")]},
        {"prediction": 20342, "weight": "82.07", "bed": "cool_plate", "png": 61542,
         "filaments": [(2, "PLA", "#FF0000")], "objects": [(144, "Tealight_camp_fire_layyyyyyymp.stl_2_id_0_copy_0")]},
    ],
    "Foldable_carrot.gcode.3mf": [
        {"prediction": 5395, "weight": "26.5", "bed": "supertack_plate", "png": 39702,
         "filaments": [(1, "PLA", "#FF8000"), (2, "PLA", "#00FF00")],
         "objects": [(428, "marchewka_v8.step_id_0_copy_0")]},
    ],
    "Little+Stitch.gcode.3mf": [
        {"prediction": 1999, "weight": "4.98", "bed": "cool_plate", "png": 68773,
         "filaments": [(1, "PLA", "#898F9B")], "objects": [(4022, "d128b7db3b17d597a3333af582665470.obj_id_0_copy_0")]},
    ],
}


def write_3mf(path, plates):
    config = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>",
              '  <header><header_item key="X-QDT-Client-Type" value="slicer"/></header>']
    with zipfile.ZipFile(path, "w") as archive:
        for index, plate in enumerate(plates, 1):
            config += ["  <plate>", f'    <metadata key="index" value="{index}"/>',
                       '    <metadata key="nozzle_diameters" value="0.4"/>',
                       f'    <metadata key="prediction" value="{plate["prediction"]}"/>',
                       f'    <metadata key="weight" value="{plate["weight"]}"/>']
            config += [f'    <object identify_id="{ident}" name="{name}" skipped="false" />'
                       for ident, name in plate["objects"]]
            config += [f'    <filament id="{ident}" type="{kind}" color="{color}" used_g="1" />'
                       for ident, kind, color in plate["filaments"]]
            config += ['    <warning msg="bed_temperature_too_high_than_filament" level="3" error_code ="1000C001"  />',
                       "  </plate>"]
            archive.writestr(f"Metadata/plate_{index}.json", json.dumps(
                {"bed_type": plate["bed"], "filament_ids": [ident - 1 for ident, _, _ in plate["filaments"]],
                 "nozzle_diameter": 0.4000000059604645, "version": 2}))
            archive.writestr(f"Metadata/plate_{index}.png", b"\x89PNG" + b"\0" * (plate["png"] - 4))
            archive.writestr(f"Metadata/plate_no_light_{index}.png", b"\x89PNG")
            archive.writestr(f"Metadata/plate_{index}.gcode", "G28\n")
        archive.writestr("Metadata/slice_info.config", "\n".join(config + ["</config>"]))


ORCA_GCODE = """; HEADER_BLOCK_START
; generated by OrcaSlicer 2.3.0
; total layer number: 312
; total filament weight [g] : 152.30
; HEADER_BLOCK_END
G28
G1 X10 Y10
; filament used [g] = 152.30
; estimated printing time (normal mode) = 8h 44m 3s
; CONFIG_BLOCK_START
; filament_colour = #00C1AE;#F4E2C1
; filament_type = ASA;ASA
; CONFIG_BLOCK_END
"""


class PrintData:
    def __init__(self):
        self.root = temp_path("qidi-files-") / "printer_data"
        self.gcodes = self.root / "gcodes"
        self.cache = self.root / ".cache"
        for folder in (self.gcodes, self.cache, self.root / "config"):
            folder.mkdir(parents=True)
        self.context = context(self.root)
        FILES.setup(self.context)

    def call(self, method):
        async def ask():
            return await self.context.methods[method]({})
        return asyncio.run(ask())

    def captured(self):
        """The captured entries, as the app lists them under this printer_data."""
        return [entry.replace("/home/qidi/printer_data/", f"{self.root}/") for entry in CAPTURED]


class FileListTests(unittest.TestCase):
    def setUp(self):
        self.printer = PrintData()
        for name, plates in SLICED.items():
            write_3mf(self.printer.gcodes / name, plates)
        shutil.copy(self.printer.gcodes / "Little+Stitch.gcode.3mf", self.printer.cache / "Little+Stitch.gcode.3mf")
        (self.printer.gcodes / "Orca Head.gcode").write_text(ORCA_GCODE, encoding="utf-8")
        (self.printer.gcodes / "project.3mf").write_bytes(b"not listed")
        (self.printer.gcodes / "notes.txt").write_text("not listed", encoding="utf-8")

    def test_file_list_matches_the_apps_entries_with_the_print_copy_listed_twice(self):
        files = self.printer.call("qidiclient.files.list")

        self.assertEqual([dump(entry) for entry in files if entry["extension"] == ".3mf"], self.printer.captured())
        self.assertEqual([entry["filename"] for entry in files], [
            "Little+Stitch.gcode.3mf", "Campfire lamp.gcode.3mf", "Foldable_carrot.gcode.3mf",
            "Little+Stitch.gcode.3mf", "Orca Head.gcode"])
        self.assertEqual(self.printer.call("api.qidiclient.files.list"), files)

    def test_plain_gcode_lists_one_plate_with_the_slicers_comments(self):
        files = self.printer.call("qidiclient.files.list")
        entry = next(entry for entry in files if entry["extension"] == ".gcode")

        self.assertEqual(entry["filepath"], f"{self.printer.gcodes}/Orca Head.gcode")
        self.assertEqual((entry["plate_count"], entry["thumbnails"], entry["print_objects"]), ("1", [], []))
        self.assertEqual(entry["show_print_time"], " 8h 44m")
        self.assertEqual(entry["show_filament_weight"], "")
        plate = entry["plates"][0]
        self.assertEqual((plate["filament_colour"], plate["filament_type"]), ("#00C1AE;#F4E2C1", "ASA;ASA"))
        self.assertEqual((plate["bed_type"], plate["nozzle_diameter"], plate["used_extruders"]), ("", "", ""))
        self.assertEqual(plate["print_time_seconds"], 8 * 3600 + 44 * 60 + 3)

    def test_stats_count_plate_one_of_every_listed_file(self):
        stats = self.printer.call("qidiclient.files.stats")

        self.assertEqual(dump(stats), dump({
            "file_types": {".3mf": 4, ".gcode": 1},
            "root_path": str(self.printer.gcodes),
            "total_filament_weight_grams": 4.0 + 76 + 26 + 4 + 0,
            "total_files": 5,
            "total_plates": 1 + 3 + 1 + 1 + 1,
            "total_print_time_seconds": 1999.0 + 10487 + 5395 + 1999 + 31443,
        }))

    def test_a_file_is_parsed_again_only_when_its_time_or_size_changes(self):
        before = self.printer.call("qidiclient.files.list")
        path = self.printer.gcodes / "Foldable_carrot.gcode.3mf"
        stat = os.stat(path)
        path.write_bytes(b"\0" * stat.st_size)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        self.assertEqual(self.printer.call("qidiclient.files.list"), before)

        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        after = next(entry for entry in self.printer.call("qidiclient.files.list")
                     if entry["filepath"] == str(path))
        self.assertEqual((after["plate_count"], after["plates"], after["show_print_time"]), ("0", [], ""))

        path.unlink()
        self.assertNotIn(str(path), [entry["filepath"] for entry in self.printer.call("qidiclient.files.list")])


class FilamentListTests(unittest.TestCase):
    # sha256 of QIDI's app's reply captured on the Max 4 (01.01.07.01), identical on the Plus 5 and Smart 5, dumped
    # compact; the cfg is QIDI's stock officiall_filas_list.cfg.
    CAPTURED_SHA256 = "1e1363679f763f2c84334bae9df550ed1828ae5173e4a39dc660fe1bd21acaca"

    def test_filament_list_matches_the_apps_reply(self):
        printer = PrintData()
        shutil.copy(STOCK_FILAMENTS, printer.root / "config/officiall_filas_list.cfg")

        reply = printer.call("api.qidiclient.config.offical_filament_list")

        self.assertEqual(hashlib.sha256(dump(reply).encode("utf-8")).hexdigest(), self.CAPTURED_SHA256)
        self.assertEqual(printer.call("qidiclient.config.offical_filament_list"), reply)
        # The app's own color table, not the cfg's [colordict] (#898F9B there).
        self.assertEqual(reply["colordict"]["20"], "#898F8B")
        self.assertEqual((reply["filament"]["9"], reply["min_temp"]["9"]), ("", 0))


# ---- LAN ------------------------------------------------------------------------------------------------------------

class Stop(Exception):
    pass


class Transport:
    def __init__(self, ip):
        self.ip, self.sent, self.closed = ip, [], False

    def sendto(self, data, address):
        self.sent.append((data, address))

    def close(self):
        self.closed = True


class Lan:
    """The network as the SSDP sender sees it, changed between rounds by a script."""

    def __init__(self, script, *, moonraker=None, fail_opens=0, name="QD_MAX4"):
        self.script = list(script)
        self.network = self.script[0]
        self.transports, self.slept, self.fail_opens = [], [], fail_opens
        self.context = context(name=name, moonraker=moonraker)
        self.sender = LAN.SsdpSender(
            self.context, network=lambda: self.network, signal=lambda: 2, open_endpoint=self.open, sleep=self.sleep,
            mac=lambda interface: {"wlan0": "AA:BB:CC:00:00:01", "eth0": "AA:BB:CC:00:00:02"}[interface])

    async def open(self, ip):
        if self.fail_opens:
            self.fail_opens -= 1
            raise OSError("Address already in use")
        self.transports.append(Transport(ip))
        return self.transports[-1]

    async def sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) >= len(self.script):
            raise Stop
        self.network = self.script[len(self.slept)]

    def run(self):
        try:
            asyncio.run(self.sender.run())
        except Stop:
            pass
        return [(data.decode("utf-8"), address) for transport in self.transports for data, address in transport.sent]


def notify(host, *, ip, mac, signal, connect, bind, status, model="QD_MAX4", name="3DP-MAX4-DEF"):
    return (f"NOTIFY * HTTP/1.1\r\nHOST: {host}\r\nServer: UPnP/1.0\r\nLocation: {ip}\r\n"
            f"NT: urn:qiditech-com:device:3dprinter:1\r\nUSN: {SERIAL}\r\nCache-Control: max-age=1800\r\n"
            f"DevModel.qidi.com: {model}\r\nDevName.qidi.com: {name}\r\nDevMAC.qidi.com: {mac}\r\n"
            f"DevSignal.qidi.com: {signal}\r\nDevConnect.qidi.com: {connect}\r\nDevBind.qidi.com: {bind}\r\n"
            f"DevStatus.qidi.com: {status}\r\n\r\n")


BROADCAST = ("255.255.255.255", 5729)
MULTICAST = ("239.255.255.250", 5863)


class SsdpTests(unittest.TestCase):
    def test_announces_every_five_seconds_alternating_broadcast_and_multicast(self):
        # A printer linked before Valve Oil was installed is announced as never linked: it is kept off QIDI's cloud.
        lan = Lan([("192.168.1.50", "192.168.1.60")] * 3, moonraker=Moonraker({"user.token": "account-token"}))
        lan.context.status = {"print_stats": {"state": "printing"}}

        sent = lan.run()

        values = dict(ip="192.168.1.50", mac="AA:BB:CC:00:00:01", signal=2, connect="false", bind="false",
                      status="printing")
        self.assertEqual(sent, [
            (notify("255.255.255.255", **values), BROADCAST),
            (notify("239.255.255.250", **values), MULTICAST),
            (notify("255.255.255.255", **values), BROADCAST),
        ])
        self.assertEqual(lan.slept, [5.0, 5.0, 5.0])
        self.assertEqual(lan.transports[0].ip, "192.168.1.50")

    def test_stops_without_a_network_and_starts_again_with_the_new_values(self):
        lan = Lan([("192.168.1.50", None), ("192.168.1.50", None), (None, None), (None, "10.0.0.7"),
                   (None, "10.0.0.7")], name="S5")

        sent = lan.run()

        self.assertTrue(lan.transports[0].closed)
        self.assertEqual(lan.transports[1].ip, "10.0.0.7")
        before = dict(mac="AA:BB:CC:00:00:01", signal=2, connect="false", bind="false", status="standby", model="S5",
                      name="3DP-S5-DEF")
        after = dict(before, mac="AA:BB:CC:00:00:02", signal=0)
        self.assertEqual(sent, [
            (notify("255.255.255.255", ip="192.168.1.50", **before), BROADCAST),
            (notify("239.255.255.250", ip="192.168.1.50", **before), MULTICAST),
            (notify("255.255.255.255", ip="10.0.0.7", **after), BROADCAST),
            (notify("239.255.255.250", ip="10.0.0.7", **after), MULTICAST),
        ])

    def test_a_port_that_cannot_be_bound_is_tried_again_next_round(self):
        lan = Lan([("192.168.1.50", None)] * 3, fail_opens=1)

        sent = lan.run()

        self.assertEqual([address for _, address in sent], [BROADCAST, MULTICAST])


class MkswifiTests(unittest.TestCase):
    def ask(self, moonraker):
        async def exchange():
            forever = asyncio.Event()

            async def sleep(seconds):
                await forever.wait()

            responder = LAN.MkswifiResponder(
                context(name="QD_PLUS5", moonraker=moonraker), ipv4={"wlan0": "192.168.20.163"}.get,
                host="127.0.0.1", port=0, sleep=sleep)
            task = asyncio.ensure_future(responder.run())
            for _ in range(1000):
                if responder.address is not None:
                    break
                await asyncio.sleep(0)
            replies = asyncio.Queue()

            class Client(asyncio.DatagramProtocol):
                def datagram_received(self, data, addr):
                    replies.put_nowait(data)

            client, _ = await asyncio.get_event_loop().create_datagram_endpoint(
                Client, remote_addr=("127.0.0.1", responder.address[1]))
            try:
                client.sendto(b"M99999")
                client.sendto(b"mkswifi discovery")
                return await asyncio.wait_for(replies.get(), 5)
            finally:
                client.close()
                task.cancel()

        return asyncio.run(exchange()).decode("utf-8")

    def test_replies_with_the_apps_line_and_the_printers_alias(self):
        reply = self.ask(Moonraker({"user.alias": "Workshop"}))

        self.assertEqual(reply, f"mkswifi:PLUS5,0A:BD:11:88:2F:3C,192.168.20.163,moonraker,ready,ASCSDS,wifi,"
                                f"{SERIAL},Workshop")

    def test_replies_with_the_apps_default_alias_when_none_is_saved(self):
        self.assertTrue(self.ask(None).endswith(f",wifi,{SERIAL},3DP-ABC-DEF"))
        self.assertTrue(self.ask(Moonraker()).endswith(f",{SERIAL},3DP-ABC-DEF"))

    def test_listens_only_while_wifi_has_an_address(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        addresses = [None, "192.168.20.163", None]
        listening = []

        def bound():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    return True
            return False

        async def sleep(seconds):
            await asyncio.sleep(0)  # a closed transport lets go of its socket on the next turn of the loop
            listening.append(bound())
            addresses.pop(0)
            if not addresses:
                raise Stop

        responder = LAN.MkswifiResponder(context(), ipv4=lambda interface: addresses[0], host="127.0.0.1",
                                         port=port, sleep=sleep)
        with self.assertRaises(Stop):
            asyncio.run(responder.run())
        self.assertEqual(listening, [False, True, False])



BLOCK = _load("vo_qidi_block")
STOCK_HOSTS = "127.0.0.1\tlocalhost qidi-s5\n::1\t\tlocalhost ip6-localhost ip6-loopback\n127.0.1.1       qidi-s5\n"


class FakeIp:
    """The ip command's routes: prohibit routes by protocol, and one route of the printer's own."""

    def __init__(self):
        self.routes = {("192.168.1.0/24", "kernel")}

    def __call__(self, family, _route, action, *args):
        class Result:
            returncode, stderr, stdout = 0, "", ""
        result = Result()
        if action == "show":
            protocol = args[1]
            result.stdout = "".join(f"prohibit {address} \n" for address, owner in sorted(self.routes)
                                    if owner == protocol and (":" in address) == (family == "-6"))
        elif action == "replace":
            self.routes.add((args[1], args[3]))
        elif action == "del":
            self.routes.discard((args[1], args[3]))
        return result

    def blocked(self):
        return {address for address, owner in self.routes if owner == BLOCK.ROUTE_PROTOCOL}


class FakeSystemctl:
    def __init__(self, state):
        self.state, self.calls = state, []

    def __call__(self, *args):
        self.calls.append(args)

        class Result:
            stdout = self.state + "\n"
        if args[0] == "stop":
            self.state = "inactive"
        return Result()


class QidiBlockTests(unittest.TestCase):
    def setUp(self):
        root = temp_path("vo-qidi-block-")
        self.database, self.hosts, self.frpc = root / "moonraker-sql.db", root / "hosts", root / "frpc.json"
        self.hosts.write_text(STOCK_HOSTS)
        self.frpc.write_text(json.dumps({"serverAddr": "TEST", "serverPort": 7080}))
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE config (id INTEGER PRIMARY KEY AUTOINCREMENT, section TEXT NOT NULL, "
                       "key TEXT NOT NULL, value TEXT, UNIQUE(section, key))")
        self.ip = FakeIp()

    def settings(self, **values):
        with sqlite3.connect(self.database) as db:
            for name, value in values.items():
                section, key = name.split("__")
                db.execute("INSERT OR REPLACE INTO config (section, key, value) VALUES (?, ?, ?)", (section, key, value))

    def check(self):
        settings = BLOCK.read_settings(str(self.database))
        BLOCK.apply(*BLOCK.wanted(settings, BLOCK.frp_server(str(self.frpc))), hosts=str(self.hosts),
                       run=self.ip)

    def resolves_nowhere(self, name):
        return f"0.0.0.0 {name}\n" in self.hosts.read_text() and f":: {name}\n" in self.hosts.read_text()

    def test_qidi_and_third_parties_are_blocked_but_never_the_network(self):
        self.settings(general__lan_mode="0", mqtt__ip="192.168.1.20", p2p__connect="47.88.1.2:7781",
                      p2p__peer="relay7.qidi3dprinter.com:443")
        self.check()
        for name in ("api.qidimaker.com", "us-mqtt.qidimaker.com", "peershare.qidimaker.com",
                     "download.na.qidi3dprinter.com", "u.peergine.com", "ip-api.com", "relay7.qidi3dprinter.com"):
            self.assertTrue(self.resolves_nowhere(name), name)
        self.assertTrue(self.hosts.read_text().startswith(STOCK_HOSTS))
        # Never the public DNS servers QIDI's DHCP hook names, the printer's only ones on a network that names none.
        self.assertEqual(self.ip.blocked(), {"8.149.135.149", "203.107.6.88", "183.232.231.100", "47.88.1.2"})
        self.assertIn(("192.168.1.0/24", "kernel"), self.ip.routes)

    def test_an_unreadable_database_still_blocks_from_the_start(self):
        self.database.unlink()
        self.run_once(FakeSystemctl("inactive"), seen := [])
        self.assertEqual(seen, [(True, True)])

    def test_stopping_the_service_unblocks_everything(self):
        seen = []
        self.run_once(FakeSystemctl("inactive"), seen)
        self.assertEqual(seen, [(True, True)])
        self.assertEqual(self.hosts.read_text(), STOCK_HOSTS)
        self.assertEqual(self.ip.blocked(), set())
        self.assertEqual(self.ip.routes, {("192.168.1.0/24", "kernel")})

    def run_once(self, systemctl, seen=None):
        """Runs the service for one round, noting whether QIDI's API resolved nowhere and routes were in place."""
        previous = signal.getsignal(signal.SIGTERM)

        def sleep(_seconds):
            if seen is not None:
                seen.append((self.resolves_nowhere("api.qidimaker.com"), bool(self.ip.blocked())))
            os.kill(os.getpid(), signal.SIGTERM)

        BLOCK.time.sleep, real_sleep = sleep, BLOCK.time.sleep
        try:
            BLOCK.run(database=str(self.database), hosts=str(self.hosts), frpc=str(self.frpc), ip=self.ip,
                      systemctl=systemctl)
        finally:
            BLOCK.time.sleep = real_sleep
            signal.signal(signal.SIGTERM, previous)

    def test_qidis_frp_client_is_stopped_even_while_it_waits_to_restart(self):
        systemctl = FakeSystemctl("activating")
        self.run_once(systemctl)
        self.assertIn(("stop", "frpc.service"), systemctl.calls)


if __name__ == "__main__":
    unittest.main()
