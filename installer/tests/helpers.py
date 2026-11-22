from __future__ import annotations

import atexit
import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
from subprocess import CompletedProcess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
BASE_RUNTIME_FIXTURE = FIXTURES_ROOT / "runtime" / "base"
from installer.runtime import klipper_chelper, qidiclient_firmware_popup
from installer.runtime.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOONRAKER_QUERY_URL = "http://moonraker.invalid/printer/objects/query?print_stats"
_TEMP_ROOTS: list[Path] = []
def homing_fixture_bytes(firmware: str) -> bytes:
    if firmware not in {"01.01.06.03", "01.01.06.04", "01.01.06.05", "01.01.07.01"}:
        raise ValueError(f"Unsupported homing fixture firmware: {firmware}")
    if firmware in {"01.01.06.05", "01.01.07.01"}:
        return homing_sync_reset_fixture_bytes()
    value = (FIXTURES_ROOT / "source-patches/01.01.06.04/homing.py").read_bytes()
    if firmware == "01.01.06.03":
        value = value.replace(
            b"G4 P200\\nSET_HOMING_MODE STEPPER=y VALUE=2",
            b"G4 P200SET_HOMING_MODE STEPPER=y VALUE=2",
        )
    return value


def homing_smart5_fixture_bytes() -> bytes:
    """QIDI's Smart 5 homing.py, rebuilt from the patched copy."""
    return (REPO_ROOT / "installer/klipper/qidi/homing-smart5.py").read_bytes().replace(
        b'self.toolhead.dwell(\r\n            .25 if rails[0].get_name() in ("stepper_x", "stepper_y")\r\n            else 1)',
        b"self.toolhead.dwell(1)",
    )


def homing_sync_reset_fixture_bytes() -> bytes:
    return (
        FIXTURES_ROOT
        / "source-patches/01.01.06.04/homing-sync-reset.py"
    ).read_bytes()


def homing_sync_reset_26_11_18_1_bytes() -> bytes:
    """homing.py as Valve Oil 26.11.18.1 patched QIDI's sync-reset file, rebuilt from QIDI's copy."""
    value = homing_sync_reset_fixture_bytes()
    for script, dwell in ((b'x_script = "', b"P100"), (b'y_script = "', b"P100"),
                          (b'x_recover_script = "', b"P50"), (b'y_recover_script = "', b"P50")):
        start = value.index(script)
        end = value.index(b"\n", start)
        value = value[:start] + value[start:end].replace(b"P200", dwell) + value[end:]
    first_home = b"rail.get_endstops()]\r\n        self.toolhead.dwell(1)"
    assert value.count(first_home) == 1
    return value.replace(first_home, (
        b'rail.get_endstops()]\r\n        self.toolhead.dwell(\r\n            .25 if rails[0].get_name() in '
        b'("stepper_x", "stepper_y")\r\n            else 1)'))


