from __future__ import annotations

import asyncio
import importlib.util
import socket
import ssl
import sys
import threading
import time
import types
import unittest

from installer.runtime import moonraker_fixes


def load_payload():
    spec = importlib.util.spec_from_file_location("vo_moonraker_fixes_under_test", moonraker_fixes.MODULE_PAYLOAD)
    module = importlib.util.module_from_spec(spec)
    before = list(sys.meta_path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.meta_path[:] = before  # the payload registers its import hook when imported
    return module


class Client:
    """The parts of QIDI's ExtPahoClient its reconnect uses."""

    def __init__(self, host, tls):
        self._ssl, self._ssl_context, self._host, self._keepalive = tls, ssl.create_default_context() if tls else None, host, 60
        self.sock = None

    def reconnect(self, sock):
        if self.sock is not None:
            self.sock.close()
        if self._ssl:
            sock = self._ssl_context.wrap_socket(sock, server_hostname=self._host, do_handshake_on_connect=False)
            sock.settimeout(self._keepalive)
            sock.do_handshake()
        self.sock = sock


class EventLoop:
    """The parts of Moonraker's EventLoop QIDI's MQTT component uses to connect."""

    async def create_socket_connection(self, address, timeout=None):
        sock = socket.socket()
        sock.setblocking(False)
        await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, address), timeout)
        return sock

    def run_in_thread(self, callback, *args):
        return asyncio.get_running_loop().run_in_executor(None, callback, *args)

    def create_task(self, coro):
        return asyncio.get_running_loop().create_task(coro)


class Agent:
    def __init__(self, name, url):
        self.client_data = {"name": name, "version": "1", "type": "agent", "url": url}


class Server:
    """Moonraker's server with QIDI's registry of extension agents."""

    def __init__(self):
        self.extensions = types.SimpleNamespace(agents={})

    def lookup_component(self, name, default=None):
        return self.extensions if name == "extensions" else default


def component_class():
    class MQTTClient:
        def __init__(self, address, tls):
            self.server = Server()
            self.eventloop = EventLoop()
            self.client = Client(address[0], tls)
            self.address = address
            self.decrypted_by = []

        def component_init(self):
            # As QIDI's does on a printer linked to an account.
            self.connect_task = self.eventloop.create_task(self._do_reconnect(first=True))

        async def _qidiclient_decrypt_hex(self, hex_str):
            # QIDI's asks the first registered agent.
            self.decrypted_by.append(next(iter(self.server.lookup_component("extensions").agents), None))

        async def _do_reconnect(self, first=False):
            await self._qidiclient_decrypt_hex("00")
            sock = await self.eventloop.create_socket_connection(self.address, timeout=10)
            self.client.reconnect(sock)
    return MQTTClient


