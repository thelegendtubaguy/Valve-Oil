from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import replace

from . import earlier_names
from .addons_cli import maybe_update_installed_addons
from . import boot_logo, boot_path, errors, klipper_cfg, klipper_tree, messages, patches, progress, safety, update_window
from .auto_update import (
    LOCK_HELD_ENV,
    adopt_legacy_enrollment,
    apply_auto_update_answer,
    settle_installed_bundle,
)
from .board_firmware import UNADMITTED_STATES, existing_installation
from .box_enablement import (
    maybe_initialize_box_motion,
    maybe_enable_box,
    maybe_initialize_filament_retention,
    maybe_write_required_tool_slot_variables,
    saved_variable_verify_marker_path,
    verify_pending_saved_variable_expectations,
    write_saved_variable_verify_marker,
)
from .backup import (
    build_install_backup_label,
    create_config_backup,
    format_installed_at,
    list_installer_backups,
    prune_installer_backups,
    utc_now,
)
from .ensure_lines import ensure_line_after
from .compatibility import (
    CompatibilityValidationError,
    load_supported_upgrade_sources,
    validate_installed_state_identity,
    validate_manifest_compatibility,
    validate_patch_ledger,
)
from .config_transaction import FileChangePlan, apply_file_change_plan
from .errors import (
    HaltedKlipperDidNotStartError,
    KlipperNotReadyError,
    NewInstallKlipperNotReadyError,
    PreviousPackageValidationError,
    PrinterStateError,
)
from .firmware import detect_supported_firmware
from .host_reboot import arm_auto_update_reboot_followup, reboot_pending
from .interaction import (
    maybe_restart_klipper,
    maybe_restart_pending_service_if_idle,
)
from .legacy_manual_install import clear_install_started, mark_install_started, maybe_reset_legacy_manual_install
from .manifest import active_patch_entries
from .mirror import (
    collect_source_hashes,
    detect_install_managed_tree_drift,
    managed_tree_source_roots,
    mirror_tree,
)
from .models import (
    IncludeLineIntent,
    InstallPlan,
    InstalledState,
    InstallResult,
    ManagedTreeFileRecord,
    ManagedTreeIntent,
    ManagedTreeState,
    Manifest,
    PatchLedgerEntry,
    RuntimePaths,
    StateFileIntent,
    SystemOptimizationCliOptions,
)
from .path_safety import ensure_install_paths_safe
from .source_patches import (
    SourcePatchError,
    changed_extra_module_sha256,
    classify_install_source_patch,
    deploy_source_patch,
    install_extra_module,
    validate_source_state,
)
from .process_restart import (
    ProcessRestartError,
    firmware_restart_url,
    read_printer_info,
    request_klipper_restart,
    restart_entries,
    restart_klipper_service,
    restart_pending,
    write_restart_marker,
)
from .postflight import verify_install_postflight
from .preflight import run_install_config_preflight, run_install_environment_preflight
from .rollback import RollbackJournal
from .state_file import StateValidationError, load_installed_state, write_installed_state
from .system_optimizations import (
    discard_legacy_system_ledger_migration,
    finalize_legacy_system_ledger_migration,
    load_carried_system_ledger,
    maybe_apply_system_optimizations,
    maybe_emit_system_dry_run,
    migrate_legacy_system_ledger,
    recover_pending_legacy_system_ledger_migration,
    system_ledger_needs_migration,
    validate_legacy_system_ledger,
    validate_system_ledger,
)


# A new install waits this long for a Klipper that is still starting, as after the touchscreen app restarts it.
NEW_INSTALL_READY_WAIT_SECONDS = 60.0


def ensure_klipper_ready_for_new_install(paths: RuntimePaths, *, urlopen=urllib.request.urlopen,
                                         wait_seconds: float = NEW_INSTALL_READY_WAIT_SECONDS) -> None:
    """Refuse a new install, before anything changes, unless Klipper is ready. Valve Oil isn't installed yet, so a
    Klipper error comes from the printer itself, and installing over it can only hide that."""
    try:
        safety.ensure_klipper_ready(paths.moonraker_url, urlopen=urlopen, wait_seconds=wait_seconds)
    except KlipperNotReadyError as exc:
        raise NewInstallKlipperNotReadyError(exc.message) from exc
    except PrinterStateError as exc:
        raise NewInstallKlipperNotReadyError("Klipper's state could not be read") from exc


