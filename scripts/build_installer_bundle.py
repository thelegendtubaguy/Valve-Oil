#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime import static_build
from installer.runtime import moonraker_speedups
from installer.runtime.compatibility import load_supported_upgrade_sources, validate_manifest_compatibility
from installer.runtime.manifest import load_manifest
from installer.runtime.mirror import managed_tree_source_files
from installer.runtime.naming import BUNDLE_NAME_PREFIX, BUNDLE_ROOT_NAME
ALLOWED_FILES = [
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "installer/package.yaml",
    "installer/supported_upgrade_sources.yaml",
    "installer/release/install.sh",
    "installer/release/restore.sh",
    "installer/release/auto-update.sh",
    "installer/release/box-firmware.sh",
    "installer/release/mainboard-firmware.sh",
    "installer/release/addons.sh",
    "scripts/flash_thr_firmware.py",
    "scripts/thr_bootloaders.py",
    "scripts/build_thr_firmware.py",
    "firmware/max4-thr-valve-oil-v1.json",
    "scripts/flash_box_firmware.py",
    "scripts/build_box_firmware.py",
    "firmware/max4-box-valve-oil-v1.json",
    "firmware/BOX_FIRMWARE.md",
    "scripts/flash_mcu_firmware.py",
    "scripts/build_mcu_firmware.py",
    "firmware/max4-mcu-valve-oil-v1.json",
    "firmware/MAINBOARD_FIRMWARE.md",
    "firmware/FLASHING.md",
    "scripts/build_zram_modules.py",
    "kernel/qidi_max4_zram_defconfig",
    "kernel/README.md",
]
ALLOWED_DIRECTORIES = [
    "LICENSES",
    "installer/runtime",
    "installer/klipper/tltg-optimized-macros",
    "installer/klipper/printers",
    "installer/klipper/qidi",
    "installer/klipper/tltg",
    "installer/stock",
    "installer/system",
]
# Release downloads are cached by checksum outside the bundled tree.
DOWNLOAD_CACHE = REPO_ROOT / "build/downloads"
VALVE_OIL_UI_BUILD = REPO_ROOT / "build/valve-oil-ui"
# Fixed entry metadata keeps the Valve Oil UI archive identical for identical build output.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--channel", choices=["release", "dev"], required=True)
    parser.add_argument("--build-id")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    compatibility = load_supported_upgrade_sources(
        REPO_ROOT / "installer/supported_upgrade_sources.yaml"
    )
    validate_manifest_compatibility(manifest, compatibility)
    validate_source_payloads(manifest)
    validate_printer_macro_trees(manifest)
    fluidd_archive = fetch_fluidd_build(manifest)
    # Dev bundles share a package version, so the build ID tells open tabs a newer dev build was installed.
    ui_version = manifest.package.version if args.channel == "release" else f"{manifest.package.version}+{args.build_id or 'local'}"
    ui_archive, ui_sha256 = build_valve_oil_ui(manifest, ui_version)
    speedup_wheels = fetch_moonraker_speedups(manifest)
    addon_files = fetch_addon_files(manifest)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_root = REPO_ROOT / "build/installer-bundle"
    bundle_root = staging_root / BUNDLE_ROOT_NAME
    if staging_root.exists():
        shutil.rmtree(staging_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    for file_path in ALLOWED_FILES:
        source = REPO_ROOT / file_path
        destination = bundle_root / file_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    shutil.copy2(REPO_ROOT / "installer/release/install.sh", bundle_root / "install.sh")
    shutil.copy2(REPO_ROOT / "installer/release/restore.sh", bundle_root / "restore.sh")
    shutil.copy2(REPO_ROOT / "installer/release/auto-update.sh", bundle_root / "auto-update.sh")
    shutil.copy2(REPO_ROOT / "installer/release/box-firmware.sh", bundle_root / "box-firmware.sh")
    shutil.copy2(REPO_ROOT / "installer/release/mainboard-firmware.sh", bundle_root / "mainboard-firmware.sh")
    shutil.copy2(REPO_ROOT / "installer/release/addons.sh", bundle_root / "addons.sh")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for directory in ALLOWED_DIRECTORIES:
        source = REPO_ROOT / directory
        destination = bundle_root / directory
        shutil.copytree(source, destination, ignore=ignore)
    shutil.copy2(fluidd_archive, bundle_root / "installer" / manifest.system_optimizations.fluidd_build.archive)
    shutil.copy2(ui_archive, bundle_root / "installer" / manifest.system_optimizations.valve_oil_ui.archive)
    pin_valve_oil_ui(bundle_root / "installer/package.yaml", ui_sha256)
    for wheel, cached in speedup_wheels:
        destination = bundle_root / "installer" / wheel.archive
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, destination)
    for artifact, cached in addon_files:
        destination = bundle_root / "installer" / artifact.archive
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, destination)

    (bundle_root / "RELEASE_METADATA.yaml").write_text(
        render_release_metadata(manifest.package.version, args.channel, args.build_id),
        encoding="utf-8",
    )

    stable_name, traceable_name = bundle_names(
        channel=args.channel,
        package_version=manifest.package.version,
        build_id=args.build_id,
    )
    stable_archive = output_dir / stable_name
    traceable_archive = output_dir / traceable_name

    create_archive(bundle_root, stable_archive)
    if traceable_archive != stable_archive:
        shutil.copy2(stable_archive, traceable_archive)

    write_sha256(stable_archive)
    if traceable_archive != stable_archive:
        write_sha256(traceable_archive)

    validate_archive(stable_archive)
    if traceable_archive != stable_archive:
        validate_archive(traceable_archive)

    if args.smoke_test:
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/smoke_test_installer_bundle.py"), "--archive", str(stable_archive)],
            check=True,
        )

    print(stable_archive)
    if traceable_archive != stable_archive:
        print(traceable_archive)
    return 0



