#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime import static_build
from installer.runtime.manifest import load_manifest
from installer.runtime.naming import (
    ARCHIVE_NAME,
    BUNDLE_ROOT_NAME,
    INSTALL_BACKUP_LABEL_PREFIX,
    UNINSTALL_BACKUP_LABEL_PREFIX,
)
from installer.tests.helpers import (  # noqa: E402
    build_env as printer_env,
    moonraker_http,
    moonraker_urlopen,
    stock_printer_data,
)


COMMAND_TIMEOUT = 600  # seconds for one run of the bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--usb-image", help="the USB drive image built from the archive")
    parser.add_argument("--usb-stub-qidi", action="store_true",
                        help="the USB drive image stands in for QIDI's target packages")
    args = parser.parse_args(argv)

    archive_path = Path(args.archive).resolve()
    validate_bundle_archive(archive_path)
    if args.usb_image:
        check_usb_image(Path(args.usb_image).resolve(), archive_path, stub_qidi=args.usb_stub_qidi)

    workspace = Path(tempfile.mkdtemp(prefix="installer-smoke-"))
    try:
        check_bundle(archive_path, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    print("bundle smoke test passed")
    return 0


def check_bundle(archive_path: Path, workspace: Path) -> None:
    """The extracted bundle's contents, its commands' help and version, and its installs on stand-in printers."""
    extract_root = workspace / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(extract_root)
    bundle_root = extract_root / BUNDLE_ROOT_NAME
    if not (bundle_root / "restore.sh").exists():
        raise SystemExit("bundle smoke test is missing restore.sh")
    if not (bundle_root / "installer/system/qidiclient-static-gifs.tar.gz").exists():
        raise SystemExit("bundle smoke test is missing qidiclient static GIF archive")
    for printer in load_manifest(bundle_root / "installer/package.yaml").printers:
        for firmware in printer.firmware:
            snapshot_root = bundle_root / "installer" / printer.stock_snapshot(firmware) / "config"
            printer_config = snapshot_root / "printer.cfg"
            if not printer_config.exists():
                raise SystemExit(f"bundle smoke test is missing {firmware} stock config snapshot")
            for excluded in ("MCU_ID.cfg", "box.cfg", "fluidd.cfg", "saved_variables.cfg"):
                if (snapshot_root / excluded).exists():
                    raise SystemExit(
                        f"bundle smoke test found excluded {firmware} stock file: {excluded}"
                    )
    snapshot_base = bundle_root / "installer/stock/qidi-max4-defaults/firmwares"

    firmware_05_root = snapshot_base / "01.01.06.05" / "config"
    firmware_05_printer = (firmware_05_root / "printer.cfg").read_text(encoding="utf-8")
    for expected in (
        "tachometer_poll_interval: 0.0005",
        "# [smart_output_pin polar_cooler]",
        "# [smart_output_pin beeper]",
        "[output_pin polar_cooler]",
    ):
        if expected not in firmware_05_printer:
            raise SystemExit(f"bundle .05 stock printer.cfg is missing {expected}")
    firmware_05_qd_macro = (
        firmware_05_root / "klipper-macros-qd/qd_macro.cfg"
    ).read_text(encoding="utf-8")
    for expected in ("SET_KINEMATIC_POSITION Z=150", "G1 Z1 F600"):
        if expected not in firmware_05_qd_macro:
            raise SystemExit(f"bundle .05 stock qd_macro.cfg is missing {expected}")
    firmware_0701_root = snapshot_base / "01.01.07.01" / "config"
    firmware_0701_printer = (firmware_0701_root / "printer.cfg").read_text(encoding="utf-8")
    for expected in ("[box485]", "[box2base]", "[multi_color_controller]"):
        if expected not in firmware_0701_printer:
            raise SystemExit(f"bundle 07.01 stock printer.cfg is missing {expected}")
    firmware_0701_filament = (
        firmware_0701_root / "klipper-macros-qd/filament.cfg"
    ).read_text(encoding="utf-8")
    if "T_MANAGER T=15" not in firmware_0701_filament:
        raise SystemExit("bundle 07.01 stock filament.cfg is missing T_MANAGER tool macros")
    package_manifest = load_manifest(bundle_root / "installer/package.yaml")
    for patch in package_manifest.install.source_patches:
        for variant in patch.variants:
            payload = bundle_root / "installer" / variant.source
            if not payload.is_file() or payload.is_symlink():
                raise SystemExit(
                    f"bundle smoke test is missing source payload: {variant.source}"
                )
            payload_bytes = payload.read_bytes()
            try:
                compile(payload_bytes, str(payload), "exec")
            except SyntaxError as exc:
                raise SystemExit(
                    f"bundle source payload does not compile: {variant.source}: {exc}"
                ) from exc
            if hashlib.sha256(payload_bytes).hexdigest() != variant.desired_sha256:
                raise SystemExit(
                    "bundle source payload hash does not match package manifest: "
                    f"{variant.source}"
                )
    ui = package_manifest.system_optimizations.valve_oil_ui
    if ui.sha256 is None:
        raise SystemExit("bundled package.yaml does not pin the Valve Oil UI archive")
    try:
        static_build.VALVE_OIL_UI.archive_files(bundle_root / "installer" / ui.archive, ui.sha256)
    except static_build.StaticBuildError as exc:
        raise SystemExit(f"bundle Valve Oil UI archive is invalid: {exc.message}") from exc
    package_version = read_package_version(bundle_root / "installer/package.yaml")
    help_output = run_command([str(bundle_root / "install.sh"), "--help"], cwd=bundle_root, env=os.environ.copy())
    if help_output.returncode != 0:
        raise SystemExit(help_output.stdout + help_output.stderr)
    for expected in (
        "Valve Oil installer runtime",
        "{install,uninstall,clear-recovery-sentinel,restore-backup",
        "-v, --version",
        "--plain",
        "--debug",
        "--dry-run",
        "--demo-tui",
        "--yes",
        "--disable-ai-detection",
        "--keep-ai-detection",
        "--keep-system-optimizations",
        "--reboot-host",
    ):
        if expected not in help_output.stdout:
            raise SystemExit(f"install.sh help output is missing {expected}")
    for relative in ("scripts/flash_thr_firmware.py", "scripts/build_thr_firmware.py", "firmware/max4-thr-valve-oil-v1.json", "firmware/smart5-thr-valve-oil-v1.json", "firmware/FLASHING.md"):
        if not (bundle_root / relative).is_file():
            raise SystemExit("bundle is missing THR firmware support: " + relative)
    tool_help = run_command([sys.executable, str(bundle_root / "scripts/flash_thr_firmware.py"), "--help"], cwd=bundle_root, env=os.environ.copy())
    if tool_help.returncode != 0:
        raise SystemExit(tool_help.stdout + tool_help.stderr)
    for relative in ("box-firmware.sh", "scripts/flash_box_firmware.py", "scripts/build_box_firmware.py", "firmware/max4-box-valve-oil-v1.json", "firmware/BOX_FIRMWARE.md"):
        if not (bundle_root / relative).is_file():
            raise SystemExit("bundle is missing Box firmware support: " + relative)
    box_tool_help = run_command([sys.executable, str(bundle_root / "scripts/flash_box_firmware.py"), "--help"], cwd=bundle_root, env=os.environ.copy())
    box_help = run_command([str(bundle_root / "box-firmware.sh"), "--help"], cwd=bundle_root, env=os.environ.copy())
    if box_tool_help.returncode != 0 or box_help.returncode != 0 or "rescue" not in box_help.stdout:
        raise SystemExit(box_tool_help.stdout + box_tool_help.stderr + box_help.stdout + box_help.stderr)
    for relative in ("mainboard-firmware.sh", "scripts/flash_mcu_firmware.py", "scripts/build_mcu_firmware.py", "firmware/max4-mcu-valve-oil-v1.json", "firmware/MAINBOARD_FIRMWARE.md"):
        if not (bundle_root / relative).is_file():
            raise SystemExit("bundle is missing mainboard firmware support: " + relative)
    mainboard_tool_help = run_command([sys.executable, str(bundle_root / "scripts/flash_mcu_firmware.py"), "--help"], cwd=bundle_root, env=os.environ.copy())
    mainboard_help = run_command([str(bundle_root / "mainboard-firmware.sh"), "--help"], cwd=bundle_root, env=os.environ.copy())
    if mainboard_tool_help.returncode != 0 or mainboard_help.returncode != 0 or "mainboard-firmware" not in mainboard_help.stdout:
        raise SystemExit(mainboard_tool_help.stdout + mainboard_tool_help.stderr + mainboard_help.stdout + mainboard_help.stderr)
    version_output = run_command([str(bundle_root / "install.sh"), "-v"], cwd=bundle_root, env=os.environ.copy())
    if version_output.returncode != 0:
        raise SystemExit(version_output.stdout + version_output.stderr)
    if version_output.stdout.strip() != f"Valve Oil installer {package_version}":
        raise SystemExit("install.sh -v output did not match package.version")

    alias_dry_run = run_command(
        [str(bundle_root / "install.sh"), "--uninstall", "--dry-run", "--plain"],
        cwd=bundle_root,
        env=build_env(stand_in_printer_data(package_manifest), moonraker_url="http://moonraker.invalid", workspace=workspace),
    )
    if alias_dry_run.returncode != 0 or "Nothing to uninstall." not in alias_dry_run.stdout:
        raise SystemExit("install.sh --uninstall alias did not preserve no-op behavior")

    check_installs(bundle_root, workspace, package_manifest)


FIRST_INSTALL = """\
# A new install reads the printer's boards before anything else (board_firmware.board_preflight), which needs a
# printer. This runs the bundle's install step itself, from the bundle's own code, as that preflight's caller does.
import os
import sys
from pathlib import Path

bundle_root = Path(sys.argv[1])
sys.path[:0] = [str(bundle_root / "installer/runtime/vendor"), str(bundle_root)]

from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install

environ = dict(os.environ)
paths = resolve_runtime_paths(bundle_root=bundle_root, environ=environ)
run_install(paths, load_manifest(paths.installer_root / "package.yaml"), PlainReporter(sys.stdout), environ=environ)
"""


def first_install(bundle_root: Path, workspace: Path, env: dict[str, str]) -> None:
    driver = workspace / "first_install.py"
    driver.write_text(FIRST_INSTALL, encoding="utf-8")
    run = run_command([sys.executable, "-I", "-S", str(driver), str(bundle_root)], cwd=bundle_root, env=env)
    if run.returncode != 0:
        raise SystemExit("first install smoke test failed:\n" + run.stdout + run.stderr)


def check_installed_modules(bundle_root: Path, printer_root: Path, manifest, *, installed: bool) -> None:
    for module in manifest.install.extra_modules:
        target = printer_root / "klipper" / module.destination
        if not installed:
            if target.exists():
                raise SystemExit(f"uninstall smoke test did not remove {target.name}")
        elif not target.is_file():
            raise SystemExit(f"install smoke test did not install {target.name}")
        elif target.read_bytes() != (bundle_root / "installer" / module.source).read_bytes():
            raise SystemExit(f"install smoke test installed an unexpected {target.name} payload")


def check_installs(bundle_root: Path, workspace: Path, manifest) -> None:
    """Run the bundle against stand-in printers: a new install's plan, a first install, an update through each front
    end, uninstall, and the restore helper. None of it may ask this host for root."""
    install_sh = str(bundle_root / "install.sh")
    state_file = "config/tltg_optimized_state.yaml"

    with stand_in_printer(manifest, workspace) as (printer_root, env):
        dry_run = run_command([install_sh, "--dry-run", "--plain"], cwd=bundle_root, env=env)
        if dry_run.returncode != 0:
            raise SystemExit(dry_run.stdout + dry_run.stderr)
        if (printer_root / state_file).exists():
            raise SystemExit("install dry-run smoke test unexpectedly created state file")
        if list(printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip")):
            raise SystemExit("install dry-run smoke test unexpectedly created a backup zip")
        if "Dry-run summary:" not in dry_run.stdout:
            raise SystemExit("install dry-run smoke test did not emit the dry-run summary")
        if "Host reboot dry-run: would schedule a delayed reboot" not in dry_run.stdout:
            raise SystemExit("install dry-run smoke test did not report the host reboot")

    with stand_in_printer(manifest, workspace) as (printer_root, env):
        first_install(bundle_root, workspace, env)
        if not (printer_root / state_file).exists():
            raise SystemExit("install smoke test did not create state file")
        check_installed_modules(bundle_root, printer_root, manifest, installed=True)
        backups = sorted(printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip"))
        if len(backups) != 1:
            raise SystemExit("install smoke test did not create the expected install backup zip")

        update = run_command([install_sh, "--yes", "--plain"], cwd=bundle_root, env=env)
        if update.returncode != 0:
            raise SystemExit(update.stdout + update.stderr)
        check_installed_modules(bundle_root, printer_root, manifest, installed=True)

        uninstall_dry_run = run_command([install_sh, "--uninstall", "--dry-run", "--plain"], cwd=bundle_root, env=env)
        if uninstall_dry_run.returncode != 0:
            raise SystemExit(uninstall_dry_run.stdout + uninstall_dry_run.stderr)
        if not (printer_root / state_file).exists():
            raise SystemExit("uninstall dry-run smoke test unexpectedly removed state file")
        if list(printer_root.glob(f"{UNINSTALL_BACKUP_LABEL_PREFIX}-*.zip")):
            raise SystemExit("uninstall dry-run smoke test unexpectedly created a backup zip")
        if "Dry-run summary:" not in uninstall_dry_run.stdout:
            raise SystemExit("uninstall dry-run smoke test did not emit the dry-run summary")

        uninstall = run_command([install_sh, "--uninstall", "--yes", "--plain"], cwd=bundle_root, env=env)
        if uninstall.returncode != 0:
            raise SystemExit(uninstall.stdout + uninstall.stderr)
        if (printer_root / state_file).exists():
            raise SystemExit("uninstall smoke test did not remove state file")
        check_installed_modules(bundle_root, printer_root, manifest, installed=False)

    with stand_in_printer(manifest, workspace) as (printer_root, env):
        first_install(bundle_root, workspace, env)
        env["TERM"] = "xterm-256color"
        rich_update = run_command_with_pty([install_sh, "--yes"], cwd=bundle_root, env=env)
        if rich_update.returncode != 0:
            raise SystemExit(rich_update.stdout)
        if "Valve Oil installer" not in rich_update.stdout:
            raise SystemExit("rich launcher smoke test did not render the live TUI")

        rich_vendor = bundle_root / "installer/runtime/vendor/rich"
        disabled_rich_vendor = bundle_root / "installer/runtime/vendor/rich.disabled"
        rich_vendor.rename(disabled_rich_vendor)
        try:
            fallback_update = run_command_with_pty([install_sh, "--yes"], cwd=bundle_root, env=env)
        finally:
            disabled_rich_vendor.rename(rich_vendor)
        if fallback_update.returncode != 0:
            raise SystemExit(fallback_update.stdout)
        if "stage 1/5" not in fallback_update.stdout:
            raise SystemExit("plain fallback smoke test did not emit contract stage output")
        if "Valve Oil installer" in fallback_update.stdout:
            raise SystemExit("plain fallback smoke test unexpectedly rendered the rich TUI")

    with stand_in_printer(manifest, workspace) as (printer_root, env):
        env["TERM"] = "xterm-256color"
        env["TLTG_OPTIMIZED_DEMO_TUI_DELAY_SECONDS"] = "0.05"
        rich_demo = run_command_with_pty([install_sh, "--demo-tui"], cwd=bundle_root, env=env)
        if rich_demo.returncode != 0:
            raise SystemExit(rich_demo.stdout)
        if "Valve Oil installer" not in rich_demo.stdout:
            raise SystemExit("demo TUI smoke test did not render the live TUI")
        if "Installed." not in rich_demo.stdout:
            raise SystemExit("demo TUI smoke test did not emit the install success line")
        if (printer_root / state_file).exists():
            raise SystemExit("demo TUI smoke test unexpectedly created state file")
        if list(printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip")):
            raise SystemExit("demo TUI smoke test unexpectedly created a backup zip")

    with stand_in_printer(manifest, workspace) as (printer_root, env):
        printer_cfg = printer_root / "config/printer.cfg"
        stock_printer_cfg = printer_cfg.read_text(encoding="utf-8")
        first_install(bundle_root, workspace, env)
        backup = next(iter(sorted(printer_root.glob(f"{INSTALL_BACKUP_LABEL_PREFIX}-*.zip"))), None)
        if backup is None:
            raise SystemExit("restore helper smoke test did not create the expected install backup zip")
        invalid_backup = printer_root / "invalid-restore.zip"
        with zipfile.ZipFile(invalid_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("notes.txt", "not a runtime backup\n")
        installed_printer_cfg = printer_cfg.read_text(encoding="utf-8")
        invalid_restore = run_command([str(bundle_root / "restore.sh"), "--backup", str(invalid_backup)],
                                      cwd=bundle_root, env=env, input_text="RESTORE\n")
        if invalid_restore.returncode == 0:
            raise SystemExit("restore helper smoke test unexpectedly accepted an invalid backup zip")
        if printer_cfg.read_text(encoding="utf-8") != installed_printer_cfg:
            raise SystemExit("restore helper smoke test changed printer.cfg after an invalid backup zip")

        restore = run_command([str(bundle_root / "restore.sh"), "--backup", str(backup)], cwd=bundle_root, env=env,
                              input_text="RESTORE\n")
        if restore.returncode != 0:
            raise SystemExit(restore.stdout + restore.stderr)
        if printer_cfg.read_text(encoding="utf-8") != stock_printer_cfg:
            raise SystemExit("restore helper smoke test did not restore the archived printer.cfg")
        if (printer_root / state_file).exists():
            raise SystemExit("restore helper smoke test unexpectedly preserved the installed state file")

    refused = refused_host_commands(workspace)
    if refused:
        raise SystemExit("bundle smoke test asked this host for root on a stand-in printer:\n  " + "\n  ".join(refused))


def check_usb_image(image: Path, bundle_archive: Path, *, stub_qidi: bool) -> None:
    """The drive image's layout, the printers' zips and their hand-off package, QIDI's files against their pins, and
    the built postinst and launcher on a fake printer of each model."""
    from installer.tests.usb_harness import FakePrinter, moonraker
    from scripts import usb_drive_image as usb

    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    bundle = bundle_archive.read_bytes()
    bundle_sha256 = hashlib.sha256(bundle).hexdigest()
    with zipfile.ZipFile(image) as drive:
        names = drive.namelist()
        zips = sorted(name for name in names if name.startswith(f"{usb.QD_UPDATE}/"))
        expected = {usb.BUNDLE_ON_DRIVE, *zips,
                    *(f"{usb.DRIVE_FOLDER}/{printer.target_firmware.archive}" for printer in manifest.printers)}
        if len(names) != len(set(names)) or set(names) != expected or len(zips) != len(manifest.printers):
            raise SystemExit(f"USB image layout is unexpected: {names}")
        if drive.read(usb.BUNDLE_ON_DRIVE) != bundle:
            raise SystemExit("USB image does not carry the bundle archive")
        for printer in manifest.printers:
            # QIDI's app lists the zips under QD_Update/ that start with the printer's prefix: exactly one.
            own = [name for name in zips if Path(name).name.startswith(usb.zip_prefix(printer))]
            if len(own) != 1 or not re.fullmatch(rf"{re.escape(usb.zip_prefix(printer))}ValveOil_[^/]+\.zip",
                                                 Path(own[0]).name):
                raise SystemExit(f"USB image does not hold exactly one {usb.zip_prefix(printer)} zip: {zips}")
            check_usb_printer_zip(printer, Path(own[0]).name, drive.read(own[0]), bundle_sha256)
            target = printer.target_firmware
            data = drive.read(f"{usb.DRIVE_FOLDER}/{target.archive}")
            carried = data.startswith(usb.STUB_PREFIX) if stub_qidi else (
                len(data) == target.size and hashlib.sha256(data).hexdigest() == target.sha256)
            if not carried:
                raise SystemExit(f"USB image's {target.archive} does not match its pin")
            del data

        workspace = Path(tempfile.mkdtemp(prefix="usb-image-smoke-"))
        try:
            for index, printer in enumerate(manifest.printers):
                own = next(name for name in zips if Path(name).name.startswith(usb.zip_prefix(printer)))
                fake = FakePrinter(workspace / printer.id, printer_id=printer.id, system_package=printer.soc_package,
                                   qidi_version=printer.target_firmware.soc_version)
                fake.put_on_drive(Path(own).name, drive.read(own), bundle_archive)
                result = fake.offline_update(Path(own).name)
                if result.returncode != 0 or fake.status.get("result") != "restored":
                    raise SystemExit(f"USB hand-off did not restore {printer.id}: {fake.log.read_text()}")
                restore_set = [item for item in printer.restore_sets
                               if item.qidi_version == printer.target_firmware.soc_version][-1]
                left = {path.name: path.read_bytes() for path in fake.update.iterdir()}
                pinned = dict(restore_set.files)
                if set(left) != {"qidi.deb", *pinned} or any(
                        hashlib.sha256(left[name]).hexdigest() != pin.sha256 for name, pin in pinned.items()):
                    raise SystemExit(f"USB hand-off left {printer.id}'s update folder unlike set {restore_set.name}")
                if index:
                    continue
                with moonraker("ready") as url:
                    launched = fake.launch(moonraker_url=url)
                bootstrap = fake.home / "valve-oil/installer/runtime/bootstrap.py"
                if launched.returncode != 0 or fake.exec_arguments() != [
                        "-I", "-S", str(bootstrap), "usb", "--drive", str(fake.drive)] or not bootstrap.is_file():
                    raise SystemExit(f"USB launcher did not start the installer: {fake.log.read_text()}")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


def check_usb_printer_zip(printer, zip_name: str, data: bytes, bundle_sha256: str) -> None:
    from scripts import usb_drive_image as usb

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("firmware_manifest.json"))
        deb_file = manifest.get("SOC", {}).get("file", "")
        if manifest != {"SOC": {"file": deb_file, "version": usb.SOC_VERSION, "region": "NA"}} or \
                not re.fullmatch(rf"{usb.HANDOFF_PACKAGE}_[^_/]+_all\.deb", deb_file):
            raise SystemExit(f"{zip_name} firmware_manifest.json is not the {usb.SOC_VERSION} SOC manifest: {manifest}")
        folder = usb.HANDOFF_FOLDER
        expected = {"firmware_manifest.json", deb_file, f"{folder}/printer", f"{folder}/zip", f"{folder}/launch.sh",
                    f"{folder}/{ARCHIVE_NAME}.sha256", f"{folder}/update_screen.py", f"{folder}/starting.png"}
        # The starting screen launch.sh draws from QIDI's app restart until the installer draws its own.
        if archive.read(f"{folder}/update_screen.py") != usb.UPDATE_SCREEN.read_bytes():
            raise SystemExit(f"{zip_name} update_screen.py is not installer/runtime/update_screen.py")
        if not archive.read(f"{folder}/starting.png").startswith(b"\x89PNG\r\n\x1a\n"):
            raise SystemExit(f"{zip_name} starting.png is not a PNG")
        for restore_set in printer.restore_sets:
            expected |= {f"{folder}/{restore_set.name}/{name}" for name, _ in restore_set.files}
            expected.add(f"{folder}/{restore_set.name}/SHA256SUMS")
            sums = archive.read(f"{folder}/{restore_set.name}/SHA256SUMS").decode()
            if sums != "".join(f"{pin.sha256}  {name}\n" for name, pin in restore_set.files):
                raise SystemExit(f"{zip_name} set {restore_set.name} SHA256SUMS does not match its pins")
            for name, pin in restore_set.files:
                file_data = archive.read(f"{folder}/{restore_set.name}/{name}")
                if len(file_data) != pin.size or hashlib.sha256(file_data).hexdigest() != pin.sha256:
                    raise SystemExit(f"{zip_name} {restore_set.name}/{name} does not match its pin")
        if set(names) != expected or any(Path(name).name.startswith(printer.soc_file_prefix) for name in names):
            raise SystemExit(f"{zip_name} holds unexpected files: {sorted(set(names) ^ expected)}")
        if archive.read(f"{folder}/printer").decode() != f"{printer.id}\n" or \
                archive.read(f"{folder}/zip").decode() != f"{zip_name}\n":
            raise SystemExit(f"{zip_name} does not name its printer and itself")
        if archive.read(f"{folder}/{ARCHIVE_NAME}.sha256").decode() != f"{bundle_sha256}  {ARCHIVE_NAME}\n":
            raise SystemExit(f"{zip_name} does not pin the bundle on the drive")
        if archive.read(f"{folder}/launch.sh") != (REPO_ROOT / "installer/usb/launch.sh").read_bytes():
            raise SystemExit(f"{zip_name} does not carry installer/usb/launch.sh")
        members = usb.read_deb(archive.read(deb_file))
    if list(members) != ["debian-binary", "control.tar.gz", "data.tar.gz"] or members["debian-binary"] != b"2.0\n":
        raise SystemExit(f"{deb_file} is not a Debian package: {list(members)}")
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as data_tar:
        if data_tar.getmembers():
            raise SystemExit(f"{deb_file} installs files")
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as control_tar:
        control = control_tar.extractfile("./control").read().decode()
        postinst = control_tar.getmember("./postinst")
        script = control_tar.extractfile(postinst).read()
    fields = dict(line.split(": ", 1) for line in control.splitlines() if ": " in line and not line.startswith(" "))
    if fields.get("Package") != usb.HANDOFF_PACKAGE or fields.get("Architecture") != "all" or \
            f"_{fields.get('Version')}_" not in deb_file:
        raise SystemExit(f"{deb_file} control is unexpected: {control}")
    if postinst.mode != 0o755 or script != (REPO_ROOT / "installer/usb/postinst").read_bytes():
        raise SystemExit(f"{deb_file} does not carry installer/usb/postinst as its postinst")


SUDO_GUARD = """#!/bin/sh
# Stands in for sudo while the smoke test runs the bundle: `sudo -v` succeeds, as it does on a printer, and any
# command that would run as root is recorded and refused.
while [ $# -gt 0 ]; do
    case "$1" in
        -S) shift ;;
        -p) shift 2 ;;
        *) break ;;
    esac
done
[ "$1" = "-v" ] && exit 0
printf '%s\\n' "$*" >> "$(dirname "$0")/refused"
exit 1
"""


def host_guard(workspace: Path) -> Path:
    """A PATH entry whose sudo refuses every command, so a bundle run on a stand-in printer can't change this host."""
    guard = workspace / "host-guard"
    if not guard.exists():
        guard.mkdir(parents=True)
        (guard / "sudo").write_text(SUDO_GUARD, encoding="utf-8")
        (guard / "sudo").chmod(0o755)
    return guard


def refused_host_commands(workspace: Path) -> list[str]:
    refused = host_guard(workspace) / "refused"
    return refused.read_text(encoding="utf-8").splitlines() if refused.exists() else []


def stand_in_printer_data(manifest) -> Path:
    """printer_data of a Max 4 on the firmware this release installs for, so no run plans a QIDI firmware update."""
    printer = next(printer for printer in manifest.printers if printer.id == "max4")
    return stock_printer_data(manifest, printer.target_firmware.version)


@contextmanager
def stand_in_printer(manifest, workspace: Path):
    """A stand-in printer and the environment that points a bundle run at it: (printer_data, env)."""
    printer_root = stand_in_printer_data(manifest)
    moonraker = moonraker_urlopen(saved_variables_path=printer_root / "config/saved_variables.cfg",
                                  heater_targets={"extruder": 0, "heater_bed": 0, "chamber": 0})
    with moonraker_http(moonraker) as url:
        yield printer_root, build_env(printer_root, moonraker_url=url + "/printer/objects/query?print_stats",
                                      workspace=workspace)


def build_env(printer_root: Path, *, moonraker_url: str, workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{host_guard(workspace)}{os.pathsep}{env.get('PATH', '')}"
    env.update(printer_env(printer_root, moonraker_url=moonraker_url))
    return env


def run_command(args: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        check=False,
        timeout=COMMAND_TIMEOUT,
    )


def run_command_with_pty(args: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None):
    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            text=False,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    if input_text:
        os.write(master_fd, input_text.encode("utf-8"))

    # A run that waits for input it never gets is stopped at the time limit; the read below then ends.
    watchdog = threading.Timer(COMMAND_TIMEOUT, process.kill)
    watchdog.start()
    chunks: list[bytes] = []
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                if process.poll() is not None:
                    break
                continue
            chunks.append(chunk)
            if process.poll() is not None and len(chunk) < 4096:
                continue
    finally:
        watchdog.cancel()
        os.close(master_fd)

    return subprocess.CompletedProcess(
        args=args,
        returncode=process.wait(),
        stdout=b"".join(chunks).decode("utf-8", errors="replace"),
        stderr="",
    )


def read_package_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'(?m)^package:\n(?:^[ \t]+.*\n)*?^[ \t]+version:\s*["\']?([^"\'\n]+)', text)
    if match is None:
        raise ValueError(f"Could not read package.version from {path}")
    return match.group(1)


def validate_bundle_archive(path: Path) -> None:
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/"):
                raise ValueError(f"Archive entry has absolute path: {member.name}")
            member_path = Path(member.name)
            if member_path.parts[0] != BUNDLE_ROOT_NAME:
                raise ValueError(f"Archive entry escapes bundle root: {member.name}")
            if any(part == ".." for part in member_path.parts):
                raise ValueError(f"Archive entry contains ..: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"Archive entry type is not allowed: {member.name}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
