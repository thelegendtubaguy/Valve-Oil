from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest

from installer.runtime import qidi_agent
from installer.tests.helpers import temp_path

AGENT = qidi_agent.payload_module("vo_qidi_agent")
ETX = b"\x03"


class FakeMoonraker:
    """Moonraker's Unix socket: answers calls, records them, and sends requests and notifications to the client."""

    def __init__(self, *, klippy_state="ready", status=None):
        self.path = os.path.join(tempfile.mkdtemp(prefix="vo-mr-", dir="/tmp"), "moonraker.sock")
        self.calls = []
        self.klippy_state = klippy_state
        self.status = status or {}
        self.writer = None
        self.connected = asyncio.Event()
        self.replies = {}
        self._next = 1000

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, path=self.path)

    async def stop(self):
        if self.writer is not None:
            self.writer.close()
        self.server.close()
        await self.server.wait_closed()

    def _send(self, message):
        self.writer.write(json.dumps(message).encode() + ETX)

    async def _client(self, reader, writer):
        self.writer = writer
        buffer = b""
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            buffer += chunk
            while ETX in buffer:
                raw, buffer = buffer.split(ETX, 1)
                message = json.loads(raw)
                if "method" not in message:
                    self.replies[message["id"]].set_result(message)
                    continue
                self.calls.append((message["method"], message.get("params")))
                self._send({"jsonrpc": "2.0", "id": message["id"], "result": self._answer(message)})
                if message["method"] == "server.connection.identify":
                    self.connected.set()

    def _answer(self, message):
        method = message["method"]
        if method == "server.info":
            return {"klippy_state": self.klippy_state}
        if method == "printer.objects.subscribe":
            return {"status": self.status, "eventtime": 1.0}
        if method == "server.database.config.select":
            params = message["params"]
            return {"status": "success", "result": {f"{params['section']}.{params['key']}": params.get("default")}}
        return "ok"

    async def request(self, method, params=None):
        """Call a method on the connected agent, as Moonraker does for QIDI's components."""
        self._next += 1
        future = asyncio.get_event_loop().create_future()
        self.replies[self._next] = future
        message = {"jsonrpc": "2.0", "method": method, "id": self._next}
        if params:
            message["params"] = params
        self._send(message)
        return await asyncio.wait_for(future, 2)

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def gcode(self):
        return [params["script"] for method, params in self.calls if method == "printer.gcode.script"]

    def config_updates(self):
        return [(params["key"], params["value"]) for method, params in self.calls
                if method == "server.database.config.update" and params["section"] == "print_state"]


def run(coroutine):
    return asyncio.run(coroutine)


async def settle(times=5):
    for _ in range(times):
        await asyncio.sleep(0.01)


class AgentConnectionTests(unittest.TestCase):
    def test_the_agent_stands_in_for_the_app_and_answers_its_methods(self):
        async def scenario():
            moonraker = FakeMoonraker()
            await moonraker.start()
            context = AGENT.Context("QD_MAX4", version="01.01.07.01", printer_data=str(temp_path("vo-agent-")))
            context.method("qidiclient.files.stats", lambda params: {"total_files": 0})
            service = asyncio.ensure_future(AGENT.serve(context, socket_path=moonraker.path, patched=lambda: True))
            await asyncio.wait_for(moonraker.connected.wait(), 2)
            identify = moonraker.calls[0]
            # QIDI's components find the agent by the app's own identity.
            self.assertEqual(identify, ("server.connection.identify", {
                "client_name": "QD_MAX4", "version": "01.01.07.01", "type": "agent", "url": "local"}))
            answered = await moonraker.request("api.qidiclient.files.stats")
            unknown = await moonraker.request("qidiclient.unknown")
            service.cancel()
            await moonraker.stop()
            return answered, unknown

        answered, unknown = run(scenario())
        self.assertEqual(answered["result"], {"total_files": 0})
        self.assertEqual(unknown["error"], {"code": -32601, "message": "Method not found: qidiclient.unknown"})

    def test_an_app_that_still_does_its_duties_keeps_them(self):
        async def scenario():
            moonraker = FakeMoonraker()
            await moonraker.start()
            context = AGENT.Context("QD_MAX4", version="01.01.07.01", printer_data="/nonexistent")
            AGENT.APP_CHECK_SECONDS = 0.01
            service = asyncio.ensure_future(AGENT.serve(context, socket_path=moonraker.path, patched=lambda: False))
            await settle(10)
            service.cancel()
            await moonraker.stop()
            return moonraker.calls

        self.assertEqual(run(scenario()), [])

    def test_the_patched_app_is_told_by_its_takeover_site(self):
        app = temp_path("vo-app-") / "qidiclient"
        app.write_bytes(b"\0" * 16 + b"display\0" + b"\0" * 8)
        builds = {str(app.stat().st_size): {"site": [16, b"display\0".hex()], "name": "S5"}}
        self.assertEqual(AGENT.patched_app(str(app), builds)["name"], "S5")
        app.write_bytes(b"\0" * 16 + b"agent\0\0\0" + b"\0" * 8)
        self.assertIsNone(AGENT.patched_app(str(app), builds))
        self.assertIsNone(AGENT.patched_app(str(app), {}))