def load_source(module_name: str, path: Path, *, stand_ins: dict | None = None):
    """Import the file at `path` as `module_name`. `stand_ins` are sys.modules entries, such as the Klipper modules
    the file imports, that exist only while it is imported."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    stand_ins = stand_ins or {}
    saved = {name: sys.modules.get(name) for name in stand_ins}
    sys.modules.update(stand_ins)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


def temp_path(prefix: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_ROOTS.append(root)
    return root



def _cleanup_temp_roots() -> None:
    for root in reversed(_TEMP_ROOTS):
        shutil.rmtree(root, ignore_errors=True)



atexit.register(_cleanup_temp_roots)



def copy_base_runtime(*, homing_patched: bool = False) -> Path:
    """A Max 4 01.01.06.03 printer_data, in a home of its own where the installer keeps its caches beside it.

    homing_patched: Klipper's homing.py already carries Valve Oil's patch, so an install has no source patch to apply
    or restore. For tests about other parts of an install: with QIDI's homing.py every state write carries its
    40 KB original, which makes up most of an install's time here. test_source_patch_lifecycle_matrix covers the patch.
    """
    temp_root = temp_path("installer-runtime-") / "printer_data"
    shutil.copytree(BASE_RUNTIME_FIXTURE / "config", temp_root / "config")
    shutil.copy2(BASE_RUNTIME_FIXTURE / "firmware_manifest.json", temp_root / "firmware_manifest.json")
    target = temp_root / "klipper/klippy/extras"
    target.mkdir(parents=True, exist_ok=True)
    homing = (REPO_ROOT / "installer/klipper/qidi/homing.py").read_bytes() if homing_patched else (
        homing_fixture_bytes("01.01.06.03"))
    (target / "homing.py").write_bytes(homing)
    return temp_root



def stock_printer_data(manifest, firmware: str, *, homing: bytes | None = None) -> Path:
    """printer_data of a printer on QIDI's `firmware` as shipped: that firmware's stock config, its firmware manifest,
    and QIDI's homing.py (or `homing`)."""
    printer_root = copy_base_runtime()
    printer = manifest.printer_for_firmware(firmware)
    if firmware != "01.01.06.03":
        shutil.copytree(REPO_ROOT / "installer" / printer.stock_snapshot(firmware) / "config", printer_root / "config",
                        dirs_exist_ok=True)
    (printer_root / "firmware_manifest.json").write_text(
        json.dumps({"SOC": {"file": f"{printer.soc_file_prefix}{printer.soc_version(firmware)}_Release_NA",
                            "version": printer.soc_version(firmware)}}),
        encoding="utf-8",
    )
    if homing is not None:
        (printer_root / "klipper/klippy/extras/homing.py").write_bytes(homing)
    elif firmware != "01.01.06.03":
        (printer_root / "klipper/klippy/extras/homing.py").write_bytes(homing_fixture_bytes(firmware))
    return printer_root



def build_env(printer_data_root: Path, *, moonraker_url: str) -> dict[str, str]:
    return {
        "TLTG_OPTIMIZED_PRINTER_DATA_ROOT": str(printer_data_root),
        "TLTG_OPTIMIZED_FIRMWARE_MANIFEST": str(printer_data_root / "firmware_manifest.json"),
        "TLTG_OPTIMIZED_MOONRAKER_URL": moonraker_url,
        "TLTG_OPTIMIZED_KLIPPER_ROOT": str(printer_data_root / "klipper"),
    }



def fake_qidiclient(variant: int = 0) -> tuple[bytes, bytes, bytes]:
    # A small stand-in for QIDI's touchscreen executable, admitted as a reviewed build, with its patched forms with and
    # without QIDI's package updates blocked. Each variant is a different build of the same size.
    popup = qidiclient_firmware_popup
    stock = (
        b"\x7fELF\xff\xfe" + bytes([variant]) + bytes(57) + popup.BRANCH_IF_EQUAL + bytes(64) + popup.BRANCH_IF_EQUAL + bytes(60)
        + popup.STORE_NEED_UPDATE + bytes(28) + popup.EXTRACT_PROLOGUE + bytes(24) + popup.DETECTOR_CHANGED + bytes(12)
    )
    sites = (
        popup.Site(64, popup.BRANCH_IF_EQUAL, popup.BRANCH_IF_NOT_LONGER),
        popup.Site(132, popup.BRANCH_IF_EQUAL, popup.BRANCH_IF_NOT_LONGER),
        popup.Site(196, popup.STORE_NEED_UPDATE, popup.STORE_ZERO, blocking=True),
        popup.Site(228, popup.EXTRACT_PROLOGUE, popup.RETURN_FALSE, retired=True),
        popup.Site(260, popup.DETECTOR_CHANGED, popup.DETECTOR_UNCHANGED),
    )
    unblocked = stock.replace(popup.BRANCH_IF_EQUAL, popup.BRANCH_IF_NOT_LONGER).replace(
        popup.DETECTOR_CHANGED, popup.DETECTOR_UNCHANGED)
    blocked = unblocked.replace(popup.STORE_NEED_UPDATE, popup.STORE_ZERO)
    popup.BUILDS[hashlib.sha256(stock).hexdigest()] = popup.Build(
        size=len(stock), sites=sites,
        patched_sha256=hashlib.sha256(unblocked).hexdigest(), blocked_sha256=hashlib.sha256(blocked).hexdigest(),
        other_blocked_sha256=(hashlib.sha256(earlier_blocked_qidiclient(blocked)).hexdigest(),),
    )
    return stock, blocked, unblocked


