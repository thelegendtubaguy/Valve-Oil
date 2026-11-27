from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from installer.tests.helpers import load_source, temp_path


def _load():
    path = Path(__file__).resolve().parents[2] / "system/vo_qidi_errors.py"
    return load_source("vo_qidi_errors_under_test", path)


# Excerpts of QIDI's definitions (QIDI_Client/resource/qidi_client_error) in the forms the printers carry.
DEFINITIONS = {
    "QDC_100_005_000_001": {
        "0": {"message": "klipper_common_warning", "message_translation": {}, "error_code": "QDC_100_005_000_001",
              "prossess_msg_translation": {}, "error_type": 0, "weight": 0,
              "popup_stlye": {"main_pic": "NO_PIC", "is_model": False}},
        "4": {"message": "Code:QDE_004_006",
              "message_translation": {"zh_CN": "挤出机进料失败。", "en_US": "Extruder loading failure."},
              "prossess_msg_translation": {"en_US": "The filament failed to enter the extruder."}},
    },
    "QDC_100_005_015_001": {
        "0": {"message": "klipper_common_error", "message_translation": {}, "error_code": "QDC_100_005_015_001",
              "prossess_msg_translation": {}, "error_type": 3, "weight": 15},
        "2": {"message": "Lost communication with MCU",
              "message_translation": {"en_US": "Communication with MCU lost"},
              "prossess_msg_translation": {"en_US": "Please click \"Restart Firmware\" and then retry printing."}},
    },
    "QDC_300_001_010_001": {
        "0": {"message": "Box耗材耗尽。",
              "message_translation": {"zh_CN": "Box耗材耗尽。", "en_US": "Box material exhausted."},
              "error_code": "QDC_300_001_010_001", "error_type": 2, "weight": 10, "popup_stlye": None,
              "prossess_msg_translation": {"en_US": "The BOX material is exhausted."}},
    },
    "QDC_200_001_005_001": {
        "0": {"message": "耗材插入", "message_translation": {"en_US": "Please insert the filament"},
              "error_code": "QDC_200_001_005_001", "error_type": 1, "weight": 5, "popup_stlye": None,
              "prossess_msg_translation": {"en_US": "Please insert the filament into the feeding port."}},
    },
    "QDC_300_002_005_005": {
        "0": {"message": "BOX 1 heating timeout", "message_translation": {"en_US": "BOX 1 heating timeout"},
              "error_code": "QDC_300_002_005_005", "error_type": 1, "weight": 5, "popup_stlye": None,
              "prossess_msg_translation": {"en_US": "BOX 1 experienced a heating timeout during heating and has "
                                                    "automatically turned off the heating. Please check if the lid is "
                                                    "closed."}},
    },
}
ENTRY_KEYS = ["error_code", "error_message", "error_popup", "error_type", "error_weight", "prossess_message",
              "timestamp"]


class FakeContext:
    """What vo_qidi_agent's Context gives a helper, with Moonraker's side driven by the test."""

    def __init__(self, printer_data):
        self.printer_data = str(printer_data)
        self.connected = True
        self.klippy_state = "ready"
        self.status = {}
        self.objects = {}
        self.methods = {}
        self.events = []
        self.config = {}
        self._status = []
        self._notifications = {}
        self._connected = []

    def subscribe(self, objects):
        self.objects.update(objects)

    def method(self, name, handler, *, api_alias=True):
        self.methods[name] = handler
        if api_alias:
            self.methods["api." + name] = handler

    def on_status(self, callback):
        self._status.append(callback)

    def on_notification(self, method, callback):
        self._notifications.setdefault(method, []).append(callback)

    def on_connected(self, callback):
        self._connected.append(callback)

    def background(self, coroutine_function):
        pass

    async def call(self, method, params=None, timeout=None):
        raise RuntimeError("no Moonraker in this test: " + method)

    async def config_get(self, section, key, default=None):
        return self.config.get(f"{section}.{key}", default)

    async def send_event(self, event, data):
        self.events.append((event, json.loads(json.dumps(data))))

    def print_state(self):
        return (self.status.get("print_stats") or {}).get("state")

    # Moonraker's side
    def update(self, changes):
        for name, fields in changes.items():
            self.status.setdefault(name, {}).update(fields)
        for callback in self._status:
            callback(changes)

    def notify(self, method, params):
        for callback in self._notifications.get(method, ()):
            result = callback(params)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)


class Clock:
    def __init__(self):
        self.now = 1795141800.5

    def __call__(self):
        return self.now