def block_qidi_package_updates(admission) -> bool | None:
    """The block_qidi_updates value for system optimizations, from this printer's boards (board_firmware.BoardAdmission).

    True blocks QIDI's package updates once the THR and the mainboard have been read: whether this release admits them
    or a read found one it doesn't, which keeps a QIDI update from taking the printer to firmware no release supports
    while the operator's support issue is open. None keeps the touchscreen app as it is (blocked stays blocked;
    otherwise not blocked): nothing is known, because a board wasn't read this run and has no earlier record, or there
    is no admission record at all. Nothing switches QIDI's updater back on but uninstall.
    """
    if admission is None:
        return None
    boards = [status for status in admission.boards if status.kind != "box"]
    if all(status.admitted or status.state in UNADMITTED_STATES for status in boards):
        return True
    return None


def _replace_legacy_system_ledger(state, ledger):
    return replace(state, system_ledger=ledger)


def run_install(
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    *,
    dry_run: bool = False,
    input_stream=None,
    urlopen=urllib.request.urlopen,
    disk_usage=shutil.disk_usage,
    environ: dict[str, str] | None = None,
    system_options: SystemOptimizationCliOptions | None = None,
    block_qidi_updates: bool | None = True,
    auto_updates: bool | None = None,
    run=None,
) -> InstallResult:
    """block_qidi_updates: the caller's block_qidi_package_updates() decision for this printer's boards. auto_updates:
    the operator's answer about automatic updates (install_flow.py), applied once the install is done; None leaves them
    as they are.

    Nothing here asks a question: the caller asked them all before the run (install_flow.py). input_stream is kept for
    the legacy manual-install reset and sudo, which take it from the terminal only when the run has one."""
    run = subprocess.run if run is None else run
    env = {} if environ is None else environ
    system_options = system_options or SystemOptimizationCliOptions()
    reporter.debug(
        event="install.start",
        dry_run=dry_run,
        package_version=manifest.package.version,
    )

    progress.step(progress.CHECK, reporter)
    reporter.status(messages.CHECKING_FIRMWARE_VERSION)
    printer, detected_firmware = detect_supported_firmware(paths.firmware_manifest_path, manifest)
    reporter.debug(
        event="install.firmware.detected",
        printer=printer.id,
        firmware_version=detected_firmware,
        manifest_path=paths.firmware_manifest_path,
    )

    reporter.status(messages.CHECKING_PACKAGE_VERSION)
    ensure_install_paths_safe(paths=paths, manifest=manifest)
    state_path = paths.printer_data_root / manifest.state_file
    if not dry_run:
        recover_pending_legacy_system_ledger_migration(paths, state_path)
    prior_state = None
    compatibility = load_supported_upgrade_sources(
        paths.installer_root / "supported_upgrade_sources.yaml"
    )
    validate_manifest_compatibility(manifest, compatibility)
    if state_path.exists():
        try:
            prior_state = load_installed_state(state_path)
        except StateValidationError as exc:
            raise PreviousPackageValidationError() from exc
        try:
            validate_installed_state_identity(prior_state, manifest)
            validate_patch_ledger(prior_state, compatibility)
            if system_ledger_needs_migration(prior_state.system_ledger):
                validate_legacy_system_ledger(prior_state.system_ledger, manifest=manifest, paths=paths, environ=env)
            else:
                validate_system_ledger(prior_state.system_ledger, manifest=manifest, paths=paths)
        except (CompatibilityValidationError, StateValidationError) as exc:
            raise PreviousPackageValidationError() from exc
        reporter.debug(
            event="install.prior_state.loaded",
            state_path=state_path,
            package_version=prior_state.package_version,
        )
    else:
        reporter.debug(event="install.prior_state.missing", state_path=state_path)
    # Before anything else changes, the printer must be able to start again (boot_path.py).
    boot_path.protect(paths, reporter=reporter, environ=env, dry_run=dry_run, run=run, input_stream=input_stream)
    # Only an install over an existing Valve Oil may repair a Klipper halted on an error (_execute_install).
    new_install = prior_state is None and not existing_installation(paths, manifest)
    if new_install:
        ensure_klipper_ready_for_new_install(paths, urlopen=urlopen)

    if saved_variable_verify_marker_path(paths).exists() and safety.klipper_halted(
        paths.moonraker_url, urlopen=urlopen
    ):
        # Kept until Klipper runs again; the install may be what lets it start.
        reporter.line(messages.SAVED_VARIABLE_CHECK_DEFERRED_KLIPPER_HALTED)
    else:
        verify_pending_saved_variable_expectations(paths, clear=not dry_run, urlopen=urlopen,
                                                   earlier_run_reporter=reporter)

    reporter.status(messages.PERFORMING_PREFLIGHT_CHECKS)
    run_install_environment_preflight(
        paths=paths,
        manifest=manifest,
        detected_firmware=detected_firmware,
        reporter=reporter,
        urlopen=urlopen,
        disk_usage=disk_usage,
        allow_halted=not new_install,
    )
    maybe_reset_legacy_manual_install(
        paths=paths,
        manifest=manifest,
        reporter=reporter,
        input_stream=input_stream,
        environ=env,
        detected_firmware=detected_firmware,
        dry_run=dry_run,
        run=run,
    )
    run_install_config_preflight(
        paths=paths,
        manifest=manifest,
        reporter=reporter,
        detected_firmware=detected_firmware,
        prior_state=prior_state,
    )
    if prior_state is not None:
        try:
            validate_source_state(
                prior_state,
                manifest.install.source_patches,
                upgrade_sources=compatibility,
                expected_firmware=detected_firmware,
            )
        except (CompatibilityValidationError, SourcePatchError) as exc:
            raise PreviousPackageValidationError() from exc
    source_results = tuple(
        classify_install_source_patch(paths=paths, patch=patch, firmware=detected_firmware, prior_state=prior_state,
                                     upgrade_sources=compatibility)
        for patch in manifest.install.source_patches
    )
    started_at = utc_now()
    backup_label = build_install_backup_label(
        label_prefix=manifest.backup.label_prefix,
        firmware_version=detected_firmware,
        package_version=manifest.package.version,
        moment=started_at,
    )

    plan = build_install_plan(
        paths=paths,
        manifest=manifest,
        detected_firmware=detected_firmware,
        prior_state=prior_state,
        backup_label=backup_label,
    )
    config_changes = plan.file_changes
    reporter.debug(
        event="install.plan.built",
        patches=len(plan.patch_results),
        managed_tree_drift=len(plan.managed_tree_drift),
        managed_tree_files=len(plan.managed_tree_files),
    )

    reporter.status(messages.CREATING_BACKUP)
    backup_zip_path = None
    if not dry_run:
        progress.step(progress.BACKUP, reporter)
        existing_backups = list_installer_backups(
            paths.printer_data_root,
            install_label_prefix=manifest.backup.label_prefix,
        )
        reporter.debug(
            event="install.backups.detected",
            existing_backups=len(existing_backups),
            backup_root=paths.backup_root,
        )
        backup_zip_path = create_config_backup(
            printer_data_root=paths.printer_data_root,
            source_directory=manifest.backup.source_directory,
            backup_label=backup_label,
            external_files=tuple(
                (patch.id, patch.destination, paths.managed_klipper_root / patch.destination)
                for patch in manifest.install.source_patches
            ),
            external_firmware=detected_firmware,
        )
        if prior_state is None:
            mark_install_started(paths, manifest.package.version)
        pruned_backups = prune_installer_backups(
            paths.printer_data_root,
            install_label_prefix=manifest.backup.label_prefix,
            keep_path=backup_zip_path,
        )
        reporter.debug(
            event="install.backup.created",
            backup_label=backup_label,
            backup_zip_path=backup_zip_path,
            pruned_backups=len(pruned_backups),
        )
        if pruned_backups:
            reporter.debug(
                event="install.backup.retention.pruned",
                removed=", ".join(str(path) for path in pruned_backups),
            )

    reporter.status(messages.INSTALLING)
    if dry_run:
        _emit_install_dry_run_counters(reporter, manifest, detected_firmware)
        result = InstallResult(
            patch_results=plan.patch_results,
            managed_tree_drift=plan.managed_tree_drift,
            backup_label=plan.backup_label,
            backup_zip_path=None,
            dry_run=True,
        )
        reporter.emit_install_dry_run(plan=plan)
        maybe_emit_system_dry_run(
            paths=paths,
            manifest=manifest,
            reporter=reporter,
            prior_state=prior_state,
            cli_options=system_options,
            environ=env,
            block_qidi_updates=block_qidi_updates,
            run=run,
        )
        reporter.line(boot_logo.dry_run_line())
        reporter.debug(event="install.complete", dry_run=True, backup_label=plan.backup_label)
        return result

    held = _hold_valve_oil_klipper(paths=paths, reporter=reporter, urlopen=urlopen)
    try:
        result = _execute_install(
            paths=paths,
            manifest=manifest,
            reporter=reporter,
            detected_firmware=detected_firmware,
            prior_state=prior_state,
            new_install=new_install,
            started_at=started_at,
            plan=plan,
            backup_zip_path=backup_zip_path,
            input_stream=input_stream,
            urlopen=urlopen,
            environ=env,
            system_options=system_options,
            source_results=source_results,
            config_changes=config_changes,
            block_qidi_updates=block_qidi_updates,
            auto_updates=auto_updates,
            run=run,
        )
    finally:
        if held and klipper_tree.release_hold(paths.printer_data_root):
            # The next start is Valve Oil's Klipper again, with what this run installed.
            try:
                request_klipper_restart(paths, urlopen=urlopen)
            except ProcessRestartError:
                pass
    return result