class MoonrakerMqttHandshakeTests(unittest.TestCase):
    def setUp(self):
        # A broker that accepts connections and never answers.
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.address = self.server.getsockname()
        self.accepted = []
        threading.Thread(target=self._accept, daemon=True).start()
        self.addCleanup(self._close)
        self.fixes = load_payload()
        self.fixes.HANDSHAKE_TIMEOUT = 0.25
        self.component_class = component_class()
        self.assertTrue(self.fixes.apply(types.SimpleNamespace(MQTTClient=self.component_class)))

    def _accept(self):
        try:
            while True:
                self.accepted.append(self.server.accept()[0])
        except OSError:
            pass

    def _close(self):
        self.server.close()
        for conn in self.accepted:
            conn.close()

    def reconnect(self, tls):
        component = self.component_class(self.address, tls)

        async def run():
            stall, last, done = 0., time.monotonic(), False

            async def watch():
                nonlocal stall, last
                while not done:
                    await asyncio.sleep(0.02)
                    now = time.monotonic()
                    stall, last = max(stall, now - last - 0.02), now
            watcher = asyncio.ensure_future(watch())
            error = None
            try:
                await component._do_reconnect()
            except OSError as exc:
                error = exc
            done = True
            await watcher
            return error, stall
        error, stall = asyncio.run(run())
        return component, error, stall

    def test_silent_broker_does_not_stop_the_event_loop(self):
        component, error, stall = self.reconnect(tls=True)
        # The component's own retry loop gets the handshake timeout, as it did when the handshake ran on the loop.
        self.assertIsInstance(error, OSError)
        self.assertIn("timed out", str(error))
        self.assertLess(stall, 0.15)
        self.assertIsNone(component.client.sock)

    def test_plain_connections_and_unknown_components_are_left_alone(self):
        component, error, _stall = self.reconnect(tls=False)
        self.assertIsNone(error)
        self.assertIs(type(component.client.sock), socket.socket)
        component.client.sock.close()
        self.assertFalse(self.fixes.apply(types.SimpleNamespace()))

    def test_first_connect_waits_for_the_touchscreen_app_and_decrypts_through_it(self):
        self.fixes.APP_POLL_INTERVAL = 0.01
        # The Max 4 and Plus 5 apps register as QD_<model>; the Smart 5's as S5.
        for app in ("QD_PLUS5", "S5"):
            with self.subTest(app=app):
                component = self.component_class(self.address, tls=False)
                agents = component.server.extensions.agents
                agents["OrcaSlicer"] = Agent("OrcaSlicer", "https://github.com/SoftFever/OrcaSlicer")

                async def run():
                    component.component_init()
                    await asyncio.sleep(0.05)
                    self.assertEqual(component.decrypted_by, [])
                    agents[app] = Agent(app, "local")
                    await asyncio.wait_for(component.connect_task, 1)
                    # Only the first connect after Moonraker starts waits.
                    del agents[app]
                    component.component_init()
                    await asyncio.wait_for(component.connect_task, 1)
                    agents[app] = Agent(app, "local")
                    await component._do_reconnect()
                asyncio.run(run())
                self.assertEqual(component.decrypted_by, [app, "OrcaSlicer", app])
                component.client.sock.close()

    def test_first_connect_goes_ahead_without_the_touchscreen_app_and_can_be_cancelled(self):
        self.fixes.APP_POLL_INTERVAL = 0.01
        self.fixes.APP_WAIT_TIMEOUT = 0.05
        waited = self.component_class(self.address, tls=False)
        cancelled = self.component_class(self.address, tls=False)

        async def run():
            waited.component_init()
            cancelled.component_init()
            await asyncio.sleep(0.02)
            cancelled.connect_task.cancel()
            await asyncio.wait_for(waited.connect_task, 1)
            with self.assertRaises(asyncio.CancelledError):
                await cancelled.connect_task
        asyncio.run(run())
        self.assertEqual(waited.decrypted_by, [None])
        self.assertEqual(cancelled.decrypted_by, [])
        self.assertIsNone(cancelled.client.sock)
        waited.client.sock.close()


class WebRequest:
    """moonraker.common.WebRequest, as QIDI's handlers read it."""

    def __init__(self, endpoint, args):
        self.endpoint, self.args = endpoint, args

    def get(self, key, default=None):
        return self.args.get(key, default)


class CloudServer:
    """Moonraker's server with QIDI's MQTT component, its warning, and its failed-component list."""

    def __init__(self, connected):
        self.mqtt = types.SimpleNamespace(
            connected=connected, retrying=not connected, disconnect_requests=0, closes=0)
        self.mqtt.is_connected = lambda: self.mqtt.connected

        async def close():
            self.mqtt.closes += 1
            self.mqtt.connected = False

        async def handle_disconnect_request(web_request):
            # QIDI's /server/mqtt/disconnect: closes the client and ends its reconnect loop. An attempt already
            # sent still gets its refusal, which sets the warning and failed component again.
            self.mqtt.disconnect_requests += 1
            self.mqtt.connected = self.mqtt.retrying = False
            asyncio.get_running_loop().call_later(0.02, self.refuse)
            return {"mqtt_status": "offline"}
        self.mqtt.close, self.mqtt._handle_disconnect_request = close, handle_disconnect_request
        self.warnings = {} if connected else {"mqtt_disconnect_reson": "MQTT Connection Failed: Bad user name or password"}
        self.failed_components = [] if connected else ["mqtt"]

    def refuse(self):
        self.warnings["mqtt_disconnect_reson"] = "MQTT Connection Failed: Bad user name or password"
        if "mqtt" not in self.failed_components:
            self.failed_components.append("mqtt")

    def lookup_component(self, name, default=None):
        return self.mqtt if name == "mqtt" else default

    def remove_warning(self, warn_id):
        self.warnings.pop(warn_id, None)

    def remove_failed_component(self, name):
        if name in self.failed_components:
            self.failed_components.remove(name)


