from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

import yaml

from . import safety
from .backup import (
    format_backup_display_timestamp,
    list_installer_backups,
    parse_installer_backup_archive,
    load_backup_snapshot,
    restore_backup_snapshot,
    snapshot_runtime_tree,
    describe_snapshot_difference,
    load_external_backup_entries,
    restore_external_backup_entries,
    requires_external_backup_manifest,
    UNKNOWN_FIRMWARE_TOKEN,
)
from .board_guard import critical_window, detached
from .firmware import detect_firmware, firmware_key, supported_printer
from .manifest import source_patch_variants_for_firmware
from .models import Manifest, RuntimePaths
from .path_safety import ensure_external_path_has_no_symlink_components
from .process_restart import read_printer_info, write_restart_marker
from .interaction import maybe_restart_pending_service
from .rollback import RollbackJournal
from .sudo import authenticate_sudo, run_sudo


CONFIRMATION_TOKEN = "RESTORE"
DebugFn = Callable[..., None]
KLIPPER_SERVICE = "klipper.service"
# Ctrl-C between stopping Klipper and starting it again would leave the printer without Klipper.
CTRL_C_REFUSED = "A backup is being restored, so Ctrl-C is ignored until Klipper runs again."


@dataclass(frozen=True)
class BackupSelection:
    path: Path
    label: str
    display_created_at: str


class RestoreHelperError(ValueError):
    pass


def run_restore_helper(
    paths: RuntimePaths,
    manifest: Manifest,
    *,
    stream: TextIO | None = None,
    input_stream: TextIO,
    backup_path: str | None = None,
    debug: DebugFn | None = None,
    reporter=None,
    urlopen=None,
    run=None,
    environ=None,
) -> int:
    run = run or subprocess.run
    environ = os.environ if environ is None else environ
    if reporter is None:
        from .reporter import PlainReporter

        reporter = PlainReporter(stream)
    debug = debug or getattr(reporter, "debug", _noop_debug)
    selection = _resolve_backup_selection(
        printer_data_root=paths.printer_data_root,
        install_label_prefix=manifest.backup.label_prefix,
        backup_path=backup_path,
        reporter=reporter,
        input_stream=input_stream,
        debug=debug,
    )
    if selection is None:
        return 0

    reporter.emit_restore_selection(label=selection.label, path=str(selection.path))
    reporter.emit_restore_warning(
        config_path=str(paths.printer_data_root / manifest.backup.source_directory)
    )
    reporter.line(f"Type {CONFIRMATION_TOKEN} to continue:")
    confirmation = input_stream.readline().strip()
    if confirmation != CONFIRMATION_TOKEN:
        reporter.line()
        reporter.line("Restore cancelled.")
        return 0
    reporter.line()

    debug(
        event="restore.selection.confirmed",
        backup_path=selection.path,
        source_directory=manifest.backup.source_directory,
    )
    parsed = parse_installer_backup_archive(selection.path, install_label_prefix=manifest.backup.label_prefix)
    backup_snapshot = load_backup_snapshot(
        backup_zip_path=selection.path,
        source_directory=manifest.backup.source_directory,
    )
    state_declares_sources, state_firmware = _snapshot_source_firmware(backup_snapshot)
    require_external = requires_external_backup_manifest(
        package_version=parsed.package_version if parsed is not None else None,
        state_declares_source_patches=state_declares_sources,
    )
    allowed = {patch.id: patch.destination for patch in manifest.install.source_patches}
    external_firmware, external = load_external_backup_entries(
        backup_zip_path=selection.path,
        allowed_entries=allowed,
        require_manifest=require_external,
    )
    restore_firmware = None
    if external:
        restore_firmware = _validate_external_restore_firmware(
            paths=paths,
            manifest=manifest,
            parsed=parsed,
            state_firmware=state_firmware,
            manifest_firmware=external_firmware,
        )
    restart_targets = tuple(
        (patch_id, destination, hashlib.sha256(value).hexdigest())
        for patch_id, destination, value, _ in external
    )
    _validate_external_restore_targets(
        paths=paths,
        manifest=manifest,
        external=external,
        firmware=restore_firmware,
    )
    # Restoring a backup may be what lets a halted Klipper start again.
    safety.ensure_printer_idle(
        paths.moonraker_url,
        allow_halted=True,
        **({"urlopen": urlopen} if urlopen is not None else {}),
    )
    process_id = None
    if restart_targets:
        process_id, _ = read_printer_info(
            paths.moonraker_url,
            **({"urlopen": urlopen} if urlopen is not None else {}),
        )

    # Klipper holds every saved variable in memory and rewrites the whole saved_variables.cfg on its next save, which
    # would undo the restored one; the restored files are written with Klipper stopped, and Klipper loads them as it
    # starts again.
    klipper_password = _klipper_stop_password(
        run=run, environ=environ, reporter=reporter, input_stream=input_stream
    )
    started = True
    with critical_window(reporter, CTRL_C_REFUSED) as reporter:
        session = detached(run)
        if klipper_password is not None:
            reporter.line("Stopping Klipper...")
            if run_sudo(["systemctl", "stop", KLIPPER_SERVICE], run=session, password=klipper_password,
                        timeout=120).returncode:
                # It may have stopped anyway; start it again rather than leave it in doubt.
                run_sudo(["systemctl", "start", KLIPPER_SERVICE], run=session, password=klipper_password,
                         timeout=120)
                raise RestoreHelperError("Could not stop Klipper. Nothing was restored.")
        try:
            _restore_files(
                paths=paths, manifest=manifest, selection=selection, backup_snapshot=backup_snapshot,
                external=external, restart_targets=restart_targets, process_id=process_id,
            )
        finally:
            if klipper_password is not None:
                reporter.line("Starting Klipper...")
                started = not run_sudo(["systemctl", "start", KLIPPER_SERVICE], run=session,
                                       password=klipper_password, timeout=120).returncode
                if not started:
                    reporter.line("Klipper could not be started. Start it with: sudo systemctl start klipper")
                    debug(event="restore.klipper_start_failed")
    if not started:
        raise RestoreHelperError(
            "The backup was restored, but Klipper could not be started. Start it with: sudo systemctl start klipper"
        )

    debug(
        event="restore.completed",
        backup_path=selection.path,
        restored_files=len(backup_snapshot),
    )
    if external:
        activated = maybe_restart_pending_service(
            paths=paths,
            allowed_entries=allowed,
            reporter=reporter,
            input_stream=input_stream,
            **({"urlopen": urlopen} if urlopen is not None else {}),
        )
        if not activated:
            reporter.line("Source restoration completed; Klipper activation remains pending.")
    elif klipper_password is not None:
        reporter.line("Klipper was started again with the restored configuration.")
    reporter.emit_restore_complete(
        verified_path=str(paths.printer_data_root / manifest.backup.source_directory)
    )
    return 0