def earlier_blocked_qidiclient(blocked: bytes) -> bytes:
    # The blocked form development builds wrote, which also stopped QIDI's USB and mandatory-file updates.
    popup = qidiclient_firmware_popup
    return blocked.replace(popup.EXTRACT_PROLOGUE, popup.RETURN_FALSE)


def fake_system_root(
    *,
    include_moonraker_cache: bool = False,
    include_fluidd_bundle: bool = False,
    include_filament_config: bool = False,
    include_klipper_box_config: bool = False,
    include_klipper_print_stats_manager: bool = False,
    include_klipper_chelper: bool = False,
    include_nginx_site: bool = False,
    include_moonraker_asvc: bool = False,
    include_moonraker_metadata: bool = False,
    include_qidiclient: bool = False,
    qidi_kernel: bool = True,
) -> Path:
    root = temp_path("system-optimization-flow-")
    if include_qidiclient:
        qidiclient = root / qidiclient_firmware_popup.FILE.lstrip("/")
        qidiclient.parent.mkdir(parents=True)
        qidiclient.write_bytes(fake_qidiclient()[0])
        qidiclient.chmod(0o755)
    if qidi_kernel:
        zram = load_manifest(REPO_ROOT / "installer/package.yaml").system_optimizations.zram_swap
        (root / "proc/sys/kernel").mkdir(parents=True)
        (root / "proc/sys/kernel/osrelease").write_text(f"{zram.kernel_release}\n", encoding="utf-8")
        (root / "proc/sys/kernel/version").write_text(f"{zram.kernel_versions[-1]}\n", encoding="utf-8")
    (root / "etc/resolvconf/resolv.conf.d").mkdir(parents=True)
    (root / "etc/resolv.conf").write_text("nameserver 114.114.114.114\n", encoding="utf-8")
    (root / "etc/resolvconf/resolv.conf.d/head").write_text("nameserver 8.8.8.8\n", encoding="utf-8")
    (root / "etc/resolvconf/resolv.conf.d/tail").write_text("", encoding="utf-8")
    (root / "etc/apt").mkdir(parents=True)
    (root / "etc/apt/sources.list").write_text("old apt\n", encoding="utf-8")
    unit = root / "lib/systemd/system/rockchip.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/etc/init.d/rockchip.sh\n", encoding="utf-8")
    script = root / "etc/init.d/rockchip.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        "#!/bin/bash -e\n"
        "rk3308\n"
        "CHIPNAME=\"rk3208\"\n"
        "mount -o remount,sync /\n"
        "install_packages\n"
        "touch /usr/local/first_boot_flag\n",
        encoding="utf-8",
    )
    if include_fluidd_bundle:
        fluidd = root / "home/qidi/fluidd/assets/index-DlFvHGAh.js"
        fluidd.parent.mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / "installer/tests/fixtures/fluidd_box_variables.js", fluidd)
        shutil.copyfile(REPO_ROOT / "installer/tests/fixtures/fluidd_sw.js", fluidd.parent.parent / "sw.js")
    if include_klipper_box_config:
        box_config = root / "home/qidi/klipper/klippy/extras/box_config.py"
        box_config.parent.mkdir(parents=True)
        box_config.write_bytes((REPO_ROOT / "installer/tests/fixtures/klipper_box_config.py").read_bytes().replace(b"\n", b"\r\n"))
    if include_klipper_print_stats_manager:
        stats_manager = root / "home/qidi/klipper/klippy/extras/print_stats_manager.py"
        stats_manager.parent.mkdir(parents=True, exist_ok=True)
        stats_manager.write_bytes((REPO_ROOT / "installer/tests/fixtures/klipper_print_stats_manager.py").read_bytes().replace(b"\n", b"\r\n"))
    if include_klipper_chelper:
        # QIDI's library beside a source file from the same firmware, which QIDI ships with the library's time.
        library = root / klipper_chelper.FILE.lstrip("/")
        library.parent.mkdir(parents=True)
        shutil.copyfile(klipper_chelper.PAYLOAD_DIR / "qidi_c_helper.so", library)
        source = library.parent / "kin_extruder.c"
        source.write_text("// QIDI's source\n", encoding="utf-8")
        for path in (library, source):
            os.utime(path, (1_786_959_581, 1_786_959_581))
    if include_nginx_site:
        site = root / "etc/nginx/sites-available/fluidd"
        site.parent.mkdir(parents=True)
        site.write_bytes((REPO_ROOT / "installer/tests/fixtures/nginx_fluidd_site.conf").read_bytes())
    if include_moonraker_asvc:
        asvc = root / "home/qidi/printer_data/moonraker.asvc"
        asvc.parent.mkdir(parents=True)
        # QIDI's list, which has no final newline.
        asvc.write_text(
            "klipper_mcu\nwebcamd\nMoonCord\nKlipperScreen\nmoonraker-telegram-bot\n"
            "moonraker-obico\nsonar\ncrowsnest\noctoeverywhere\nratos-configurator",
            encoding="utf-8",
        )
    if include_filament_config:
        filaments = root / "home/qidi/QIDI_Client/resource/filament_config/filament-0.4.json"
        filaments.parent.mkdir(parents=True)
        filaments.write_text("{}\n", encoding="utf-8")
    gif = root / "home/qidi/QIDI_Client/access/account/process.gif"
    gif.parent.mkdir(parents=True)
    gif.write_bytes(b"old")
    (root / "systemd").mkdir()
    for service in ("xl2tpd", "bluetooth", "algo_app.service"):
        (root / "systemd" / f"{service}.json").write_text(
            json.dumps({"exists": True, "service": service, "enabled": "enabled", "active": "active"}, sort_keys=True),
            encoding="utf-8",
        )
    if include_moonraker_cache:
        moonraker = root / "home/qidi/moonraker/moonraker/components/file_manager/file_manager.py"
        moonraker.parent.mkdir(parents=True)
        moonraker.write_text(
            "from __future__ import annotations\n" +
            (REPO_ROOT / "installer/tests/fixtures/moonraker_metadata_storage.py").read_text(),
            encoding="utf-8",
        )
        if include_moonraker_metadata:
            metadata = moonraker.parent / "metadata.py"
            metadata.write_bytes((REPO_ROOT / "installer/tests/fixtures/moonraker_metadata.py").read_bytes().replace(b"\n", b"\r\n"))
        klippy_apis = moonraker.parent.parent / "klippy_apis.py"
        klippy_apis.write_bytes((REPO_ROOT / "installer/tests/fixtures/moonraker_klippy_apis.py").read_bytes().replace(b"\n", b"\r\n"))
        spoolman = moonraker.parent.parent / "spoolman.py"
        spoolman.write_bytes((REPO_ROOT / "installer/tests/fixtures/moonraker_spoolman.py").read_bytes().replace(b"\n", b"\r\n"))
        (root / "home/qidi/moonraker-env/lib/python3.9/site-packages").mkdir(parents=True)
        (root / "home/qidi/klippy-env/lib/python3.9/site-packages").mkdir(parents=True)
    (root / "mounts").mkdir()
    (root / "mounts/root.options").write_text("rw,relatime,sync\n", encoding="utf-8")
    return root