class QidiErrorListTests(unittest.TestCase):
    """The error list QIDI's phone app shows, kept and sent as QIDI's touchscreen app did."""

    def setUp(self):
        self.module = _load()
        root = temp_path("vo-qidi-errors-")
        for code, files in DEFINITIONS.items():
            (root / "definitions" / code).mkdir(parents=True)
            for number, definition in files.items():
                (root / "definitions" / code / f"{number}.json").write_text(
                    json.dumps(definition, ensure_ascii=False), encoding="utf-8")
        (root / "mounts").write_text("", encoding="utf-8")
        self.context = FakeContext(root / "printer_data")
        self.errors = self.module.setup(self.context, definitions=str(root / "definitions"),
                                        manifest=str(root / "missing.json"), mounts=str(root / "mounts"))
        self.clock = Clock()
        self.errors.clock = self.clock
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def drive(self, action):
        """Run what Moonraker would deliver, then let the events it sends go out."""
        async def run():
            action()
            for _ in range(5):
                await asyncio.sleep(0)
        self.loop.run_until_complete(run())

    def sent(self):
        events, self.context.events[:] = list(self.context.events), []
        self.assertTrue(all(name == "ui_assistant_message_update" for name, _ in events))
        return [(data["event"], data["results"]) for _, data in events]

    def all_errors(self):
        return self.context.methods["get_all_error_list"]({})

    def test_get_all_error_list_answers_the_apps_shape_without_an_api_alias(self):
        self.assertNotIn("api.get_all_error_list", self.context.methods)
        self.assertEqual(self.all_errors(), {"event": "GetAll", "results": []})

        self.drive(lambda: self.context.notify("notify_gcode_response", ["!! Must home axis first: 154.300 183.800"]))

        listed = self.all_errors()
        self.assertEqual(listed["event"], "GetAll")
        self.assertEqual([sorted(entry) for entry in listed["results"]], [ENTRY_KEYS])
        self.assertEqual(listed["results"][0], {
            "error_code": "QDC_100_005_000_001",
            "error_message": "!! Must home axis first: 154.300 183.800",
            "error_popup": True,
            "error_type": 0,
            "error_weight": 0,
            "prossess_message": "!! Must home axis first: 154.300 183.800",
            "timestamp": 1795141800500,
        })

    def test_a_klipper_warning_is_sent_once_and_refreshed_by_repeats(self):
        self.drive(lambda: self.context.notify("notify_gcode_response", ["// Box heating", "!! Move out of range"]))
        [(event, entry)] = self.sent()
        self.assertEqual((event, entry["error_message"]), ("add", "!! Move out of range"))

        self.clock.now += 5
        self.drive(lambda: self.context.notify(
            "notify_gcode_response", ["!! Code:QDE_004_006; Message:Extruder loading failure."]))

        self.assertEqual(self.sent(), [])
        [entry] = self.all_errors()["results"]
        self.assertEqual(entry["error_message"], "Extruder loading failure.")
        self.assertEqual(entry["prossess_message"], "The filament failed to enter the extruder.")
        self.assertEqual(entry["timestamp"], 1795141805500)

    def test_a_klipper_error_is_added_on_shutdown_and_removed_when_klipper_is_ready(self):
        self.drive(lambda: self.context.update(
            {"webhooks": {"state": "startup", "state_message": "Printer is not ready"}}))
        self.assertEqual(self.sent(), [])

        self.drive(lambda: self.context.update({"webhooks": {
            "state": "shutdown", "state_message": "Lost communication with MCU 'mcu'\nOnce the underlying issue..."}}))
        [(event, added)] = self.sent()
        self.assertEqual(event, "add")
        self.assertEqual(added["error_code"], "QDC_100_005_015_001")
        self.assertEqual(added["error_message"], "Communication with MCU lost")
        self.assertEqual(added["prossess_message"], "Please click \"Restart Firmware\" and then retry printing.")
        self.assertEqual((added["error_type"], added["error_weight"], added["error_popup"]), (3, 15, False))

        self.clock.now += 30
        self.drive(lambda: self.context.update({"webhooks": {"state": "ready", "state_message": "Printer is ready"}}))
        self.assertEqual(self.sent(), [("remove", added)])
        self.assertEqual(self.all_errors()["results"], [])

    def test_an_unmatched_klipper_error_keeps_klippers_text(self):
        self.drive(lambda: self.context.update(
            {"webhooks": {"state": "error", "state_message": "Option 'x' is not valid"}}))
        [(_, entry)] = self.sent()
        self.assertEqual((entry["error_message"], entry["prossess_message"]),
                         ("Option 'x' is not valid", "Option 'x' is not valid"))

    def test_box_and_print_conditions_are_listed_while_they_hold(self):
        warning = ("BOX 1 experienced a heating timeout during heating and has automatically turned off the heating. "
                   "Please check if the lid is closed.")
        self.drive(lambda: self.context.update({
            "print_stats_manager": {"sub_status": "box_filament_exhausted"},
            "multi_color_controller": {"task": {"current_step": "EXT_BITE", "is_paused": True},
                                       "box_warning_info": [warning]},
        }))
        self.assertEqual(sorted((event, entry["error_code"]) for event, entry in self.sent()), [
            ("add", "QDC_200_001_005_001"), ("add", "QDC_300_001_010_001"), ("add", "QDC_300_002_005_005")])
        self.assertEqual(self.all_errors()["results"][0]["error_message"], "Box material exhausted.")

        self.drive(lambda: self.context.update({
            "print_stats_manager": {"sub_status": ""},
            "multi_color_controller": {"task": {"current_step": "EXT_BITE", "is_paused": False},
                                       "box_warning_info": [warning + " "]},
        }))
        self.assertEqual(sorted((event, entry["error_code"]) for event, entry in self.sent()), [
            ("remove", "QDC_200_001_005_001"), ("remove", "QDC_300_001_010_001"), ("remove", "QDC_300_002_005_005")])

    def test_a_code_without_a_definition_is_skipped(self):
        self.drive(lambda: self.context.update({"print_stats_manager": {"sub_status": "ext_filament_exhausted"}}))

        self.assertEqual(self.sent(), [])
        self.assertEqual(self.all_errors()["results"], [])


if __name__ == "__main__":
    unittest.main()
