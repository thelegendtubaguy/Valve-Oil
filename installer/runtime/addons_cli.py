from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import addons
from .errors import InstallerError, PrinterStateError
from .locking import acquire
from .manifest import load_manifest
from .models import RuntimePaths
from .reporter import create_reporter
from .safety import ensure_no_recovery_sentinel, query_printer_state
from .sudo import SudoError, authenticate_sudo

SYSTEM_ROOT_ENV = "TLTG_OPTIMIZED_SYSTEM_ROOT"
DEFAULT_PRINTER_DATA_ROOT = Path("/home/qidi/printer_data")


def main(
    argv: list[str],
    *,
    paths: RuntimePaths,
    stream=None,
    input_stream=None,
    environ: dict[str, str],
    run=subprocess.run,
) -> int:
    parser = argparse.ArgumentParser(prog="addons.sh", description="Install, update, or remove Valve Oil add-ons.")
    parser.add_argument("action", choices=["list", "install", "update", "remove", "relink"])
    parser.add_argument("addon", nargs="?", help="Add-on id, such as octoeverywhere.")
    parser.add_argument("--server", help="relink: the server to link to, such as https://app.obico.io.")
    parser.add_argument("--json", action="store_true", help="list: print JSON.")
    parser.add_argument("--plain", action="store_true", help="Use plain text output.")
    parser.add_argument("--yes", action="store_true", help="Don't ask for a sudo password; fail instead.")
    parser.add_argument("--debug", action="store_true", help="Print debug events and tracebacks on failure.")
    args = parser.parse_args(argv)
    if (args.action == "list") == (args.addon is not None):
        parser.error("list takes no add-on; install, update, remove, and relink need one.")
    if args.server is not None and args.action != "relink":
        parser.error("--server is only for relink.")

    out = stream or sys.stdout
    manifest = load_manifest(paths.installer_root / "package.yaml")
    root = Path(environ.get(SYSTEM_ROOT_ENV, "/"))
    if args.action == "list":
        entries = addons.list_addons(manifest.addons, root=root, run=run)
        if args.json:
            print(json.dumps({"addons": entries}), file=out)
        else:
            for entry in entries:
                installed = entry["installed_version"] or "not installed"
                print(f"{entry['id']}: {entry['name']} {entry['available_version']} ({entry['state']}; installed: {installed})", file=out)
        return 0

    reporter = create_reporter(stream, prefer_plain=args.plain, bundle_root=paths.bundle_root, environ=environ, debug=args.debug)
    try:
        if args.addon not in addons.DEFINITIONS or (args.action != "remove" and (manifest.addons is None or manifest.addons.find(args.addon) is None)):
            raise addons.AddonError(f"Unknown add-on: {args.addon}")
        with acquire(paths.lock_path):
            ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
            if args.action != "remove":
                _ensure_not_printing(paths.moonraker_url)
            sudo_password = None
            if root == Path("/"):
                try:
                    sudo_password = authenticate_sudo(
                        run=run,
                        environ=environ,
                        reporter=reporter,
                        input_stream=None if args.yes else (input_stream or sys.stdin),
                    )
                except SudoError as exc:
                    raise addons.AddonError(
                        "sudo didn't accept the printer's default password. Run "
                        f"~/tltg-optimized-macros/addons.sh {args.action} {args.addon} over SSH and enter it there."
                    ) from exc
            if args.action == "relink":
                addons.relink_addon(args.addon, server=args.server, root=root, sudo_password=sudo_password, run=run, status=reporter.status)
            elif args.action == "remove":
                if not addons.remove_addon(args.addon, root=root, sudo_password=sudo_password, run=run, status=reporter.status):
                    reporter.status(f"{addons.DEFINITIONS[args.addon].name} is not installed.")
            else:
                if args.action == "update" and addons.read_marker(args.addon, root=root) is None:
                    raise addons.AddonError(f"{addons.DEFINITIONS[args.addon].name} is not installed.")
                addons.install_addon(
                    args.addon,
                    spec=manifest.addons,
                    installer_root=paths.installer_root,
                    root=root,
                    sudo_password=sudo_password,
                    run=run,
                    status=reporter.status,
                )
    except (InstallerError, OSError) as exc:
        reporter.emit_error(exc)
        return 1
    return 0


def _ensure_not_printing(moonraker_url: str) -> None:
    # Building an add-on's environment takes the CPU and memory a print needs. A stopped Moonraker or Klipper can't be
    # printing, so an unknown state doesn't block.
    try:
        state = query_printer_state(moonraker_url)
    except PrinterStateError:
        return
    if state in {"printing", "paused"}:
        raise addons.AddonError("Add-ons can be installed, updated, or removed only while the printer is not printing.")


def maybe_update_installed_addons(*, paths: RuntimePaths, manifest, reporter, input_stream, environ: dict[str, str], run) -> None:
    """After install or update, bring installed add-ons to this package's pinned builds; failures only warn."""
    root = Path(environ.get(SYSTEM_ROOT_ENV, "/"))
    if not _host_allowed(paths, environ) or not addons.installed_addon_ids(root=root):
        return
    try:
        sudo_password = None if root != Path("/") else authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
    except SudoError:
        reporter.line("Warning: add-ons were not updated because sudo failed; run ~/tltg-optimized-macros/addons.sh update <id>.")
        return
    failures = addons.update_installed_addons(
        spec=manifest.addons,
        installer_root=paths.installer_root,
        root=root,
        sudo_password=sudo_password,
        run=run,
        status=reporter.status,
    )
    for failure in failures:
        reporter.line(f"Warning: add-on not updated. {failure}")


def maybe_remove_addons(*, paths: RuntimePaths, reporter, input_stream, environ: dict[str, str], run) -> None:
    """Uninstall removes every package-owned add-on; failures only warn."""
    root = Path(environ.get(SYSTEM_ROOT_ENV, "/"))
    if not _host_allowed(paths, environ):
        return
    if not addons.installed_addon_ids(root=root) and not any(addons.unit_owned(d, root=root) for d in addons.DEFINITIONS.values()):
        return
    try:
        sudo_password = None if root != Path("/") else authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
    except SudoError:
        reporter.line("Warning: add-ons were not removed because sudo failed; run ~/tltg-optimized-macros/addons.sh remove <id> first.")
        return
    for failure in addons.remove_all_addons(root=root, sudo_password=sudo_password, run=run, status=reporter.status):
        reporter.line(f"Warning: add-on not removed. {failure}")


def _host_allowed(paths: RuntimePaths, environ: dict[str, str]) -> bool:
    # As for the host optimizations: an install against another printer_data, such as a test or dev run, leaves the
    # host's add-ons alone unless a system root is given.
    return SYSTEM_ROOT_ENV in environ or paths.printer_data_root == DEFAULT_PRINTER_DATA_ROOT