def _klipper_stop_password(*, run, environ, reporter, input_stream) -> str | None:
    """The sudo password to stop Klipper with, or None when its service isn't running. Asked before anything changes."""
    result = run(["systemctl", "show", KLIPPER_SERVICE, "--property=ActiveState"],
                 capture_output=True, text=True, timeout=10)
    values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    state = values.get("ActiveState", "")
    if result.returncode or not state:
        raise RestoreHelperError("Could not read whether Klipper is running. Nothing was restored.")
    if state in {"inactive", "failed"}:
        return None
    return authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)


def _restore_files(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    selection: BackupSelection,
    backup_snapshot,
    external,
    restart_targets,
    process_id,
) -> None:
    journal = RollbackJournal(
        paths.recovery_sentinel_path,
        printer_data_root=paths.printer_data_root,
        source_directory=manifest.backup.source_directory,
    )
    journal.track_tree(paths.printer_data_root / manifest.backup.source_directory)
    journal.track_file(paths.restart_marker_path)
    for _, destination, _, _ in external:
        journal.track_file(paths.managed_klipper_root / destination)
    try:
        journal.note_write()
        restored_snapshot = restore_backup_snapshot(
            backup_zip_path=selection.path,
            printer_data_root=paths.printer_data_root,
            source_directory=manifest.backup.source_directory,
        )
        runtime_snapshot = snapshot_runtime_tree(
            printer_data_root=paths.printer_data_root,
            source_directory=manifest.backup.source_directory,
        )
        if runtime_snapshot != backup_snapshot or restored_snapshot != backup_snapshot:
            raise RestoreHelperError(
                "Restore verification failed. "
                + describe_snapshot_difference(runtime_snapshot, backup_snapshot)
            )
        if restart_targets:
            assert process_id is not None
            journal.note_write()
            write_restart_marker(
                paths,
                restart_targets,
                operation="restore",
                process_id=process_id,
            )
            journal.note_write()
            restore_external_backup_entries(
                entries=external, destination_root=paths.managed_klipper_root
            )
    except BaseException as exc:
        journal.rollback_or_raise(
            exc, backup_label=selection.label, backup_zip_path=selection.path
        )
        raise


def _validate_external_restore_firmware(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    parsed,
    state_firmware: str | None,
    manifest_firmware: str | None,
) -> str:
    detected = detect_firmware(paths.firmware_manifest_path)
    current_firmware = firmware_key(manifest, detected)
    if supported_printer(manifest, detected) is None:
        raise RestoreHelperError(
            f"External source restore does not support firmware {current_firmware}."
        )

    label_firmware = None
    if parsed is not None and parsed.firmware_token != UNKNOWN_FIRMWARE_TOKEN:
        label_firmware = parsed.firmware_token
    provenance = tuple(
        value
        for value in (manifest_firmware, label_firmware, state_firmware)
        if value is not None
    )
    if not provenance:
        raise RestoreHelperError(
            "External source backup has no firmware provenance."
        )
    if any(value != current_firmware for value in provenance):
        raise RestoreHelperError(
            "External source backup firmware does not match the printer firmware."
        )
    return current_firmware