def printing(**fields):
    """A status update during a print."""
    return {"print_stats": {"state": "printing", "info": {"current_layer": fields.pop("layer", 3)}}, **fields}


class PrintStateTests(unittest.TestCase):
    def recorder(self):
        written = []

        async def write(key, value):
            written.append((key, value))

        context = AGENT.Context("QD_MAX4", version="1", printer_data="/nonexistent")
        return AGENT.PrintStateRecorder(context, write=write), written

    def test_the_print_state_is_written_as_qidis_app_writes_it(self):
        async def scenario():
            recorder, written = self.recorder()
            writer = asyncio.ensure_future(recorder._writer())
            recorder.update(printing(
                gcode_move={"extrude_factor": 1.0, "speed_2": 250.7, "speed_factor_2": 1 / 60,
                            "absolute_coordinates": True, "absolute_extrude": False,
                            "gcode_position": [10.0, 20.5, 0.6, 1234.5], "homing_position": [0.0, 0.0, -0.02, 0.0],
                            "base_position": [0.0, 0.0, 0.0, 0.0]},
                extruder={"target": 219.6}, heater_bed={"target": 60.0},
                **{"heater_generic chamber": {"target": 0.0}, "fan_generic cooling_fan": {"speed": 0.6}},
                virtual_sdcard={"file_position": 900, "plate_index": "1"},
                exclude_object={"excluded_objects": [], "objects": [{"name": "a", "center": [1.0, 2.0],
                                                                     "polygon": [[0.0, 0.0]]}]}))
            await settle()
            first = list(written)
            # Past 1024 bytes the record is written: each field that differs from the last record, in the app's
            # order and string forms; fields at the app's defaults (absolute positioning, a 0 chamber target, idle fans)
            # aren't written.
            recorder.update({"virtual_sdcard": {"file_position": 2000}})
            await settle()
            second = list(written[len(first):])
            # Only what changed is written again, and only once the file moved another 1024 bytes.
            recorder.update({"gcode_move": {"gcode_position": [11.0, 20.5, 0.6, 1300.0]},
                             "virtual_sdcard": {"file_position": 2500}})
            await settle()
            recorder.update({"virtual_sdcard": {"file_position": 3100}})
            await settle()
            third = list(written[len(first) + len(second):])
            writer.cancel()
            return first, second, third

        first, second, third = run(scenario())
        self.assertEqual(first, [])
        self.assertEqual(second, [
            ("extrude_factor", "1.000000"), ("speed", "250.000000"), ("speed_factor", "0.016667"),
            ("current_layer", "3"), ("absolute_extrude", "0"), ("base_position", "[0.0,0.0,0.0,0.0]"),
            ("gcode_position", "[10.0,20.5,0.6,1234.5]"), ("homing_position", "[0.0,0.0,-0.02,0.0]"),
            ("extruder_target", "220"), ("heater_bed_target", "60"), ("cooling_fan_speed", "0.600000"),
            ("file_position", "2000"), ("plate_index", "1"), ("exclude_objects", "[]"),
            ("all_objects", '[{"center":[1.0,2.0],"name":"a","polygon":[[0.0,0.0]]}]'),
        ])
        self.assertEqual(third, [("gcode_position", "[11.0,20.5,0.6,1300.0]"), ("file_position", "3100")])

    def test_a_new_print_writes_its_record_from_the_start(self):
        async def scenario():
            recorder, written = self.recorder()
            writer = asyncio.ensure_future(recorder._writer())
            recorder.update(printing(gcode_move={"extrude_factor": 1.0}, virtual_sdcard={"file_position": 5000}))
            recorder.update({"print_stats": {"state": "complete"}})
            await settle()
            written.clear()
            recorder.update(printing(gcode_move={"extrude_factor": 1.0}, virtual_sdcard={"file_position": 1500}))
            await settle()
            writer.cancel()
            return written

        self.assertEqual(run(scenario()), [("extrude_factor", "1.000000"), ("current_layer", "3"),
                                           ("file_position", "1500")])


