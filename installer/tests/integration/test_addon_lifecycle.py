from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from subprocess import CompletedProcess

from installer.runtime import addons
from installer.runtime.models import AddonSpec, AddonsSpec, SystemWheelSpec

ADDON = addons.OCTOEVERYWHERE
OBICO = addons.OBICO
PATHS = addons.addon_paths(ADDON.id)
UNIT_FILE = f"{addons.UNIT_DIR}/{ADDON.unit}"


class FakeHost:
    """systemctl, venv, and pip as the add-on runtime calls them, against a fake root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.commands: list[list[str]] = []
        self.active = "active"
        self.polls = 0

    def __call__(self, command, **kwargs):
        command = [str(part) for part in command]
        self.commands.append(command)
        if command[:2] == ["systemctl", "is-active"]:
            return CompletedProcess(command, 0, stdout=f"{self.active}\n")
        if command[:2] == ["systemctl", "show"]:
            self.polls += 1
            # A crash-looping service is active between restarts; only its restart count shows it.
            restarts = self.polls if self.active == "restarting" else 0
            state = "active" if self.active == "restarting" else self.active
            return CompletedProcess(command, 0, stdout=f"ActiveState={state}\nNRestarts={restarts}\n")
        if command[1:4] == ["-m", "venv", "--without-pip"]:
            (Path(command[4]) / "bin").mkdir(parents=True)
            (Path(command[4]) / "bin/python3").write_text("", encoding="utf-8")
        elif len(command) > 2 and command[2] == "install":
            site = Path(command[0]).parents[1] / "lib/python3.9/site-packages"
            site.mkdir(parents=True, exist_ok=True)
            for wheel in command[3:]:
                if wheel.endswith(".whl"):
                    (site / Path(wheel).name).write_text("", encoding="utf-8")
        return CompletedProcess(command, 0, stdout="")

    def builds(self) -> int:
        return sum(1 for command in self.commands if command[1:3] == ["-m", "venv"])


def _bundle(installer_root: Path, version: str, ids: tuple[str, ...] = (ADDON.id,)) -> AddonsSpec:
    def artifact(relative: str, data: bytes) -> SystemWheelSpec:
        path = installer_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return SystemWheelSpec(url=f"https://example.invalid/{relative}", archive=relative, sha256=hashlib.sha256(data).hexdigest())

    def addon(addon_id: str) -> AddonSpec:
        source = io.BytesIO()
        with tarfile.open(fileobj=source, mode="w:gz") as tar:
            data = f"VERSION = {version!r}\n".encode("utf-8")
            for name in ("app/__main__.py", "lib/plugin.so.0.0"):
                info = tarfile.TarInfo(f"{addon_id}-{version}/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            # Release archives name shared libraries through links to the file beside them.
            link = tarfile.TarInfo(f"{addon_id}-{version}/lib/plugin.so")
            link.type, link.linkname = tarfile.SYMTYPE, "plugin.so.0.0"
            tar.addfile(link)
        return AddonSpec(
            id=addon_id,
            version=version,
            source=artifact(f"addons/{addon_id}/{addon_id}-{version}.tar.gz", source.getvalue()),
            wheels=(artifact(f"addons/{addon_id}/wheels/example-{version}-py3-none-any.whl", version.encode("utf-8")),),
            sdists=(artifact(f"addons/{addon_id}/sdists/pure-{version}.tar.gz", version.encode("utf-8")),) if addon_id == OBICO.id else (),
        )

    return AddonsSpec(pip_wheel=artifact("addons/pip-25.3-py3-none-any.whl", b"pip"), catalog=tuple(addon(addon_id) for addon_id in ids))


class AddonLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "root"
        self.root.mkdir()
        self.installer_root = Path(tmp.name) / "bundle/installer"
        self.host = FakeHost(self.root)

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")

    def install(self, spec: AddonsSpec, addon_id: str = ADDON.id) -> str:
        return addons.install_addon(
            addon_id, spec=spec, installer_root=self.installer_root, root=self.root, sudo_password=None, run=self.host, sleep=lambda _: None
        )

    def test_install_update_and_remove_touch_only_the_addon(self) -> None:
        spec = _bundle(self.installer_root, "1.0")
        self.assertEqual(addons.list_addons(spec, root=self.root, run=self.host)[0]["state"], addons.NOT_INSTALLED)

        self.install(spec)
        for directory in ADDON.data_dirs:
            self.assertTrue(self.path(f"{PATHS.data}/{directory}").is_dir())
        unit = self.path(UNIT_FILE).read_text(encoding="utf-8")
        self.assertTrue(unit.startswith(ADDON.unit_marker))
        self.assertIn('"DisableMoonrakerConfigFileWrites":true', __import__("base64").b64decode(unit.split('"')[-2]).decode())
        self.assertEqual(self.path(f"{PATHS.app}/app/__main__.py").read_text(encoding="utf-8"), "VERSION = '1.0'\n")
        self.assertEqual(self.path(f"{PATHS.app}/lib/plugin.so").read_text(encoding="utf-8"), "VERSION = '1.0'\n")
        self.assertIn(["systemctl", "enable", ADDON.unit], self.host.commands)
        listed = addons.list_addons(spec, root=self.root, run=self.host)[0]
        self.assertEqual((listed["state"], listed["installed_version"], listed["service"]["active"]), (addons.INSTALLED, "1.0", "active"))
        # The plugin's own settings stay; the package's status API and frontend settings are enforced.
        config = self.path(f"{PATHS.data}/config/octoeverywhere.conf")
        config.write_text("[general]\nlocal_web_api_port = 9\nlog_level = DEBUG\n", encoding="utf-8")

        # The same build doesn't rebuild.
        self.install(spec)
        self.assertEqual(self.host.builds(), 1)

        newer = _bundle(self.installer_root, "2.0")
        self.assertEqual(addons.list_addons(newer, root=self.root, run=self.host)[0]["state"], addons.UPDATE_AVAILABLE)
        self.assertEqual(addons.update_installed_addons(spec=newer, installer_root=self.installer_root, root=self.root, sudo_password=None, run=self.host, sleep=lambda _: None), [])
        self.assertEqual(self.host.builds(), 2)
        self.assertEqual(addons.read_marker(ADDON.id, root=self.root)["version"], "2.0")
        text = config.read_text(encoding="utf-8")
        self.assertIn("log_level = DEBUG", text)
        self.assertIn(f"local_web_api_port = {addons.OCTOEVERYWHERE_STATUS_PORT}", text)
        self.assertFalse(self.path(PATHS.code + addons.PREVIOUS_SUFFIX).exists())

        self.assertTrue(addons.remove_addon(ADDON.id, root=self.root, sudo_password=None, run=self.host))
        self.assertIn(["systemctl", "disable", "--now", ADDON.unit], self.host.commands)
        self.assertFalse(self.path(UNIT_FILE).exists())
        self.assertFalse(self.path(addons.ROOT).exists())
        self.assertFalse(addons.remove_addon(ADDON.id, root=self.root, sudo_password=None, run=self.host))

    def test_an_update_that_does_not_stay_running_puts_the_previous_build_back(self) -> None:
        self.install(_bundle(self.installer_root, "1.0"))
        for failure in ("failed", "restarting"):
            with self.subTest(failure=failure):
                self.host.active = failure
                with self.assertRaises(addons.AddonError):
                    self.install(_bundle(self.installer_root, "2.0"))
        self.assertEqual(addons.read_marker(ADDON.id, root=self.root)["version"], "1.0")
        self.assertEqual(self.path(f"{PATHS.app}/app/__main__.py").read_text(encoding="utf-8"), "VERSION = '1.0'\n")
        self.assertTrue(self.path(UNIT_FILE).exists())

        # A first install that never stays running leaves nothing behind but its data.
        addons.remove_addon(ADDON.id, root=self.root, sudo_password=None, run=self.host)
        with self.assertRaises(addons.AddonError):
            self.install(_bundle(self.installer_root, "1.0"))
        self.assertIsNone(addons.read_marker(ADDON.id, root=self.root))
        self.assertFalse(self.path(UNIT_FILE).exists())

    def test_installs_made_outside_valve_oil_are_left_alone(self) -> None:
        spec = _bundle(self.installer_root, "1.0")
        for foreign in (UNIT_FILE, "/home/qidi/octoeverywhere/install.sh"):
            with self.subTest(foreign=foreign):
                path = self.path(foreign)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[Unit]\n", encoding="utf-8")
                self.assertEqual(addons.list_addons(spec, root=self.root, run=self.host)[0]["state"], addons.FOREIGN)
                with self.assertRaises(addons.AddonError):
                    self.install(spec)
                with self.assertRaises(addons.AddonError):
                    addons.remove_addon(ADDON.id, root=self.root, sudo_password=None, run=self.host)
                self.assertEqual(path.read_text(encoding="utf-8"), "[Unit]\n")
                path.unlink()
        self.assertEqual(self.host.builds(), 0)

    def test_obico_keeps_its_server_and_relinks_without_touching_printer_config(self) -> None:
        spec = _bundle(self.installer_root, "1.0", ids=(OBICO.id,))
        paths = addons.addon_paths(OBICO.id)
        self.install(spec, OBICO.id)
        # The launcher that keeps the plugin out of printer.cfg runs in place of the plugin's own entry point.
        unit = self.path(f"{addons.UNIT_DIR}/{OBICO.unit}").read_text(encoding="utf-8")
        self.assertIn(f"-m tltg_obico -c {paths.data}/{addons.OBICO_CONFIG}", unit)
        self.assertEqual(self.path(f"{paths.app}/tltg_obico.py").read_bytes(), (addons.PAYLOAD_DIR / "tltg_obico.py").read_bytes())
        self.assertTrue(any("--no-build-isolation" in command and command[-1].endswith("pure-1.0.tar.gz") for command in self.host.commands))
        self.assertFalse(self.path("/home/qidi/printer_data/config").exists())

        config = self.path(f"{paths.data}/{addons.OBICO_CONFIG}")
        text = config.read_text(encoding="utf-8")
        self.assertIn(f"url = {addons.OBICO_CLOUD}", text)
        self.assertIn("dest_port = 80", text)
        # A self-hosted server and its link survive updates; the package's Moonraker and log settings are enforced.
        config.write_text("[server]\nurl = http://192.168.1.10:3334\nauth_token = secret\n[logging]\npath = /tmp/x.log\n", encoding="utf-8")
        self.install(_bundle(self.installer_root, "2.0", ids=(OBICO.id,)), OBICO.id)
        text = config.read_text(encoding="utf-8")
        self.assertIn("url = http://192.168.1.10:3334", text)
        self.assertIn("auth_token = secret", text)
        self.assertIn(f"path = {addons.OBICO_LOG}", text)

        def relink(server=None) -> None:
            addons.relink_addon(OBICO.id, server=server, root=self.root, sudo_password=None, run=self.host, sleep=lambda _: None)

        for bad in ("ftp://server", "http://server\nauth_token = stolen", "http://server/?next=x"):
            with self.subTest(server=bad):
                commands = len(self.host.commands)
                with self.assertRaises(addons.AddonError):
                    relink(bad)
                self.assertEqual(len(self.host.commands), commands)
        self.assertIn("auth_token = secret", config.read_text(encoding="utf-8"))
        relink("https://obico.example.com/")
        text = config.read_text(encoding="utf-8")
        self.assertIn("url = https://obico.example.com\n", text)
        self.assertNotIn("auth_token", text)
        self.assertIn(["systemctl", "restart", OBICO.unit], self.host.commands)
        with self.assertRaises(addons.AddonError):
            addons.relink_addon(ADDON.id, root=self.root, sudo_password=None, run=self.host)

    def test_remote_access_addons_are_installed_one_at_a_time(self) -> None:
        spec = _bundle(self.installer_root, "1.0", ids=(ADDON.id, OBICO.id))
        self.install(spec)
        listed = {entry["id"]: entry for entry in addons.list_addons(spec, root=self.root, run=self.host)}
        self.assertEqual((listed[OBICO.id]["state"], listed[OBICO.id]["blocked_by"]), (addons.NOT_INSTALLED, [ADDON.name]))
        with self.assertRaises(addons.AddonError):
            self.install(spec, OBICO.id)
        self.assertIsNone(addons.read_marker(OBICO.id, root=self.root))
        # An installed add-on still updates, and an Obico installed with its own installer blocks OctoEverywhere too.
        self.install(_bundle(self.installer_root, "2.0", ids=(ADDON.id, OBICO.id)))
        addons.remove_addon(ADDON.id, root=self.root, sudo_password=None, run=self.host)
        foreign = self.path("/home/qidi/moonraker-obico/install.sh")
        foreign.parent.mkdir(parents=True)
        foreign.write_text("", encoding="utf-8")
        with self.assertRaises(addons.AddonError):
            self.install(spec)
        foreign.unlink()
        foreign.parent.rmdir()
        self.install(spec, OBICO.id)
        self.assertEqual(addons.read_marker(OBICO.id, root=self.root)["version"], "1.0")

    def test_files_that_do_not_match_the_manifest_are_refused(self) -> None:
        spec = _bundle(self.installer_root, "1.0")
        (self.installer_root / spec.catalog[0].wheels[0].archive).write_bytes(b"tampered")
        with self.assertRaises(addons.AddonError):
            self.install(spec)
        self.assertFalse(self.path(addons.ROOT).exists())


def _load_server():
    path = Path(__file__).resolve().parents[2] / "system/tltg_addons.py"
    module_spec = importlib.util.spec_from_file_location("tltg_addons_under_test", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


class AddonServiceRequestTests(unittest.TestCase):
    """The add-ons service starts root-level changes, so only same-origin Fluidd requests may start them."""

    @classmethod
    def setUpClass(cls) -> None:
        server = _load_server()
        started: list[tuple[str, str]] = []

        class Catalog:
            error = None

            def entries(self):
                return [{"id": "octoeverywhere", "state": "not_installed"}, {"id": "obico", "state": "installed"}]

            def reload(self, *, force=False):
                pass

        class Jobs:
            def busy(self):
                return False

            def current(self):
                return None

            def start(self, addon, action, options=()):
                started.append((addon, action, *options))
                return {"addon": addon, "action": action, "state": "running"}

        server.printer_busy = lambda: False
        server.Handler.catalog = Catalog()
        server.Handler.jobs = Jobs()
        cls.started = started
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, path: str, *, method: str = "POST", headers: dict[str, str] | None = None, body: bytes = b"{}") -> tuple[int, dict]:
        request = urllib.request.Request(self.base + path, data=body if method == "POST" else None, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_only_marked_same_origin_json_posts_start_jobs(self) -> None:
        good = {"X-Valve-Oil": "addons", "Content-Type": "application/json", "Host": "printer.local", "Origin": "http://printer.local"}
        rejected = (
            {key: value for key, value in good.items() if key != "X-Valve-Oil"},
            {**good, "Content-Type": "application/x-www-form-urlencoded"},
            {**good, "Origin": "http://evil.example"},
        )
        for headers in rejected:
            with self.subTest(headers=headers):
                status, _ = self.request("/api/addons/octoeverywhere/install", headers=headers)
                self.assertIn(status, {400, 403})
        self.assertEqual(self.started, [])
        self.assertEqual(self.request("/api/addons/unknown/install", headers=good)[0], 404)
        self.assertEqual(self.request("/api/addons/octoeverywhere/reboot", headers=good)[0], 404)
        status, body = self.request("/api/addons/octoeverywhere/install", headers=good)
        self.assertEqual((status, body["job"]["action"]), (202, "install"))
        self.assertEqual(self.started, [("octoeverywhere", "install")])
        # A relink may name a server, passed to addons.sh as a single option; nothing else takes a body.
        for body in (b'{"server": ["x"]}', b'{"server": "' + b"a" * 300 + b'"}', b"[]", b"not json"):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.request("/api/addons/obico/relink", headers=good, body=body)[0], 400)
        self.assertEqual(self.request("/api/addons/obico/install", headers=good, body=b'{"server": "http://x"}')[0], 400)
        status, _ = self.request("/api/addons/obico/relink", headers=good, body=b'{"server": "--help http://x"}')
        self.assertEqual(status, 202)
        self.assertEqual(self.started[-1], ("obico", "relink", "--server=--help http://x"))
        status, body = self.request("/api/addons", method="GET")
        self.assertEqual((status, body["addons"][0]["id"], body["busy"]), (200, "octoeverywhere", False))


if __name__ == "__main__":
    unittest.main()