def _validate_external_restore_targets(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    external: tuple[tuple[str, str, bytes, int], ...],
    firmware: str | None,
) -> None:
    if not external:
        return
    assert firmware is not None
    patches = {patch.id: patch for patch in manifest.install.source_patches}
    for patch_id, destination, archived_bytes, _ in external:
        target = paths.managed_klipper_root / destination
        ensure_external_path_has_no_symlink_components(
            root=paths.managed_klipper_root, target=target
        )
        if target.is_symlink() or not target.is_file():
            raise RestoreHelperError(
                f"External restore target is not a regular file: {destination}"
            )
        variants = source_patch_variants_for_firmware(
            patches[patch_id], firmware
        )
        archived_hash = hashlib.sha256(archived_bytes).hexdigest()
        accepted_hashes = {
            value
            for variant in variants
            for value in (variant.expected_sha256, variant.desired_sha256)
        }
        if archived_hash not in accepted_hashes:
            raise RestoreHelperError(
                f"External backup source is not valid for firmware {firmware}: {destination}"
            )
        if hashlib.sha256(target.read_bytes()).hexdigest() not in (
            accepted_hashes | {archived_hash}
        ):
            raise RestoreHelperError(
                f"External restore target has drifted: {destination}"
            )


def _snapshot_source_firmware(snapshot: dict[str, bytes]) -> tuple[bool, str | None]:
    state = snapshot.get("config/vo_state.yaml")
    if state is None:
        return False, None
    try:
        raw = yaml.safe_load(state)
    except yaml.YAMLError as exc:
        raise RestoreHelperError("Archived installed state is malformed.") from exc
    if not isinstance(raw, dict):
        raise RestoreHelperError("Archived installed state is malformed.")
    if "source_patches" not in raw:
        return False, None
    runtime = raw.get("runtime")
    firmware = runtime.get("firmware") if isinstance(runtime, dict) else None
    if not isinstance(firmware, str) or not firmware:
        raise RestoreHelperError(
            "Archived source-aware state has no valid firmware provenance."
        )
    return True, firmware


def _resolve_backup_selection(
    *,
    printer_data_root: Path,
    install_label_prefix: str,
    backup_path: str | None,
    reporter,
    input_stream: TextIO,
    debug: DebugFn,
) -> BackupSelection | None:
    if backup_path is not None:
        resolved_path = _resolve_backup_path(backup_path)
        if not resolved_path.exists() or not resolved_path.is_file():
            raise RestoreHelperError(f"Backup zip was not found: {resolved_path}")
        selection = _selection_from_path(
            resolved_path,
            install_label_prefix=install_label_prefix,
        )
        debug(event="restore.selection.direct", backup_path=selection.path)
        return selection

    backups = list_installer_backups(
        printer_data_root,
        install_label_prefix=install_label_prefix,
    )
    if not backups:
        reporter.line(f"No installer backups were found under {printer_data_root}.")
        return None

    ordered_backups = tuple(reversed(backups))
    reporter.emit_backup_choices(
        tuple(
            (index, archive.display_created_at, archive.label, str(archive.path))
            for index, archive in enumerate(ordered_backups, start=1)
        )
    )

    while True:
        reporter.line("Select a backup number to restore, or q to cancel:")
        raw_choice = input_stream.readline().strip()
        if not raw_choice:
            reporter.line()
            continue
        if raw_choice.lower() in {"q", "quit"}:
            reporter.line()
            reporter.line("Restore cancelled.")
            return None
        try:
            selection_index = int(raw_choice)
        except ValueError:
            reporter.line()
            reporter.line("Enter a listed backup number or q.")
            continue
        if 1 <= selection_index <= len(ordered_backups):
            reporter.line()
            archive = ordered_backups[selection_index - 1]
            selection = BackupSelection(
                path=archive.path,
                label=archive.label,
                display_created_at=archive.display_created_at,
            )
            debug(event="restore.selection.menu", backup_path=selection.path)
            return selection
        reporter.line()
        reporter.line("Enter a listed backup number or q.")


def _selection_from_path(path: Path, *, install_label_prefix: str) -> BackupSelection:
    parsed = parse_installer_backup_archive(path, install_label_prefix=install_label_prefix)
    if parsed is not None:
        return BackupSelection(
            path=path,
            label=parsed.label,
            display_created_at=parsed.display_created_at,
        )
    created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return BackupSelection(
        path=path,
        label=path.stem,
        display_created_at=format_backup_display_timestamp(created_at),
    )


def _resolve_backup_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _noop_debug(**kwargs) -> None:
    return