def validate_source_payloads(manifest) -> None:
    for patch in manifest.install.source_patches:
        for variant in patch.variants:
            payload = REPO_ROOT / "installer" / variant.source
            if payload.is_symlink() or not payload.is_file():
                raise ValueError(
                    f"Source patch payload is not a regular file: {payload}"
                )
            value = payload.read_bytes()
            compile(value, str(payload), "exec")
            digest = hashlib.sha256(value).hexdigest()
            if digest != variant.desired_sha256:
                raise ValueError(
                    "Source patch payload hash does not match "
                    f"{patch.id} for {variant.firmware} stock "
                    f"{variant.expected_sha256}"
                )


def validate_printer_macro_trees(manifest) -> None:
    """Each printer's macro folder must complete the common macros without replacing any of them, and every
    printer's folder must define the same config sections so each printer has the same macros. Driver
    sections are left out: they add options to the printer's own stock driver sections, which differ by printer."""
    sections_by_printer: dict[str, set[str]] = {}
    for printer in manifest.printers:
        common, printer_source = manifest.managed_tree_sources(printer.firmware[0])
        managed_tree_source_files(
            (REPO_ROOT / "installer" / common, REPO_ROOT / "installer" / printer_source),
            required_files=manifest.managed_tree.required_files,
        )
        sections_by_printer[printer.id] = {
            line.strip().lower()
            for path in sorted((REPO_ROOT / "installer" / printer_source).rglob("*.cfg"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("[") and not line.lower().startswith("[tmc")
        }
    expected_id, expected = next(iter(sections_by_printer.items()))
    for printer_id, sections in sections_by_printer.items():
        if sections != expected:
            raise ValueError(
                f"Printer macros for {printer_id} and {expected_id} differ in sections: "
                + ", ".join(sorted(sections ^ expected))
            )


def fetch_fluidd_build(manifest) -> Path:
    """Return the pinned Fluidd release archive, downloading it once and verifying it against the manifest."""
    build = manifest.system_optimizations.fluidd_build
    cached = DOWNLOAD_CACHE / f"fluidd-{build.sha256}.zip"
    if not cached.is_file():
        print(f"Downloading Fluidd {build.version} from {build.url}", file=sys.stderr)
        with urllib.request.urlopen(build.url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != build.sha256:
            raise ValueError(f"Fluidd {build.version} from {build.url} does not match the pinned sha256.")
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_name(cached.name + ".partial")
        partial.write_bytes(data)
        partial.replace(cached)
    static_build.FLUIDD.archive_files(cached, build.sha256)
    return cached


def build_valve_oil_ui(manifest, version: str) -> tuple[Path, str]:
    """Build Valve Oil UI from source, labelled with version, and return its archive and sha256."""
    spec = manifest.system_optimizations.valve_oil_ui
    if spec.sha256 is not None:
        raise ValueError("installer/package.yaml must not pin valve_oil_ui.sha256; the bundle builder pins the archive it builds.")
    source = REPO_ROOT / spec.source
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit(
            f"Building Valve Oil UI needs Node.js and npm; install the Node version in {spec.source}/.nvmrc."
        )
    environ = {**os.environ, "VO_UI_VERSION": version}
    for command in ([npm, "ci"], [npm, "run", "build"]):
        step = "npm " + " ".join(command[1:])
        print(f"Running {step} in {spec.source}/", file=sys.stderr)
        # npm's output goes to stderr; stdout lists only the built bundles.
        result = subprocess.run(command, cwd=source, env=environ, stdout=sys.stderr)
        if result.returncode != 0:
            raise SystemExit(f"Valve Oil UI build failed: {step} exited with {result.returncode}.")
    archive = VALVE_OIL_UI_BUILD / Path(spec.archive).name
    zip_directory(source / "dist", archive)
    sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    static_build.VALVE_OIL_UI.archive_files(archive, sha256)
    return archive, sha256


def zip_directory(directory: Path, archive: Path) -> None:
    """Zip a build output directory with sorted entries and fixed timestamps and permissions."""
    if directory.is_symlink() or not directory.is_dir():
        raise SystemExit(f"Build output is missing: {directory}")
    files = []
    for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise SystemExit(f"Build output contains a link or special file: {path}")
        if path.is_file():
            files.append(path)
    archive.parent.mkdir(parents=True, exist_ok=True)
    partial = archive.with_name(archive.name + ".partial")
    with zipfile.ZipFile(partial, "w") as output:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(directory).as_posix(), date_time=ZIP_TIMESTAMP)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, path.read_bytes())
    partial.replace(archive)


def pin_valve_oil_ui(package_yaml: Path, sha256: str) -> None:
    """Record the built archive's sha256 in the bundled manifest, which the installer verifies it against."""
    text = package_yaml.read_text(encoding="utf-8")
    pinned, count = re.subn(
        r"(?m)^(  valve_oil_ui:\n(?:    .*\n)*?    archive: .*\n)", lambda match: f"{match.group(1)}    sha256: {sha256}\n", text
    )
    if count != 1:
        raise ValueError("Could not pin valve_oil_ui.sha256 in the bundled package.yaml.")
    package_yaml.write_text(pinned, encoding="utf-8")
    if load_manifest(package_yaml).system_optimizations.valve_oil_ui.sha256 != sha256:
        raise ValueError("Bundled package.yaml does not pin the built Valve Oil UI archive.")


def fetch_moonraker_speedups(manifest) -> list[tuple[object, Path]]:
    """Return the pinned Moonraker speedup wheels, downloading each once and verifying it against the manifest."""
    fetched = []
    for wheel in manifest.system_optimizations.moonraker_speedups.wheels:
        cached = DOWNLOAD_CACHE / f"{wheel.sha256}-{Path(wheel.archive).name}"
        if not cached.is_file():
            print(f"Downloading {Path(wheel.archive).name} from {wheel.url}", file=sys.stderr)
            with urllib.request.urlopen(wheel.url, timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != wheel.sha256:
                raise ValueError(f"{wheel.url} does not match the pinned sha256.")
            cached.parent.mkdir(parents=True, exist_ok=True)
            partial = cached.with_name(cached.name + ".partial")
            partial.write_bytes(data)
            partial.replace(cached)
        fetched.append((wheel, cached))
    moonraker_speedups.wheel_files((cached, wheel.sha256) for wheel, cached in fetched)
    return fetched


def fetch_addon_files(manifest) -> list[tuple[object, Path]]:
    """Return every pinned add-on file, downloading each once and verifying it against the manifest."""
    if manifest.addons is None:
        return []
    artifacts = [manifest.addons.pip_wheel]
    for addon in manifest.addons.catalog:
        # A download add-on's source is fetched by the printer when it is installed.
        artifacts += [*(() if addon.download else (addon.source,)), *addon.wheels, *addon.sdists]
    fetched = []
    for artifact in artifacts:
        cached = DOWNLOAD_CACHE / f"{artifact.sha256}-{Path(artifact.archive).name}"
        if not cached.is_file() or hashlib.sha256(cached.read_bytes()).hexdigest() != artifact.sha256:
            with urllib.request.urlopen(artifact.url, timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise ValueError(f"{artifact.url} does not match the pinned sha256.")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
        fetched.append((artifact, cached))
    return fetched


def bundle_names(*, channel: str, package_version: str, build_id: str | None) -> tuple[str, str]:
    if channel == "release":
        stable = f"{BUNDLE_NAME_PREFIX}.tar.gz"
        versioned = f"{BUNDLE_NAME_PREFIX}-{package_version}.tar.gz"
        return stable, versioned
    trace = build_id or git_short_sha()
    stable = f"{BUNDLE_NAME_PREFIX}-dev.tar.gz"
    traceable = f"{BUNDLE_NAME_PREFIX}-dev-{trace}.tar.gz"
    return stable, traceable



def create_archive(bundle_root: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as tar:
        for file_path in sorted(item for item in bundle_root.rglob("*") if item.is_file()):
            relative = file_path.relative_to(bundle_root.parent)
            info = tar.gettarinfo(str(file_path), arcname=relative.as_posix())
            if not info.isreg():
                raise ValueError(f"Only regular files are allowed in bundle archive: {relative}")
            with file_path.open("rb") as handle:
                tar.addfile(info, handle)



def validate_archive(archive_path: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
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



def render_release_metadata(package_version: str, channel: str, build_id: str | None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = git_commit()
    lines = [
        f"package_version: \"{package_version}\"",
        f"channel: \"{channel}\"",
        f"git_commit: \"{commit}\"",
        f"git_short_sha: \"{commit[:7]}\"",
        f"build_timestamp: \"{timestamp}\"",
    ]
    if build_id:
        lines.append(f"build_id: \"{build_id}\"")
    return "\n".join(lines) + "\n"



def write_sha256(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    checksum_path = path.with_name(path.name + ".sha256")
    checksum_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")



def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()



def git_short_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()


if __name__ == "__main__":
    raise SystemExit(main())