def _hold_valve_oil_klipper(*, paths, reporter, urlopen, sleep=time.sleep, attempts: int = 60) -> bool:
    """Start QIDI's Klipper for this run when Valve Oil's Klipper is running but not ready.

    The run needs a ready Klipper for saved variables, and may be what repairs Valve Oil's Klipper. The launcher starts
    QIDI's Klipper while the hold exists; the caller drops it when the run ends."""
    if klipper_tree.running_tree(paths.printer_data_root) != "valve_oil":
        return False
    try:
        pid, state = read_printer_info(paths.moonraker_url, urlopen=urlopen)
    except ProcessRestartError:
        return False
    if state == "ready":
        return False
    klipper_tree.hold(paths.printer_data_root)
    reporter.line(messages.VALVE_OIL_KLIPPER_HELD)

    def wait(done) -> str | None:
        for _ in range(attempts):
            sleep(1)
            try:
                info = read_printer_info(paths.moonraker_url, urlopen=urlopen)
            except ProcessRestartError:
                continue
            if done(*info):
                return info[1]
        return None

    try:
        request_klipper_restart(paths, urlopen=urlopen)
    except ProcessRestartError:
        return True
    state = wait(lambda new_pid, new_state: new_pid != pid and new_state in {"ready", "error", "shutdown"})
    if state in {"error", "shutdown"}:
        # A board left shut down by the stopped Klipper stays so across a service restart (the Smart 5's mainboard
        # does), so QIDI's Klipper comes back with an error until a firmware restart.
        try:
            _request_firmware_restart(paths, urlopen=urlopen)
        except ProcessRestartError:
            return True
        wait(lambda _pid, new_state: new_state == "ready")
    return True