def add_merged_usr(root: Path, *, format_tools: bool = False, dosfstools_deb: bool = False) -> Path:
    """QIDI's merged /usr under a fake system root: /sbin, /bin, and /lib link into /usr, and /sbin/init leads to
    systemd. format_tools: dosfstools is installed. dosfstools_deb: the touchscreen app carries its package."""
    usr_lib = root / "usr/lib"
    usr_lib.mkdir(parents=True, exist_ok=True)
    if (root / "lib").is_dir() and not (root / "lib").is_symlink():
        shutil.copytree(root / "lib", usr_lib, dirs_exist_ok=True)
        shutil.rmtree(root / "lib")
    for name in ("sbin", "bin"):
        (root / "usr" / name).mkdir(parents=True, exist_ok=True)
    systemd = usr_lib / "systemd/systemd"
    systemd.parent.mkdir(parents=True, exist_ok=True)
    systemd.write_bytes(b"systemd")
    systemd.chmod(0o755)
    (root / "usr/bin/sh").write_bytes(b"sh")
    (root / "usr/bin/sh").chmod(0o755)
    os.symlink("/lib/systemd/systemd", root / "usr/sbin/init")
    for name in ("sbin", "bin", "lib"):
        os.symlink(f"usr/{name}", root / name)
    if format_tools:
        _install_fake_dosfstools(root)
    if dosfstools_deb:
        deb = root / "home/qidi/QIDI_Client/resource/mkfs_tools/dosfstools_4.2-1_arm64.deb"
        deb.parent.mkdir(parents=True, exist_ok=True)
        deb.write_text("Package: dosfstools\nArchitecture: arm64\n", encoding="utf-8")
    return root