def database_class():
    class MoonrakerDatabase:
        def __init__(self, server):
            self.server = server
            self.config_items = {}

        async def _handle_config_update_request(self, web_request):
            # QIDI's: stores the value, and for general.lan_mode "1" closes MQTT only when it is connected.
            section, key, value = (web_request.get(name) for name in ("section", "key", "value"))
            self.config_items[f"{section}.{key}"] = value
            mqtt = self.server.lookup_component("mqtt")
            if f"{section}.{key}" == "general.lan_mode" and value == "1" and mqtt.is_connected():
                await mqtt.close()
            return {"status": "success", "result": {f"{section}.{key}": value}}
    return MoonrakerDatabase


class MoonrakerLanModeTests(unittest.TestCase):
    def setUp(self):
        self.fixes = load_payload()
        self.fixes.LAN_MODE_SETTLE = 0.05
        self.database_class = database_class()
        self.assertTrue(self.fixes.apply_database(types.SimpleNamespace(MoonrakerDatabase=self.database_class)))
        common = types.ModuleType("moonraker.common")
        common.WebRequest = WebRequest
        modules = {"moonraker": types.ModuleType("moonraker"), "moonraker.common": common}
        saved = {name: sys.modules.get(name) for name in modules}
        sys.modules.update(modules)
        self.addCleanup(lambda: [sys.modules.pop(name) if module is None else sys.modules.__setitem__(name, module)
                                 for name, module in saved.items()])

    def update(self, server, section, key, value):
        database = self.database_class(server)

        async def run():
            result = await database._handle_config_update_request(
                WebRequest("server.database.config.update", {"section": section, "key": key, "value": value}))
            await asyncio.sleep(0.15)
            return result
        result = asyncio.run(run())
        self.assertEqual(result["status"], "success")
        self.assertEqual(database.config_items, {f"{section}.{key}": value})

    def test_lan_only_on_stops_retries_that_never_connected_and_clears_their_warning(self):
        server = CloudServer(connected=False)
        self.update(server, "general", "lan_mode", "1")
        self.assertFalse(server.mqtt.retrying)
        self.assertEqual(server.mqtt.disconnect_requests, 1)
        self.assertEqual(server.warnings, {})
        self.assertEqual(server.failed_components, [])

    def test_lan_only_on_while_connected_is_left_to_qidi(self):
        server = CloudServer(connected=True)
        self.update(server, "general", "lan_mode", "1")
        self.assertEqual((server.mqtt.closes, server.mqtt.disconnect_requests), (1, 0))

    def test_other_writes_leave_the_retries_alone(self):
        for section, key, value in (("general", "lan_mode", "0"), ("general", "sound", "1"), ("user", "lan_mode", "1")):
            with self.subTest(key=f"{section}.{key}", value=value):
                server = CloudServer(connected=False)
                self.update(server, section, key, value)
                self.assertTrue(server.mqtt.retrying)
                self.assertEqual(server.failed_components, ["mqtt"])

    def test_unrecognized_database_code_is_left_alone(self):
        self.assertFalse(self.fixes.apply_database(types.SimpleNamespace()))
        # Patching twice wraps QIDI's handler once.
        handler = self.database_class._handle_config_update_request
        self.assertTrue(self.fixes.apply_database(types.SimpleNamespace(MoonrakerDatabase=self.database_class)))
        self.assertIs(self.database_class._handle_config_update_request, handler)


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