def _request_firmware_restart(paths, *, urlopen) -> None:
    request = urllib.request.Request(firmware_restart_url(paths.moonraker_url), data=b"", method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise ProcessRestartError("Could not request a firmware restart.") from exc



def build_install_plan(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    detected_firmware: str,
    prior_state: InstalledState | None,
    backup_label: str,
) -> InstallPlan:
    source_hashes = _source_hashes(paths=paths, manifest=manifest, detected_firmware=detected_firmware)
    managed_tree_root = paths.printer_data_root / manifest.managed_tree.destination
    managed_tree_drift = detect_install_managed_tree_drift(
        destination_root=managed_tree_root,
        printer_data_root=paths.printer_data_root,
        prior_state=prior_state,
        source_hashes=source_hashes,
    )
    patch_results = _collect_install_patch_results(
        paths=paths,
        manifest=manifest,
        detected_firmware=detected_firmware,
        prior_state=prior_state,
    )
    include_line_intents = _collect_install_include_line_intents(paths=paths, manifest=manifest)
    managed_tree_intent = ManagedTreeIntent(
        id=manifest.managed_tree.id,
        source=manifest.managed_tree.source,
        destination=manifest.managed_tree.destination,
        action="mirror",
    )
    managed_tree_files = tuple(
        ManagedTreeFileRecord(path=path, sha256=sha)
        for path, sha in sorted(source_hashes.items())
    )
    return InstallPlan(
        backup_label=backup_label,
        file_changes=_build_install_file_changes(
            paths=paths, manifest=manifest, patch_results=patch_results
        ),
        managed_tree_intent=managed_tree_intent,
        include_line_intents=include_line_intents,
        patch_results=patch_results,
        managed_tree_drift=managed_tree_drift,
        managed_tree_files=managed_tree_files,
        state_file_intent=StateFileIntent(path=manifest.state_file, action="write"),
    )



def _note_outcome(exc: BaseException, outcome: str) -> None:
    try:
        exc.install_outcome = outcome
    except AttributeError:
        pass


def _execute_install(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    reporter,
    detected_firmware: str,
    prior_state: InstalledState | None,
    new_install: bool = False,
    started_at,
    plan: InstallPlan,
    backup_zip_path,
    input_stream,
    urlopen,
    environ: dict[str, str],
    system_options: SystemOptimizationCliOptions,
    source_results=(),
    config_changes=(),
    block_qidi_updates: bool | None = True,
    auto_updates: bool | None = None,
    run=subprocess.run,
) -> InstallResult:
    progress.step(progress.CONFIGURATION, reporter)
    state_path = paths.printer_data_root / manifest.state_file
    journal = RollbackJournal(
        paths.recovery_sentinel_path,
        printer_data_root=paths.printer_data_root,
        source_directory=manifest.backup.source_directory,
    )
    migrated_paths: list[Path] = []
    migration_committed = False
    patch_results = list(plan.patch_results)
    active_patches = _active_install_patches(manifest, detected_firmware)
    active_section_patches = _active_install_section_patches(manifest, detected_firmware)
    install_counters = _install_counters_template(manifest, detected_firmware)
    saved_variables_written: dict[str, str] = {}
    try:
        if prior_state is not None and system_ledger_needs_migration(prior_state.system_ledger):
            prior_state = _replace_legacy_system_ledger(
                prior_state,
                migrate_legacy_system_ledger(
                    prior_state.system_ledger,
                    manifest=manifest,
                    paths=paths,
                    environ=environ,
                    created_paths=migrated_paths,
                ),
            )
        touched_files = {state_path}
        touched_files.update(paths.printer_data_root / spec.file for spec in manifest.install.ensure_lines)
        touched_files.update(paths.printer_data_root / patch.file for patch in active_patches)
        touched_files.update(paths.printer_data_root / patch.file for patch in active_section_patches)
        for touched in touched_files:
            journal.track_file(touched)
        journal.track_tree(paths.printer_data_root / manifest.managed_tree.destination)
        for result in source_results:
            journal.track_file(paths.managed_klipper_root / result.destination)
        reporter.debug(
            event="install.rollback.tracking",
            tracked_files=len(touched_files),
            tracked_trees=1,
        )

        # Saved variables go through Klipper. A Klipper halted on an error, on a printer that has Valve Oil, may be
        # halted by Valve Oil's own files: it gets the Klipper files first and a restart, before anything else.
        klipper_halted = safety.klipper_halted(paths.moonraker_url, urlopen=urlopen)
        if klipper_halted and new_install:
            raise NewInstallKlipperNotReadyError(messages.klipper_not_ready("error"))
        if klipper_halted:
            reporter.line(messages.INSTALLING_WHILE_KLIPPER_HALTED)
        else:
            _update_saved_variables(
                paths=paths,
                reporter=reporter,
                new_install=prior_state is None,
                journal=journal,
                written=saved_variables_written,
                urlopen=urlopen,
            )

        for directory in manifest.install.ensure_directories:
            target = paths.printer_data_root / directory
            if not target.exists():
                journal.note_write()
                target.mkdir(parents=True, exist_ok=True)
            install_counters["ensure_directories"][0] += 1
        reporter.emit_install_counters(**_freeze_counters(install_counters))

        source_changes = tuple(result for result in source_results if result.classification != "noop_desired")
        # Klipper keeps imported Python for the life of its process, so changed sources and
        # modules run only after the Klipper service restarts.
        restart_targets = tuple(
            (entry.id, entry.destination, entry.desired_sha256)
            for result in source_changes
            if (entry := result.original) is not None
        ) + tuple(
            (module.id, module.destination, sha256)
            for module in manifest.install.extra_modules
            if (sha256 := changed_extra_module_sha256(paths=paths, module=module)) is not None
        )
        if restart_targets:
            process_id, _ = read_printer_info(paths.moonraker_url, urlopen=urlopen)
            journal.track_file(paths.restart_marker_path)
            journal.note_write()
            write_restart_marker(
                paths,
                restart_targets,
                operation="install",
                process_id=process_id,
            )
        for patch, result in zip(manifest.install.source_patches, source_results):
            if result.classification != "noop_desired":
                journal.note_write()
                deploy_source_patch(paths=paths, patch=patch, firmware=detected_firmware, result=result)

        for module in manifest.install.extra_modules:
            install_extra_module(
                paths=paths, module=module, journal=journal, reporter=reporter
            )
            install_counters["extra_modules"][0] += 1

        mirror_tree(
            source_roots=managed_tree_source_roots(paths, manifest, detected_firmware),
            destination_root=paths.printer_data_root / manifest.managed_tree.destination,
            journal=journal,
            required_files=manifest.managed_tree.required_files,
        )
        install_counters["managed_trees"][0] = 1
        # The same modules under the names releases before 26.11.22.3 gave them, which no config section loads now
        # that the macros above are in place.
        for removed in earlier_names.remove_earlier_klipper_modules(
            paths, [module.destination for module in manifest.install.extra_modules], journal=journal
        ):
            reporter.debug(event="install.earlier_module.removed", path=removed)
        reporter.emit_install_counters(**_freeze_counters(install_counters))

        apply_file_change_plan(config_changes, journal=journal)
        install_counters["patches"][0] = len(active_patches) + len(active_section_patches)
        install_counters["ensure_lines"][0] = len(manifest.install.ensure_lines)
        reporter.emit_install_counters(**_freeze_counters(install_counters))

        verify_install_postflight(
            paths=paths,
            manifest=manifest,
            patch_results=tuple(patch_results),
            detected_firmware=detected_firmware,
        )
        install_counters["postflight"][0] = 1
        reporter.emit_install_counters(**_freeze_counters(install_counters))

        if klipper_halted:
            # Nothing else changes, and nothing reboots, unless Klipper starts with these files; otherwise the files
            # are put back below.
            _restart_halted_klipper(paths=paths, manifest=manifest, reporter=reporter, urlopen=urlopen)
            verify_pending_saved_variable_expectations(paths, urlopen=urlopen)
            _update_saved_variables(
                paths=paths,
                reporter=reporter,
                new_install=prior_state is None,
                journal=journal,
                written=saved_variables_written,
                urlopen=urlopen,
            )

        state = InstalledState(
            schema_version=1,
            package_id=manifest.package.id,
            package_version=manifest.package.version,
            runtime_firmware=detected_firmware,
            backup_label=plan.backup_label,
            installed_at=format_installed_at(started_at),
            managed_tree=ManagedTreeState(
                root=manifest.managed_tree.destination,
                files=plan.managed_tree_files,
            ),
            patch_ledger=tuple(
                PatchLedgerEntry(
                    id=result.id,
                    file=result.file,
                    section=result.section,
                    option=result.option,
                    expected=result.ledger_expected
                    or _preserve_prior_expected(result, prior_state),
                    desired=result.desired,
                    install_result=result.classification,
                )
                for result in patch_results
            ),
            source_patches=tuple(
                result.original
                for result in source_results
                if result.original is not None
            ),
            system_ledger=(
                prior_state.system_ledger
                if prior_state is not None and prior_state.system_ledger and prior_state.system_ledger.get("restore_preimages")
                # QIDI's package deleted the state file of the install a firmware update replaced; that update
                # kept its system ledger, so what that install changed on the host can still be undone.
                else load_carried_system_ledger(paths, manifest=manifest, environ=environ, run=run)
                if prior_state is None else None
            ),
        )
        journal.note_write()
        write_installed_state(state_path, state)
        clear_install_started(paths)
        migration_committed = True
        finalize_legacy_system_ledger_migration(paths)
        journal.commit()
        install_counters["state_write"][0] = 1
        reporter.emit_install_counters(**_freeze_counters(install_counters))

        # Units an earlier release wrote only for automatic updates mean they are on, before this install replaces them.
        adopt_legacy_enrollment(paths, environ)
        # The printer's clock runs in UTC: the update window needs the operator's timezone (update_window.py).
        update_window.ensure_timezone(paths, reporter=reporter, urlopen=urlopen)
        progress.step(progress.SYSTEM_CHANGES, reporter)
        state = maybe_apply_system_optimizations(
            paths=paths,
            manifest=manifest,
            reporter=reporter,
            state=state,
            state_path=state_path,
            input_stream=None,  # the install asked its questions before it started (install_flow.py)
            cli_options=system_options,
            environ=environ,
            auto_update_child=environ.get(LOCK_HELD_ENV) == "1",
            block_qidi_updates=block_qidi_updates,
            run=run,
            urlopen=urlopen,
        )
        boot_logo.install(paths, reporter=reporter, environ=environ, input_stream=input_stream, run=run)
    except BaseException as exc:
        reporter.debug(
            event="install.failure",
            backup_label=plan.backup_label,
            error_type=type(exc).__name__,
            message=getattr(exc, "message", str(exc)),
            rollback_started=journal.write_started,
        )
        if journal.write_started and not journal.committed:
            journal.rollback_or_raise(
                exc, backup_label=plan.backup_label, backup_zip_path=backup_zip_path
            )
            _note_outcome(exc, errors.OUTCOME_PUT_BACK)
            if isinstance(exc, HaltedKlipperDidNotStartError):
                # The failed process loaded the files that were just put back; start one with the files on disk.
                try:
                    request_klipper_restart(paths, urlopen=urlopen)
                except ProcessRestartError:
                    pass
        else:
            # Once the state is committed, Valve Oil's files stay installed: the system changes and boot logo that
            # come after aren't undone by the journal, and a later run finishes them.
            _note_outcome(exc, errors.OUTCOME_INSTALLED if journal.committed else errors.OUTCOME_UNCHANGED)
        if not journal.committed and not migration_committed:
            discard_legacy_system_ledger_migration(paths, migrated_paths)
        raise

    result = InstallResult(
        patch_results=tuple(patch_results),
        managed_tree_drift=plan.managed_tree_drift,
        backup_label=plan.backup_label,
        backup_zip_path=backup_zip_path,
    )
    reporter.emit_install_success(
        patch_results=result.patch_results,
        managed_tree_drift=result.managed_tree_drift,
    )
    maybe_update_installed_addons(
        paths=paths,
        manifest=manifest,
        reporter=reporter,
        input_stream=input_stream,
        environ=environ,
        run=run,
    )
    # The update check runs the bundle in the printer user's home; it is this one from now on.
    settle_installed_bundle(paths, environ)
    apply_auto_update_answer(paths=paths, reporter=reporter, answer=auto_updates, environ=environ)
    progress.step(progress.RESTART_KLIPPER, reporter)
    if klipper_halted:
        pass  # restarted on the installed files before anything else changed
    elif paths.restart_marker_path.exists():
        if saved_variables_written:
            write_saved_variable_verify_marker(paths, saved_variables_written)
        restarted = maybe_restart_pending_service_if_idle(
            paths=paths,
            allowed_entries=restart_entries(manifest),
            reporter=reporter,
            input_stream=input_stream,
            urlopen=urlopen,
        )
        if restarted:
            verify_pending_saved_variable_expectations(paths, urlopen=urlopen)
    elif environ.get(safety.FIRMWARE_UPDATE_RUN_ENV) and reboot_pending(paths):
        # The firmware update reboots the printer when it ends (firmware_update._end_succeeded), which starts Klipper
        # with the installed configuration.
        reporter.line("Klipper starts with the new configuration when the printer restarts.")
    else:
        maybe_restart_klipper(
            paths=paths,
            reporter=reporter,
            input_stream=input_stream,
            urlopen=urlopen,
        )
    reporter.debug(
        event="install.complete",
        dry_run=False,
        backup_label=plan.backup_label,
        backup_zip_path=backup_zip_path,
        managed_tree_drift=len(result.managed_tree_drift),
        user_modified_patches=len(
            [item for item in result.patch_results if item.classification == patches.USER_MODIFIED]
        ),
    )
    # An automatic update reboots from a check once it has recorded the release; a QIDI firmware update reboots when
    # it ends (firmware_update._end_succeeded).
    if environ.get(LOCK_HELD_ENV) == "1" and not environ.get(safety.FIRMWARE_UPDATE_RUN_ENV):
        arm_auto_update_reboot_followup(
            paths,
            reporter=reporter,
            environ=environ,
            run=run,
        )
    return result



def _update_saved_variables(*, paths, reporter, new_install, journal, written, urlopen) -> None:
    maybe_initialize_filament_retention(
        paths=paths, reporter=reporter, journal=journal, written=written, urlopen=urlopen
    )
    maybe_initialize_box_motion(
        paths=paths, reporter=reporter, journal=journal, written=written, urlopen=urlopen
    )
    maybe_enable_box(
        paths=paths, reporter=reporter, new_install=new_install, journal=journal,
        written=written, urlopen=urlopen,
    )
    maybe_write_required_tool_slot_variables(
        paths=paths, reporter=reporter, journal=journal, written=written, urlopen=urlopen
    )


def _restart_halted_klipper(*, paths, manifest: Manifest, reporter, urlopen) -> None:
    """Restart a Klipper that was halted before the install on the files just installed; raise unless it is ready."""
    reporter.line(messages.RESTARTING_HALTED_KLIPPER)
    try:
        if paths.restart_marker_path.exists():
            restart_pending(
                paths,
                allowed_entries=restart_entries(manifest),
                urlopen=urlopen,
            )
        else:
            restart_klipper_service(paths, urlopen=urlopen)
    except ProcessRestartError as exc:
        reporter.debug(event="install.halted_klipper.restart_failed", message=exc.message)
        raise HaltedKlipperDidNotStartError() from exc
    reporter.line(messages.KLIPPER_SERVICE_RESTARTED)


def _collect_install_patch_results(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
    detected_firmware: str,
    prior_state: InstalledState | None,
) -> tuple:
    results = []
    for patch in _active_install_patches(manifest, detected_firmware):
        path = paths.printer_data_root / patch.file
        text = klipper_cfg.read_text(path)
        resolved = klipper_cfg.resolve_unique_option(text, patch.section, patch.option)
        results.append(
            patches.classify_install_patch(
                resolved.value, patch, detected_firmware, prior_state
            )
        )
    for patch in _active_install_section_patches(manifest, detected_firmware):
        path = paths.printer_data_root / patch.file
        text = klipper_cfg.read_text(path)
        current_section = _resolve_section_text_or_none(text, patch.section)
        result = _preserve_prior_section_expected(
            patches.classify_install_section_delete(current_section, patch, detected_firmware),
            prior_state,
        )
        if result.classification == patches.INSTALL_NOOP_DESIRED and patches.is_section_hash(result.expected):
            # Already deleted with no record of what it held: record QIDI's text so uninstall can put it back.
            stock = patches.stock_section_text(
                paths.installer_root / manifest.printer_for_firmware(detected_firmware).stock_snapshot(detected_firmware),
                patch.file, patch.section, result.expected,
            )
            if stock is not None:
                result = replace(result, expected=stock)
        results.append(result)
    return tuple(results)


def _build_install_file_changes(*, paths: RuntimePaths, manifest: Manifest, patch_results):
    file_plan = FileChangePlan()
    for result in patch_results:
        if result.classification != patches.INSTALL_APPLIED:
            continue
        path = paths.printer_data_root / result.file
        text = file_plan.text(path)
        if result.option == "__section__":
            file_plan.replace_text(path, klipper_cfg.delete_section(text, result.section))
        else:
            file_plan.replace_text(
                path,
                klipper_cfg.set_option_value(
                    text, result.section, result.option, result.desired
                ),
            )
    for ensure_line in manifest.install.ensure_lines:
        path = paths.printer_data_root / ensure_line.file
        text = file_plan.text(path)
        new_text = ensure_line_after(text, ensure_line.line, ensure_line.after)
        if new_text != text:
            file_plan.replace_text(path, new_text)
    return file_plan.changes()


def _active_install_patches(manifest: Manifest, detected_firmware: str):
    active = set(active_patch_entries(manifest, detected_firmware))
    return tuple(patch for patch in manifest.patches.set_options if patch in active)



def _active_install_section_patches(manifest: Manifest, detected_firmware: str):
    active = set(active_patch_entries(manifest, detected_firmware))
    return tuple(patch for patch in manifest.patches.delete_sections if patch in active)



def _resolve_section_text_or_none(text: str, section: str) -> str | None:
    try:
        return klipper_cfg.resolve_unique_section(text, section).text
    except klipper_cfg.TargetResolutionError as exc:
        if exc.reason == "missing":
            return None
        raise



def _preserve_prior_expected(result, prior_state: InstalledState | None):
    if prior_state is not None:
        for entry in prior_state.patch_ledger:
            if entry.target_tuple == (result.file, result.section, result.option):
                return entry.expected
    return result.expected


def _preserve_prior_section_expected(result, prior_state: InstalledState | None):
    if result.option != "__section__" or result.classification != patches.INSTALL_NOOP_DESIRED:
        return result
    if prior_state is None:
        return result
    for entry in prior_state.patch_ledger:
        if entry.target_tuple == (result.file, result.section, result.option):
            return type(result)(
                id=result.id,
                file=result.file,
                section=result.section,
                option=result.option,
                current=result.current,
                expected=entry.expected,
                desired=result.desired,
                classification=result.classification,
            )
    return result



def _collect_install_include_line_intents(
    *,
    paths: RuntimePaths,
    manifest: Manifest,
) -> tuple[IncludeLineIntent, ...]:
    intents = []
    for ensure_line in manifest.install.ensure_lines:
        path = paths.printer_data_root / ensure_line.file
        text = klipper_cfg.read_text(path)
        new_text = ensure_line_after(text, ensure_line.line, ensure_line.after)
        intents.append(
            IncludeLineIntent(
                id=ensure_line.id,
                file=ensure_line.file,
                line=ensure_line.line,
                after=ensure_line.after,
                action="already_present" if new_text == text else "ensure",
            )
        )
    return tuple(intents)



def _source_hashes(paths: RuntimePaths, manifest: Manifest, detected_firmware: str) -> dict[str, str]:
    return collect_source_hashes(
        managed_tree_source_roots(paths, manifest, detected_firmware),
        destination_root=paths.printer_data_root / manifest.managed_tree.destination,
        relative_to=paths.printer_data_root,
        required_files=manifest.managed_tree.required_files,
    )



def _install_counters_template(
    manifest: Manifest, detected_firmware: str | None = None
) -> dict[str, list[int]]:
    if detected_firmware is None:
        patch_count = len(manifest.patches.set_options) + len(manifest.patches.delete_sections)
    else:
        patch_count = len(active_patch_entries(manifest, detected_firmware))
    return {
        "ensure_directories": [0, len(manifest.install.ensure_directories)],
        "extra_modules": [0, len(manifest.install.extra_modules)],
        "managed_trees": [0, 1],
        "ensure_lines": [0, len(manifest.install.ensure_lines)],
        "patches": [0, patch_count],
        "postflight": [0, 1],
        "state_write": [0, 1],
    }



def _emit_install_dry_run_counters(reporter, manifest: Manifest, detected_firmware: str) -> None:
    counters = _install_counters_template(manifest, detected_firmware)
    for name in counters:
        counters[name][0] = counters[name][1]
        reporter.emit_install_counters(**_freeze_counters(counters))



def _freeze_counters(counters: dict[str, list[int]]) -> dict[str, tuple[int, int]]:
    return {name: (value[0], value[1]) for name, value in counters.items()}