def _install_fake_dosfstools(root: Path) -> None:
    """What dpkg -i of QIDI's dosfstools leaves: its tools in /usr/sbin, replacing unowned copies, and a status entry."""
    tool = root / "usr/sbin/mkfs.fat"
    tool.write_bytes(b"mkfs.fat")
    tool.chmod(0o755)
    alias = root / "usr/sbin/mkfs.vfat"
    if alias.is_symlink() or alias.exists():
        alias.unlink()
    os.symlink("mkfs.fat", alias)
    status = root / "var/lib/dpkg/status"
    status.parent.mkdir(parents=True, exist_ok=True)
    with status.open("a", encoding="utf-8") as file:
        file.write("Package: dosfstools\nStatus: install ok installed\nArchitecture: arm64\n\n")


def with_fluidd_build(manifest):
    """Pin the manifest's Fluidd build to a small archive; repository checkouts carry no release build."""
    archive = temp_path("fluidd-build-") / "fluidd.zip"
    with zipfile.ZipFile(archive, "w") as build:
        build.writestr("index.html", "<!doctype html><title>Fluidd</title>\n")
        build.writestr("sw.js", "self.skipWaiting()\n")
        build.writestr("assets/", "")
        build.writestr("assets/index.js", "console.log('fluidd')\n")
    spec = manifest.system_optimizations
    pinned = dataclasses.replace(spec.fluidd_build, archive=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    return dataclasses.replace(manifest, system_optimizations=dataclasses.replace(spec, fluidd_build=pinned))


def with_valve_oil_ui(manifest):
    """Pin the manifest's Valve Oil UI to a small archive, as the bundle builder pins the one it builds."""
    archive = temp_path("valve-oil-ui-") / "valve-oil-ui.zip"
    with zipfile.ZipFile(archive, "w") as build:
        build.writestr("index.html", "<!doctype html><title>Valve Oil</title>\n")
        build.writestr("version.json", '{"version":"test"}\n')
        build.writestr("assets/index.js", "console.log('valve oil')\n")
    spec = manifest.system_optimizations
    pinned = dataclasses.replace(spec.valve_oil_ui, archive=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    return dataclasses.replace(manifest, system_optimizations=dataclasses.replace(spec, valve_oil_ui=pinned))


def with_moonraker_speedups(manifest):
    """Pin the manifest's Moonraker speedups to small wheels; repository checkouts carry no release wheels."""
    directory = temp_path("moonraker-speedups-")
    wheels = []
    for name, extension in (("msgspec", "_core.so"), ("uvloop", "loop.so")):
        wheel = directory / f"{name}.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(f"{name}/__init__.py", f"# {name}\n")
            binary = zipfile.ZipInfo(f"{name}/{extension}")
            binary.external_attr = 0o100755 << 16
            archive.writestr(binary, b"\x7fELF")
            archive.writestr(f"{name}-1.0.dist-info/RECORD", "")
        wheels.append(dataclasses.replace(
            manifest.system_optimizations.moonraker_speedups.wheels[0],
            archive=str(wheel),
            sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        ))
    spec = manifest.system_optimizations
    pinned = dataclasses.replace(spec.moonraker_speedups, wheels=tuple(wheels))
    return dataclasses.replace(manifest, system_optimizations=dataclasses.replace(spec, moonraker_speedups=pinned))


def fake_klipper_tree(system_root: Path, manifest, firmware: str, *, unit: bool = True):
    """Valve Oil's Klipper as a few small files, with its C helper library, QIDI's compiled modules for `firmware` as
    small stand-ins under the system root's ~/klipper, and QIDI's klipper.service (unless `unit` is False). Returns the
    manifest pinned to those modules, and a context manager that points the installer at the small tree."""
    from unittest import mock

    from installer.runtime import klipper_tree
    from installer.runtime.models import ImagePin, SystemKlipperTreeSpec

    source = temp_path("vo-klipper-source-") / "klipper"
    for relative, text in {
        "klippy/klippy.py": "# Valve Oil's klippy.py\n",
        "klippy/extras/heaters.py": "# heaters\n",
        "klippy/chelper/__init__.py": "# chelper\n",
        "klippy/chelper/serialqueue.c": "/* serialqueue */\n",
        "printers/plus5/klippy/extras/heaters.py": "# Plus 5 heaters\n",
        "printers/smart5/klippy/mcu.py": "# Smart 5 mcu\n",
        "COPYING": "GPLv3\n",
    }.items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text(text, encoding="utf-8")
    library = source.parent / "c_helper.so"
    library.write_bytes(b"\x7fELF Valve Oil c_helper")
    qidi = system_root / klipper_tree.QIDI_KLIPPY.lstrip("/")
    pins = {}
    for relative in ("extras/box_stepper.so", "extras/cs1237.cpython-39-aarch64-linux-gnu.so"):
        data = f"QIDI {relative} for {firmware}\n".encode()
        (qidi / relative).parent.mkdir(parents=True, exist_ok=True)
        (qidi / relative).write_bytes(data)
        pins[relative] = ImagePin(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    (qidi / "chelper").mkdir(parents=True, exist_ok=True)
    (qidi / "chelper/c_helper.so").write_bytes(b"\x7fELF QIDI c_helper")
    (qidi / "klippy.py").write_text("# QIDI's klippy.py\n", encoding="utf-8")
    if unit:
        service = system_root / klipper_tree.UNIT_FILE.lstrip("/")
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text(
            "[Unit]\nDescription=Klipper 3D Printer Firmware SV1\n\n[Service]\nType=simple\nUser=qidi\n"
            "RemainAfterExit=yes\nWorkingDirectory=/home/qidi/klipper\n"
            "EnvironmentFile=/home/qidi/printer_data/systemd/klipper.env\n"
            "ExecStart=/home/qidi/klippy-env/bin/python $KLIPPER_ARGS\nRestart=always\nRestartSec=10\n",
            encoding="utf-8",
        )
    spec = manifest.system_optimizations
    tree = SystemKlipperTreeSpec(qidi_modules=((firmware, tuple(sorted(pins.items()))),))
    pinned = dataclasses.replace(manifest, system_optimizations=dataclasses.replace(spec, klipper_tree=tree))
    patch = mock.patch.multiple(
        klipper_tree, TREE_SOURCE=source, CHELPER_PAYLOAD=library,
        CHELPER_SHA256=hashlib.sha256(library.read_bytes()).hexdigest(),
        CHELPER_SOURCES_SHA256=klipper_tree.chelper_sources_sha256(source),
    )
    return pinned, patch


def fake_host_run(root: Path):
    """Bounded command fixture for systemd and root-mount interactions."""
    def service_state(service: str) -> dict:
        path = root / "systemd" / f"{service}.json"
        if not path.exists():
            return {"exists": True, "service": service, "enabled": "enabled", "active": "active"}
        return json.loads(path.read_text(encoding="utf-8"))

    def write_service_state(service: str, state: dict) -> None:
        path = root / "systemd" / f"{service}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    def result(command, *, stdout="", returncode=0):
        return CompletedProcess(command, returncode, stdout=stdout)

    def run(command, **_kwargs):
        command = list(command)
        if command[:4] == ["sudo", "-S", "-p", ""]:
            command = command[4:]
        if command in (["-v"], ["-k", "-v"]):
            return result(command)
        if command[:2] == ["systemctl", "is-enabled"]:
            state = service_state(command[2])
            return result(command, stdout=f"{state['enabled']}\n")
        if command[:2] == ["systemctl", "is-active"]:
            state = service_state(command[2])
            return result(command, stdout=f"{state['active']}\n")
        if command[:2] == ["systemctl", "show"]:
            service = command[2]
            state = service_state(service)
            prop = next((item.split("=", 1)[1] for item in command if item.startswith("--property=")), "")
            if prop == "ExecStart":
                dropin = root / "etc/systemd/system/rockchip.service.d/override.conf"
                source = dropin if dropin.is_file() else root / "lib/systemd/system/rockchip.service"
                values = [line.split("=", 1)[1].strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip().startswith("ExecStart=")]
                return result(command, stdout=(values[-1] if values else "") + "\n")
            values = {"ActiveState": state.get("active", "unknown"), "SubState": state.get("sub", "dead"), "Result": state.get("result", "success"), "ExecMainStatus": str(state.get("exec_main_status", 0))}
            return result(command, stdout=values[prop] + "\n")
        if command[:1] == ["findmnt"]:
            return result(command, stdout=(root / "mounts/root.options").read_text(encoding="utf-8"))
        if command[:2] == ["systemctl", "disable"]:
            service = command[-1]
            state = service_state(service)
            state.update({"enabled": "disabled", "active": "inactive"})
            write_service_state(service, state)
            return result(command)
        if command[:2] == ["systemctl", "enable"]:
            service = command[-1]
            state = service_state(service)
            state["enabled"] = "enabled"
            write_service_state(service, state)
            return result(command)
        if command[:2] in (["systemctl", "start"], ["systemctl", "stop"]):
            service = command[-1]
            state = service_state(service)
            state["active"] = "active" if command[1] == "start" else "inactive"
            write_service_state(service, state)
            return result(command)
        if command[:2] in (["systemctl", "restart"], ["systemctl", "try-restart"]):
            # systemd can't restart a unit whose file is gone ("Unit ... not found."); the fixture holds only the
            # package's own unit files.
            service = command[-1]
            if service.startswith("tltg-") and not (root / "etc/systemd/system" / service).exists():
                return result(command, returncode=5)
            return result(command)
        if command[:2] == ["systemctl", "daemon-reload"] or command[:2] == ["systemctl", "reset-failed"]:
            return result(command)
        if command[:1] == ["mount"]:
            options = command[command.index("-o") + 1].split(",")
            (root / "mounts/root.options").write_text(
                ",".join(item for item in options if item not in {"remount", "sync", "async"})
                + (",async" if "async" in options else ",sync" if "sync" in options else "")
                + "\n",
                encoding="utf-8",
            )
            return result(command)
        if command and command[0].startswith("/etc/init.d/"):
            return result(command)
        if command[:2] == ["dpkg-deb", "-f"]:
            return result(command, stdout=Path(command[2]).read_text(encoding="utf-8"))
        if command[:1] == ["dpkg"] and command[1] == f"--root={root}" and command[2] == "-i":
            _install_fake_dosfstools(root)
            return result(command)
        raise AssertionError(f"Unexpected host command: {command}")

    return run


def snapshot_tree(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    for item in sorted(root.rglob("*")):
        if item.is_file():
            snapshot[item.relative_to(root).as_posix()] = item.read_bytes()
    return snapshot


class _JsonResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def moonraker_urlopen(
    state: str | None = "standby",
    *,
    raw_payload=None,
    saved_variables_path: Path | None = None,
    box_status: dict | None = None,
    heater_targets: dict[str, float] | None = None,
):
    payload = raw_payload
    if payload is None:
        payload = {"result": {"status": {"print_stats": {"state": state}}}}
    process = {"pid": 100}

    def saved_variables():
        if saved_variables_path is None or not saved_variables_path.exists():
            return {"box_count": 0, "enable_box": 0}
        from installer.runtime import klipper_cfg

        text = saved_variables_path.read_text(encoding="utf-8")
        section = klipper_cfg.resolve_unique_section(text, "Variables")
        values = {}
        for line in text.splitlines(keepends=True)[section.header_index + 1 : section.end_index]:
            parsed = klipper_cfg.parse_option_line(line)
            if parsed is not None:
                values[parsed.key] = parsed.value.strip().strip("'\"")
        return values

    def persist_script(request):
        if saved_variables_path is None:
            return
        import ast
        import re
        import shlex

        body = json.loads(request.data.decode("utf-8"))
        script = body["script"]
        match = re.fullmatch(r"SAVE_VARIABLE VARIABLE=([a-z0-9_]+) VALUE=(.+)", script)
        if match is None:
            raise AssertionError(f"Unexpected G-code: {script}")
        name, raw_value = match.groups()
        value = repr(ast.literal_eval(shlex.split(raw_value)[0]))
        text = saved_variables_path.read_text(encoding="utf-8")
        from installer.runtime import klipper_cfg

        try:
            text = klipper_cfg.set_option_value(text, "Variables", name, value)
        except klipper_cfg.TargetResolutionError as exc:
            if exc.reason != "missing":
                raise
            text += f"{name} = {value}\n"
        saved_variables_path.write_text(text, encoding="utf-8")

    def urlopen(request, timeout=0):
        url = getattr(request, "full_url", str(request))
        if "/printer/gcode/script" in url:
            persist_script(request)
            return _JsonResponse({"result": "ok"})
        if box_status is not None and "printer/objects/query?gcode=commands" in url:
            return _JsonResponse({"result": {"status": box_status}})
        if "printer/objects/query?save_variables" in url:
            return _JsonResponse({"result": {"status": {"save_variables": {"variables": saved_variables()}}}})
        if "/machine/services/restart" in url:
            process["pid"] += 1
            return _JsonResponse({"result": "ok"})
        if "/printer/info" in url:
            return _JsonResponse({"result": {"state": "ready", "process_id": process["pid"]}})
        heater_status = _heater_status(url, heater_targets, state)
        if heater_status is not None:
            return _JsonResponse(heater_status)
        return _JsonResponse(payload)

    return urlopen


def _heater_status(url: str, heater_targets: dict[str, float] | None, state: str | None = "standby"):
    """Answer the installer's heater queries: Klipper ready, with these heaters and targets."""
    from urllib.parse import unquote, urlsplit

    parts = urlsplit(url)
    names = [unquote(name) for name in parts.query.split("&") if name]
    targets = heater_targets or {}
    if heater_targets is not None and "idle_timeout" in names:
        # The board steps' readiness check (thr_firmware.check_thr_printer_ready): everything at once.
        return {"result": {"status": {
            "print_stats": {"state": state}, "webhooks": {"state": "ready"}, "idle_timeout": {"state": "Idle"},
            "heaters": {"available_heaters": list(targets)}, **{name: {"target": targets[name]} for name in targets}}}}
    if names == ["webhooks", "heaters"]:
        return {"result": {"status": {"webhooks": {"state": "ready"}, "heaters": {"available_heaters": list(targets)}}}}
    if names and all(name in targets for name in names):
        return {"result": {"status": {name: {"target": targets[name]} for name in names}}}
    return None



@contextmanager
def moonraker_http(urlopen):
    """Serve a Moonraker stand-in (moonraker_urlopen, or any urlopen-shaped function) over HTTP, for installer code
    that runs in another process. Yields the server's base URL."""

    class Handler(BaseHTTPRequestHandler):
        def _answer(self, data=None):
            request = urllib.request.Request(f"http://moonraker.invalid{self.path}", data=data, method=self.command)
            with urlopen(request, timeout=0) as response:
                body = response.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._answer()

        def do_POST(self):
            self._answer(self.rfile.read(int(self.headers.get("Content-Length", "0"))))

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
