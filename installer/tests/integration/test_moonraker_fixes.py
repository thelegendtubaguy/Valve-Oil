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
    spec = importlib.util.spec_from_file_location("tltg_moonraker_fixes_under_test", moonraker_fixes.MODULE_PAYLOAD)
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
        component = self.component_class(self.address, tls=False)
        agents = component.server.extensions.agents
        agents["OrcaSlicer"] = Agent("OrcaSlicer", "https://github.com/SoftFever/OrcaSlicer")

        async def run():
            component.component_init()
            await asyncio.sleep(0.05)
            self.assertEqual(component.decrypted_by, [])
            agents["QD_PLUS5"] = Agent("QD_PLUS5", "local")
            await asyncio.wait_for(component.connect_task, 1)
            # Only the first connect after Moonraker starts waits.
            del agents["QD_PLUS5"]
            component.component_init()
            await asyncio.wait_for(component.connect_task, 1)
            agents["QD_PLUS5"] = Agent("QD_PLUS5", "local")
            await component._do_reconnect()
        asyncio.run(run())
        self.assertEqual(component.decrypted_by, ["QD_PLUS5", "OrcaSlicer", "QD_PLUS5"])
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