class KlipperErrorResumeTests(unittest.TestCase):
    def scenario(self, *, layer, message):
        async def go():
            moonraker = FakeMoonraker()
            await moonraker.start()
            context = AGENT.Context("QD_PLUS5", version="1", printer_data="/nonexistent")
            states = iter(["ready", "shutdown", "startup", "ready", "ready"])
            moonraker._answer_original = moonraker._answer

            def answer(message):
                if message["method"] == "server.info":
                    return {"klippy_state": next(states, "ready")}
                return moonraker._answer_original(message)
            moonraker._answer = answer

            async def no_wait(seconds):
                await asyncio.sleep(0)
            resume = AGENT.ErrorResume(context, sleep=no_wait)
            service = asyncio.ensure_future(AGENT.serve(context, socket_path=moonraker.path, patched=lambda: True))
            await asyncio.wait_for(moonraker.connected.wait(), 2)
            await settle()
            context._status_update(printing(layer=layer))
            context._status_update({"webhooks": {"state": "shutdown", "state_message": message}})
            if resume.running is not None:
                await asyncio.wait_for(resume.running, 2)
            service.cancel()
            await moonraker.stop()
            return [method for method, _ in moonraker.calls if method in (
                "printer.gcode.script", "printer.print.power_off_print")], moonraker.gcode()
        return run(go())

    def test_a_lost_mainboard_mid_print_restarts_klipper_and_resumes_the_print(self):
        calls, gcode = self.scenario(layer=12, message="Lost communication with MCU 'mcu'")
        self.assertEqual(calls, ["printer.gcode.script", "printer.print.power_off_print"])
        self.assertEqual(gcode, ["FIRMWARE_RESTART"])

    def test_first_layer_and_other_errors_are_not_resumed(self):
        self.assertEqual(self.scenario(layer=1, message="Lost communication with MCU 'mcu'")[0], [])
        self.assertEqual(self.scenario(layer=12, message="ADC out of range")[0], [])


class AiPauseAndBoxCountTests(unittest.TestCase):
    def test_spaghetti_pauses_a_print_once(self):
        async def scenario():
            moonraker = FakeMoonraker()
            await moonraker.start()
            context = AGENT.Context("S5", version="1", printer_data="/nonexistent")
            AGENT.AiPause(context)
            service = asyncio.ensure_future(AGENT.serve(context, socket_path=moonraker.path, patched=lambda: True))
            await asyncio.wait_for(moonraker.connected.wait(), 2)
            await settle()
            report = {"save_variables": {"variables": {"qdc_ai_error_code": "noodle=1 status=1 time=1"}}}
            context._status_update({"print_stats": {"state": "standby"}, **report})
            context._status_update({"print_stats": {"state": "printing"}})
            context._status_update({"save_variables": {"variables": {"qdc_ai_error_code": "foreign=1 status=1 time=2"}}})
            context._status_update({"save_variables": {"variables": {"qdc_ai_error_code": "noodle=1 status=1 time=3"}}})
            context._status_update({"save_variables": {"variables": {"qdc_ai_error_code": "noodle=1 status=1 time=4"}}})
            await settle()
            service.cancel()
            await moonraker.stop()
            return moonraker.gcode()

        # Not while idle, not for a foreign object, and once per print.
        self.assertEqual(run(scenario()), ["PAUSE W=1"])

    def test_box_count_is_saved_from_the_usb_boxes_when_it_differs(self):
        async def scenario(saved):
            moonraker = FakeMoonraker(status={"save_variables": {"variables": {"box_count": saved}}})
            await moonraker.start()
            context = AGENT.Context("QD_MAX4", version="1", printer_data="/nonexistent")
            AGENT.BoxCount(context, count=lambda: 2)
            service = asyncio.ensure_future(AGENT.serve(context, socket_path=moonraker.path, patched=lambda: True))
            await asyncio.wait_for(moonraker.connected.wait(), 2)
            await settle()
            service.cancel()
            await moonraker.stop()
            return moonraker.gcode()

        self.assertEqual(run(scenario(1)), ["SAVE_VARIABLE VARIABLE=box_count VALUE=2"])
        self.assertEqual(run(scenario(2)), [])


class InstalledFilesTests(unittest.TestCase):
    def test_installed_services_carry_the_reviewed_app_builds(self):
        for name in ("vo_qidi_agent", "vo_usb_mount"):
            source = qidi_agent.installed_source(name)
            builds = json.loads(source.split("APP_BUILDS = ", 1)[1].split("\n", 1)[0])
            self.assertEqual({entry["name"] for entry in builds.values()}, {"QD_MAX4", "QD_PLUS5", "S5"})
