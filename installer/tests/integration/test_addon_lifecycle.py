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


def _bundle(installer_root: Path, version: str) -> AddonsSpec:
    def artifact(relative: str, data: bytes) -> SystemWheelSpec:
        path = installer_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return SystemWheelSpec(url=f"https://example.invalid/{relative}", archive=relative, sha256=hashlib.sha256(data).hexdigest())

    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode="w:gz") as tar:
        data = f"VERSION = {version!r}\n".encode("utf-8")
        info = tarfile.TarInfo(f"OE-{version}/moonraker_octoeverywhere/__main__.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return AddonsSpec(
        pip_wheel=artifact("addons/pip-25.3-py3-none-any.whl", b"pip"),
        catalog=(
            AddonSpec(
                id=ADDON.id,
                version=version,
                source=artifact(f"addons/{ADDON.id}/{ADDON.id}-{version}.tar.gz", source.getvalue()),
                wheels=(artifact(f"addons/{ADDON.id}/wheels/example-{version}-py3-none-any.whl", version.encode("utf-8")),),
            ),
        ),
    )


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

    def install(self, spec: AddonsSpec) -> str:
        return addons.install_addon(
            ADDON.id, spec=spec, installer_root=self.installer_root, root=self.root, sudo_password=None, run=self.host, sleep=lambda _: None
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
        self.assertEqual(self.path(f"{PATHS.app}/moonraker_octoeverywhere/__main__.py").read_text(encoding="utf-8"), "VERSION = '1.0'\n")
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
        self.assertEqual(self.path(f"{PATHS.app}/moonraker_octoeverywhere/__main__.py").read_text(encoding="utf-8"), "VERSION = '1.0'\n")
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
                return [{"id": "octoeverywhere", "state": "not_installed"}]

            def reload(self, *, force=False):
                pass

        class Jobs:
            def busy(self):
                return False

            def current(self):
                return None

            def start(self, addon, action):
                started.append((addon, action))
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

    def request(self, path: str, *, method: str = "POST", headers: dict[str, str] | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(self.base + path, data=b"{}" if method == "POST" else None, method=method, headers=headers or {})
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
        status, body = self.request("/api/addons", method="GET")
        self.assertEqual((status, body["addons"][0]["id"], body["busy"]), (200, "octoeverywhere", False))


if __name__ == "__main__":
    unittest.main()
