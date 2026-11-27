from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import shutil
import sys
import types
import unittest

from installer.runtime import moonraker_fixes
from installer.tests.helpers import temp_path

MQTT_MODULE = "moonraker.components.mqtt"
# Upstream Moonraker v0.9.3's moonraker/components/mqtt.py (sha256 7153770c...), with the contact address left out of
# its copyright line.
UPSTREAM_MQTT_SHA256 = "115f3d101a7de53f11e3ab9abc232a17c3351a67566f692717c8de45d3e69d28"
UPSTREAM_FIRST_LINE = "# MQTT client implementation for Moonraker\n"
UPSTREAM_ENDING = "\n\ndef load_component(config: ConfigHelper) -> MQTTClient:\n    return MQTTClient(config)\n"
VALVE_OIL_SECTION = "\n\n\n# ---- Valve Oil ----"


def load_payload(path=moonraker_fixes.MODULE_PAYLOAD):
    spec = importlib.util.spec_from_file_location("vo_moonraker_fixes_under_test", path)
    module = importlib.util.module_from_spec(spec)
    before = list(sys.meta_path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.meta_path[:] = before  # the payload registers its import hooks when imported
    return module


class Stubs:
    """Stand-ins for what upstream's MQTT component imports from Moonraker and paho, installed for one test."""

    def __init__(self, test):
        paho = types.ModuleType("paho")
        paho_mqtt = types.ModuleType("paho.mqtt")
        paho_mqtt.__version__ = "1.6.1"
        client = types.ModuleType("paho.mqtt.client")
        client.Client = type("Client", (), {})
        client.MQTTv31, client.MQTTv311, client.MQTTv5 = 3, 4, 5
        paho.mqtt, paho_mqtt.client = paho_mqtt, client
        common = types.ModuleType("moonraker.common")
        for name in ("TransportType", "RequestType", "WebRequest", "APITransport", "KlippyState"):
            setattr(common, name, type(name, (), {}))
        utils = types.ModuleType("moonraker.utils")
        utils.json_wrapper = types.ModuleType("moonraker.utils.json_wrapper")
        packages = {"moonraker": types.ModuleType("moonraker"),
                    "moonraker.components": types.ModuleType("moonraker.components")}
        for package in packages.values():
            package.__path__ = []
        modules = {**packages, "moonraker.common": common, "moonraker.utils": utils,
                   "moonraker.utils.json_wrapper": utils.json_wrapper, "paho": paho, "paho.mqtt": paho_mqtt,
                   "paho.mqtt.client": client}
        saved = {name: sys.modules.get(name) for name in (*modules, MQTT_MODULE)}
        sys.modules.update(modules)
        sys.modules.pop(MQTT_MODULE, None)
        test.addCleanup(lambda: [sys.modules.pop(name, None) if module is None
                                 else sys.modules.__setitem__(name, module) for name, module in saved.items()])


class ServerError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class Config:
    def __init__(self, sections):
        self.sections = sections
        self.server = types.SimpleNamespace(error=ServerError)

    def get_server(self):
        return self.server

    def has_section(self, section):
        return section in self.sections


class MoonrakerMqttTests(unittest.TestCase):
    """Moonraker's own MQTT component, for the operator's broker, takes the place of QIDI's cloud-only one."""

    def installed(self):
        """The fixes module and the MQTT component as installed side by side in Moonraker's virtualenv."""
        site_packages = temp_path("vo-site-packages-")
        shutil.copy(moonraker_fixes.MODULE_PAYLOAD, site_packages / f"{moonraker_fixes.MODULE_NAME}.py")
        shutil.copy(moonraker_fixes.MQTT_PAYLOAD, site_packages / "vo_moonraker_mqtt.py")
        return site_packages, load_payload(site_packages / f"{moonraker_fixes.MODULE_NAME}.py")

    def import_mqtt(self, fixes):
        Stubs(self)
        finder = fixes._MqttFinder()
        sys.meta_path.insert(0, finder)
        self.addCleanup(lambda: finder in sys.meta_path and sys.meta_path.remove(finder))
        return importlib.import_module(MQTT_MODULE)

    def test_the_component_is_upstream_moonrakers_unchanged(self):
        source = moonraker_fixes.mqtt_source()
        upstream = source[source.index(UPSTREAM_FIRST_LINE):source.index(VALVE_OIL_SECTION)] + UPSTREAM_ENDING
        self.assertEqual(hashlib.sha256(upstream.encode()).hexdigest(), UPSTREAM_MQTT_SHA256)

    def test_moonraker_loads_it_in_place_of_qidis(self):
        site_packages, fixes = self.installed()
        module = self.import_mqtt(fixes)
        self.assertEqual(module.__file__, str(site_packages / "vo_moonraker_mqtt.py"))

    def test_an_mqtt_section_connects_to_the_operators_broker(self):
        _site_packages, fixes = self.installed()
        module = self.import_mqtt(fixes)
        module.MQTTClient = lambda config: ("upstream client", config)
        config = Config({"mqtt"})
        self.assertEqual(module.load_component(config), ("upstream client", config))

    def test_without_an_mqtt_section_it_connects_nowhere_and_never_fails_to_load(self):
        _site_packages, fixes = self.installed()
        module = self.import_mqtt(fixes)
        module.MQTTClient = lambda config: self.fail("connected without an [mqtt] section")
        mqtt = module.load_component(Config(set()))
        self.assertFalse(mqtt.is_connected())
        for publish in (lambda: mqtt.publish_topic("printer/status", {}), lambda: mqtt.subscribe_topic("x", print)):
            with self.assertRaises(ServerError):
                publish()
        asyncio.run(mqtt.close())

    def test_without_the_component_file_qidis_loads(self):
        site_packages, fixes = self.installed()
        (site_packages / "vo_moonraker_mqtt.py").unlink()
        self.assertIsNone(fixes._MqttFinder().find_spec(MQTT_MODULE))


class WebRequest:
    """moonraker.common.WebRequest, as QIDI's handlers read it."""

    def __init__(self, args):
        self.args = args

    def get(self, key, default=None):
        return self.args.get(key, default)


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


def database_class():
    class MoonrakerDatabase:
        """QIDI's database component: its config table, the in-memory copy it keeps, and its config handlers."""

        def __init__(self, table):
            self.table = table
            self.config_items = {"general.lan_mode": "0", "general.sound": "1"}
            self.reads = 0
            self.mqtt_calls = []

        async def get_value_from_config(self, section, key):
            self.reads += 1
            value = self.table.get(f"{section}.{key}")
            return Cursor(None if value is None else {"value": value})

        async def update_value_from_config(self, section, key, value):
            self.table[f"{section}.{key}"] = value

        async def _init_config_items(self):
            for key in self.config_items:
                if key in self.table:
                    self.config_items[key] = self.table[key]

        async def _handle_config_update_request(self, web_request):
            section, key, value = (web_request.get(name) for name in ("section", "key", "value"))
            self.table[f"{section}.{key}"] = self.config_items[f"{section}.{key}"] = value
            if f"{section}.{key}" == "general.lan_mode":
                # QIDI's handler closes MQTT, or connects QIDI's, and starts or stops FRP.
                self.mqtt_calls.append(value)
            return {"status": "success", "result": {f"{section}.{key}": value}}

        async def _handle_config_update_batch_request(self, web_request):
            for item in web_request.get("items"):
                name = f"{item['section']}.{item['key']}"
                self.table[name] = self.config_items[name] = item["value"]
            return {"status": "success"}

        async def _handle_config_delete_request(self, web_request):
            section = web_request.get("section")
            for name in [name for name in self.table if name.startswith(f"{section}.")]:
                del self.table[name]
            self.config_items["general.lan_mode"] = "0"  # QIDI's default
            return {"status": "success"}
    return MoonrakerDatabase


class LanOnlyModeTests(unittest.TestCase):
    """QIDI's LAN-only mode stays on, so QIDI's app shows LAN Only Mode and keeps its own cloud requests in."""

    def setUp(self):
        self.fixes = load_payload()
        self.database_class = database_class()
        self.assertTrue(self.fixes.apply_database(types.SimpleNamespace(MoonrakerDatabase=self.database_class)))

    def started(self, table):
        database = self.database_class(table)
        asyncio.run(database._init_config_items())
        return database

    def test_moonraker_starts_with_lan_only_mode_on(self):
        for table in ({"general.lan_mode": "0"}, {}):
            with self.subTest(table=dict(table)):
                database = self.started(table)
                self.assertEqual((table["general.lan_mode"], database.config_items["general.lan_mode"]), ("1", "1"))

    def test_turning_it_off_answers_on_and_never_reaches_qidis_mqtt_or_frp(self):
        database = self.started({"general.lan_mode": "1"})
        result = asyncio.run(database._handle_config_update_request(
            WebRequest({"section": "general", "key": "lan_mode", "value": "0"})))
        self.assertEqual(result, {"status": "success", "result": {"general.lan_mode": "1"}})
        self.assertEqual((database.table["general.lan_mode"], database.config_items["general.lan_mode"]), ("1", "1"))
        self.assertEqual(database.mqtt_calls, [])

    def test_batch_writes_and_deletes_leave_it_on(self):
        database = self.started({"general.lan_mode": "1"})
        asyncio.run(database._handle_config_update_batch_request(WebRequest({"items": [
            {"section": "general", "key": "lan_mode", "value": "0"}, {"section": "general", "key": "sound", "value": "0"}]})))
        self.assertEqual((database.table["general.lan_mode"], database.table["general.sound"]), ("1", "0"))
        asyncio.run(database._handle_config_delete_request(WebRequest({"section": "general"})))
        self.assertEqual((database.table, database.config_items["general.lan_mode"]), ({"general.lan_mode": "1"}, "1"))

    def test_other_writes_add_no_database_reads(self):
        database = self.started({"general.lan_mode": "1"})
        reads = database.reads
        for value in ("0", "1", "0"):
            asyncio.run(database._handle_config_update_request(
                WebRequest({"section": "general", "key": "sound", "value": value})))
        self.assertEqual(database.reads, reads)

    def test_unrecognized_database_code_is_left_alone(self):
        self.assertFalse(self.fixes.apply_database(types.SimpleNamespace()))
        # Patching twice wraps QIDI's handlers once.
        handler = self.database_class._handle_config_update_request
        self.assertTrue(self.fixes.apply_database(types.SimpleNamespace(MoonrakerDatabase=self.database_class)))
        self.assertIs(self.database_class._handle_config_update_request, handler)


class Agent:
    def __init__(self, name, url):
        self.client_data = {"name": name, "url": url, "type": "agent"}


class Request:
    def __init__(self, **args):
        self.args = args

    def get_str(self, name, default=None):
        return self.args.get(name, default)


class AgentSelectionTests(unittest.TestCase):
    def test_qidiclient_endpoints_reach_the_app_agent_when_a_slicer_is_an_agent_too(self):
        class ExtensionManager:
            def __init__(self, agents):
                self.agents = agents

            def _select_qidiclient(self, web_request):
                name = web_request.get_str("agent", None)
                if name is not None:
                    return self.agents[name]
                if len(self.agents) > 1:
                    raise RuntimeError("Multiple agents connected, specify 'agent' parameter")
                return next(iter(self.agents.values()))

        fixes = load_payload()
        self.assertTrue(fixes.apply_extensions(types.SimpleNamespace(ExtensionManager=ExtensionManager)))
        slicer, app = Agent("OrcaSlicer", "https://example.invalid"), Agent("QD_MAX4", "local")
        manager = ExtensionManager({"OrcaSlicer": slicer, "QD_MAX4": app})
        self.assertIs(manager._select_qidiclient(Request()), app)
        self.assertIs(manager._select_qidiclient(Request(agent="OrcaSlicer")), slicer)
        without_app = ExtensionManager({"OrcaSlicer": slicer, "Other": Agent("Other", "x")})
        with self.assertRaises(RuntimeError):
            without_app._select_qidiclient(Request())


def dbus_manager_class(answers):
    """QIDI's DbusManager: its startup asks the touchscreen app and then Klipper over D-Bus, with no time limit, and
    only logs their answers. `answers` maps a service to how long it takes to answer, None for never."""
    class DbusManager:
        def __init__(self):
            self.startup_answers = None

        async def component_init(self):
            qidi_client = await self.send_to_qidi_client("query_methods_list", {})
            klipper = await self.send_to_klipper("query_methods_list", {})
            self.startup_answers = (qidi_client, klipper)

        async def send_request(self, service, obj_path, interface, method_name, payload):
            try:
                delay = answers[service]
                await asyncio.sleep(delay) if delay is not None else await asyncio.Event().wait()
                return {"methods": ["ping", method_name]}
            except Exception as e:
                return {"error": "Low-level DBus call failed", "details": str(e)}

        async def send_to_klipper(self, method, payload):
            return await self.send_request("org.qidi.klipper", "/org/qidi/klipper", "org.qidi.klipper", method, payload)

        async def send_to_qidi_client(self, method, payload):
            return await self.send_request("org.qidi.qidi-client", "/org/qidi/qidi_client", "org.qidi.qidi_client", method, payload)
    return DbusManager


class MoonrakerDbusStartupTests(unittest.TestCase):
    def setUp(self):
        self.fixes = load_payload()
        self.fixes.DBUS_STARTUP_CALL_TIMEOUT = 0.1

    def manager(self, answers):
        manager_class = dbus_manager_class(answers)
        self.assertTrue(self.fixes.apply_dbus(types.SimpleNamespace(DbusManager=manager_class)))
        return manager_class()

    def test_a_klipper_that_does_not_answer_no_longer_stops_moonraker_starting(self):
        manager = self.manager({"org.qidi.qidi-client": 0, "org.qidi.klipper": None})
        asyncio.run(asyncio.wait_for(manager.component_init(), 1))
        qidi_client, klipper = manager.startup_answers
        self.assertEqual(qidi_client, {"methods": ["ping", "query_methods_list"]})
        self.assertEqual(klipper["error"], "Low-level DBus call failed")

    def test_calls_after_startup_keep_qidi_s_wait(self):
        manager = self.manager({"org.qidi.qidi-client": 0, "org.qidi.klipper": 0.3})

        async def run():
            await manager.component_init()
            return await manager.send_to_klipper("ping", {})
        self.assertEqual(asyncio.run(run()), {"methods": ["ping", "ping"]})
        # The same answer came too late for the startup check.
        self.assertEqual(manager.startup_answers[1]["error"], "Low-level DBus call failed")

    def test_unrecognized_dbus_code_is_left_alone(self):
        self.assertFalse(self.fixes.apply_dbus(types.SimpleNamespace()))
        # Patching twice wraps QIDI's methods once.
        manager_class = dbus_manager_class({})
        self.assertTrue(self.fixes.apply_dbus(types.SimpleNamespace(DbusManager=manager_class)))
        send_request = manager_class.send_request
        self.assertTrue(self.fixes.apply_dbus(types.SimpleNamespace(DbusManager=manager_class)))
        self.assertIs(manager_class.send_request, send_request)


if __name__ == "__main__":
    unittest.main()
