from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import traceback
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from dataclasses import replace
from pathlib import Path

from . import addons_cli, boot_path, earlier_names, firmware_update, install_flow, install_screen, messages, progress, safety, thr_firmware, update_window, valve_oil_status
from .auto_update import (
    KIND_ENV,
    LOCK_HELD_ENV,
    AutoUpdateError,
    auto_update_enrolled,
    auto_update_status_lines,
    current_auto_update_checksum,
    disable_auto_updates,
    enable_auto_updates,
    selected_update_channel,
    prefetch_latest_release,
    newer_release,
    run_auto_update_check,
)
from .box_enablement import maybe_reconcile_tool_slots_after_box_count_change
from .backup import BackupArchiveError
from .compatibility import CompatibilityValidationError, load_supported_upgrade_sources, validate_manifest_compatibility
from .demo import resolve_demo_tui_delay_seconds, run_demo
from .errors import (ActivePrintError, InstallerError, KlipperNotReadyError, LockAcquisitionError, OperationCancelled,
                     PrinterStateError, RecoveryRequiredError)
from .host_reboot import (
    HostRebootError,
    maybe_schedule_recommended_reboot,
    maybe_schedule_host_reboot,
    perform_scheduled_host_reboot,
    read_host_reboot_marker,
    reboot_pending,
    verify_completed_host_reboot,
)
from .locking import acquire, acquire_shared
from .manifest import ManifestValidationError, load_manifest
from .models import RuntimePaths, SystemOptimizationCliOptions
from .ownership import repair_installer_ownership
from .reporter import create_reporter
from .run_log import LoggingReporter, open_run_log
from .restore_helper import RestoreHelperError, run_restore_helper
from .rollback import clear_recovery_sentinel
from .runner import block_qidi_package_updates, ensure_klipper_ready_for_new_install, run_install
from .safety import ensure_no_recovery_sentinel
from .state_file import StateValidationError
from .system_optimizations import (
    maybe_reconcile_system_optimizations,
    recover_pending_system_optimization,
    verify_rockchip_postflight,
)
from .uninstall import run_uninstall
from .board_firmware import board_preflight, deliver_board_firmware, existing_installation, load_board_admission
from .box_firmware import run_box_firmware
from .mainboard_firmware import run_mainboard_firmware

DEFAULT_PRINTER_DATA_ROOT = Path("/home/qidi/printer_data")
USB_ORIGIN_ENV = "VO_USB_INSTALL"  # set by the USB installer for the runs it starts (usb_install.py)
DEFAULT_FIRMWARE_MANIFEST = Path("/home/qidi/update/firmware_manifest.json")
DEFAULT_MOONRAKER_URL = "http://127.0.0.1:7125/printer/objects/query?print_stats"
FIRMWARE_MODES = {"box-firmware", "mainboard-firmware"}
AUTO_UPDATE_SWITCH_MODES = {"enable-auto-updates", "disable-auto-updates", "auto-update-status"}
# --window not given: the saved window stays as it is.
_UNCHANGED = object()
# Runs that keep a log in printer_data/logs (run_log.py).
LOGGED_MODES = {"install", "uninstall", "restore-backup", "usb"}


def main(
    argv: list[str] | None = None,
    *,
    stream=None,
    input_stream=None,
    bundle_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    if argv in (["--version"], ["-v"]):
        root = bundle_root or Path(__file__).resolve().parents[2]
        print(installer_version(root), file=stream or sys.stdout)
        return 0
    if argv and argv[0] == "addons":
        env = _environment(environ)
        root = bundle_root or Path(__file__).resolve().parents[2]
        addon_paths = resolve_runtime_paths(bundle_root=root, environ=env)
        try:
            earlier_names.migrate_when_free(addon_paths)
        except InstallerError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        return addons_cli.main(
            argv[1:],
            paths=addon_paths,
            stream=stream,
            input_stream=input_stream,
            environ=env,
        )
    args = parse_args(argv)
    if args.show_version:
        print(installer_version(bundle_root or Path(__file__).resolve().parents[2]), file=stream or sys.stdout)
        return 0
    env = _environment(environ)
    bundle_root = bundle_root or Path(__file__).resolve().parents[2]
    reporter = create_reporter(
        stream,
        prefer_plain=args.plain,
        bundle_root=bundle_root,
        environ=env,
        debug=args.debug,
    )
    input_stream = None if args.yes else (input_stream or sys.stdin)
    paths = resolve_runtime_paths(bundle_root=bundle_root, environ=env)
    reporter.debug(
        event="cli.start",
        mode=args.mode,
        dry_run=args.dry_run,
        demo_tui=args.demo_tui,
        printer_data_root=paths.printer_data_root,
        bundle_root=bundle_root,
    )

    run_log = None
    if args.mode in LOGGED_MODES and not args.demo_tui and not args.dry_run:
        run_log = open_run_log(paths, args.mode)
        reporter = LoggingReporter(reporter, run_log)
        reporter.debug(event="cli.log", path=run_log.path,
                       argv=" ".join(argv if argv is not None else sys.argv[1:]))
    try:
        with _survives_hangup():
            rc = _dispatch(args, env=env, bundle_root=bundle_root, reporter=reporter, paths=paths,
                           input_stream=input_stream)
            if args.mode == "install" and not args.dry_run:
                earlier_names.finish_earlier_parent_run(paths, succeeded=rc == 0)
            return rc
    finally:
        if run_log is not None:
            run_log.close()


@contextmanager
def _survives_hangup():
    """Keep a run going when the SSH session closing hangs up its terminal.

    The default SIGHUP stops the process at once, with no rollback and no record of where it was, which can leave the
    printer half changed. The run instead goes on to its end and writes only its log: the terminal output is pointed at
    /dev/null, so writing to a terminal that is gone can't fail it either. Questions are all asked first, so nothing
    waits for an answer that can't come; one that does reads end of input."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def hung_up(signum, frame):
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            for descriptor in (1, 2):
                with suppress(OSError):
                    os.dup2(devnull, descriptor)
        finally:
            os.close(devnull)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    previous = signal.signal(signal.SIGHUP, hung_up)
    try:
        yield
    finally:
        signal.signal(signal.SIGHUP, previous)


def _dispatch(args, *, env, bundle_root, reporter, paths, input_stream, front_end=None) -> int:
    """Run args.mode. front_end, for an install the touchscreen drives (usb_install.py), holds the screen, asks the
    questions, and hears how the run ended; the terminal does all three otherwise."""
    try:
        if args.mode == "usb":
            return _usb(args, env=env, bundle_root=bundle_root, reporter=reporter, paths=paths)

        if args.demo_tui:
            delay_seconds = resolve_demo_tui_delay_seconds(environ=env)
            reporter.debug(
                event="cli.demo_tui.start",
                mode=args.mode,
                delay_seconds=delay_seconds,
            )
            run_demo(args.mode, reporter, delay_seconds=delay_seconds)
            reporter.debug(
                event="cli.complete",
                mode=args.mode,
                return_code=0,
                action="demo-tui",
            )
            return 0

        # Releases before 26.11.22.3 named Valve Oil's files tltg; everything below reads them by their current names.
        if earlier_names.migrate_when_free(paths):
            reporter.debug(event="cli.earlier_names.moved")

        if args.mode in AUTO_UPDATE_SWITCH_MODES:
            # Only the enrollment marker and the window's settings change: no sudo, no lock, and the check reads them at
            # its next run.
            if args.window is not _UNCHANGED:
                update_window.set_window(paths, args.window)
            if args.timezone is not None:
                update_window.set_timezone(paths, args.timezone, source="operator")
            if args.mode == "enable-auto-updates":
                enable_auto_updates(paths=paths, reporter=reporter, environ=env)
            elif args.mode == "disable-auto-updates":
                disable_auto_updates(paths=paths, reporter=reporter, environ=env)
            else:
                for line in auto_update_status_lines(paths, env):
                    reporter.line(line)
            reporter.debug(event="cli.complete", mode=args.mode, return_code=0)
            return 0

        in_firmware_update = bool(env.get(safety.FIRMWARE_UPDATE_RUN_ENV))
        if args.mode == "install" and not in_firmware_update:
            # A QIDI firmware update this or an earlier session started: follow or finish it, or report how it ended.
            rc = firmware_update.report_or_attach(paths=paths, reporter=reporter, environ=env,
                                                  input_stream=input_stream, dry_run=args.dry_run)
            if rc is not None:
                reporter.debug(event="cli.complete", mode=args.mode, return_code=rc, action="firmware-update")
                return rc

        # A check on a printer that isn't enrolled uses no sudo at all.
        checking_only = args.mode == "auto-update-check" and not auto_update_enrolled(paths)
        if not args.dry_run and not (args.mode in FIRMWARE_MODES and args.box_action == "status") and not checking_only:
            repair_installer_ownership(paths, reporter=reporter, environ=env, input_stream=input_stream)

        if args.mode == "box-firmware":
            if args.box_action == "status":
                return run_box_firmware(paths=paths, reporter=reporter, action="status", environ=env)
            with acquire(paths.lock_path):
                rc = run_box_firmware(
                    paths=paths,
                    reporter=reporter,
                    action=args.box_action,
                    boxes=args.box,
                    input_stream=input_stream,
                    environ=env,
                )
            reporter.debug(event="cli.complete", mode=args.mode, return_code=rc)
            return rc

        if args.mode == "mainboard-firmware":
            if args.box_action == "status":
                return run_mainboard_firmware(paths=paths, reporter=reporter, action="status", environ=env)
            with acquire(paths.lock_path):
                rc = run_mainboard_firmware(
                    paths=paths,
                    reporter=reporter,
                    action=args.box_action,
                    input_stream=input_stream,
                    environ=env,
                )
            reporter.debug(event="cli.complete", mode=args.mode, return_code=rc)
            return rc

        if (not args.dry_run and not checking_only and not in_firmware_update
                and (paths.printer_data_root / safety.THR_RECOVERY_MARKER).exists()):
            # A THR flash an earlier run left unfinished is finished first, as vo-thr-firmware.service does at boot.
            with acquire(paths.lock_path):
                try:
                    thr_firmware.finish_pending(paths=paths, reporter=reporter, environ=env, input_stream=input_stream)
                except InstallerError as exc:
                    reporter.line(exc.message)
        safety.ensure_no_pending_firmware(paths.recovery_sentinel_path)

        if args.mode == "complete-host-reboot":
            with acquire(paths.lock_path):
                ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
                current_manifest = load_manifest(paths.installer_root / "package.yaml")
                recover_pending_system_optimization(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    input_stream=None,
                    environ=env,
                )
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                )
                if read_host_reboot_marker(paths) is not None and _host_reboot_operation_current(
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                ):
                    perform_scheduled_host_reboot(
                        paths,
                        reporter=reporter,
                        environ=env,
                        require_klipper_idle=args.require_klipper_idle,
                        require_ready=not args.operator_approved,
                    )
                elif read_host_reboot_marker(paths) is None:
                    perform_scheduled_host_reboot(
                        paths,
                        reporter=reporter,
                        environ=env,
                        recommended=True,
                        require_klipper_idle=args.require_klipper_idle,
                        require_ready=not args.operator_approved,
                    )
            return 0

        if args.mode == "auto-update-check":
            # Fetched before the lock: an exclusive hold stops print starts, and the network may be slow.
            channel = selected_update_channel(paths)
            latest = prefetch_latest_release(env, channel=channel)
            with ExitStack() as held:
                try:
                    held.enter_context(acquire_shared(paths.lock_path) if checking_only else acquire(paths.lock_path))
                except LockAcquisitionError:
                    # An install, uninstall, or update is running; the next hourly check sees what it leaves. A
                    # failed run here would only mark the service failed.
                    reporter.line(messages.AUTO_UPDATE_SKIPPED_ANOTHER_RUN)
                    reporter.debug(event="cli.complete", mode=args.mode, return_code=0, action="skipped-locked")
                    return 0
                reporter.debug(
                    event="cli.lock.acquired",
                    mode=args.mode,
                    lock_path=paths.lock_path,
                )
                if channel != selected_update_channel(paths):
                    reporter.line("The update source changed during the check; try again.")
                    return 0
                enrolled = not checking_only and auto_update_enrolled(paths)
                if not enrolled:
                    # Only check and publish: no recovery, reconciliation, install, board flash, service change, or
                    # reboot on a printer whose operator hasn't turned automatic updates on.
                    result = run_auto_update_check(paths=paths, reporter=reporter, environ=env, enrolled=False,
                                                   latest=latest)
                    reporter.debug(event="cli.complete", mode=args.mode, return_code=0, action=result.action)
                    return 0
                try:
                    ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
                except RecoveryRequiredError:
                    # What is waiting is still shown, with recovery as the reason it doesn't run.
                    try:
                        run_auto_update_check(paths=paths, reporter=reporter, environ=env, enrolled=True,
                                              recovery_required=True, latest=latest)
                    except InstallerError:
                        pass
                    raise
                reporter.debug(
                    event="cli.recovery_sentinel.clear",
                    sentinel_path=paths.recovery_sentinel_path,
                )
                current_manifest = load_manifest(paths.installer_root / "package.yaml")
                recover_pending_system_optimization(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    input_stream=None,
                    environ=env,
                )
                maybe_reconcile_tool_slots_after_box_count_change(paths=paths, reporter=reporter)
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                )
                pending_reboot = read_host_reboot_marker(paths)
                release_state_committed = _auto_reboot_release_committed(
                    paths=paths,
                    marker=pending_reboot,
                )
                if release_state_committed and _host_reboot_operation_current(
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                ):
                    maybe_schedule_host_reboot(
                        paths,
                        reporter=reporter,
                        input_stream=None,
                        environ=env,
                        automatic=True,
                        require_klipper_idle=True,
                    )
                    reporter.debug(
                        event="cli.complete",
                        mode=args.mode,
                        return_code=0,
                        action="pending-host-reboot",
                    )
                    return 0
                result = run_auto_update_check(paths=paths, reporter=reporter, environ=env, enrolled=True, latest=latest)
                if result.action == "firmware-update-started":
                    # The firmware update runs on its own and stops the services; nothing else runs beside it.
                    reporter.debug(event="cli.complete", mode=args.mode, return_code=0, action=result.action)
                    return 0
                if result.action in ("already-current", "boards-updated", "boards-not-updated"):
                    try:
                        safety.ensure_printer_idle(paths.moonraker_url)
                        safety.ensure_klipper_ready(paths.moonraker_url)
                    except ActivePrintError:
                        reporter.line(messages.AUTO_UPDATE_SKIPPED_ACTIVE_PRINT)
                    except KlipperNotReadyError:
                        reporter.line(messages.AUTO_UPDATE_SKIPPED_KLIPPER_NOT_READY)
                    except PrinterStateError:
                        reporter.line(messages.AUTO_UPDATE_SKIPPED_UNKNOWN_STATE)
                    else:
                        current_manifest = load_manifest(paths.installer_root / "package.yaml")
                        maybe_reconcile_system_optimizations(
                            paths=paths,
                            manifest=current_manifest,
                            reporter=reporter,
                            environ=env,
                            block_qidi_updates=block_qidi_package_updates(load_board_admission(paths)),
                        )
                current_manifest = load_manifest(paths.installer_root / "package.yaml")
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                )
                pending_reboot = read_host_reboot_marker(paths)
                if _auto_reboot_release_committed(
                    paths=paths,
                    marker=pending_reboot,
                ) and _host_reboot_operation_current(
                    manifest=current_manifest,
                    reporter=reporter,
                    environ=env,
                ):
                    maybe_schedule_host_reboot(
                        paths,
                        reporter=reporter,
                        input_stream=None,
                        environ=env,
                        automatic=True,
                        require_klipper_idle=True,
                    )
                elif pending_reboot is None:
                    maybe_schedule_recommended_reboot(
                        paths,
                        reporter=reporter,
                        input_stream=None,
                        environ=env,
                        require_klipper_idle=True,
                    )
                reporter.debug(
                    event="cli.complete",
                    mode=args.mode,
                    return_code=0,
                    action=result.action,
                )
                return 0

        if args.mode == "clear-recovery-sentinel":
            with acquire(paths.lock_path):
                reporter.debug(
                    event="cli.lock.acquired",
                    mode=args.mode,
                    lock_path=paths.lock_path,
                )
                manifest = load_manifest(paths.installer_root / "package.yaml")
                safety.ensure_no_pending_firmware(paths.recovery_sentinel_path)
                removed = clear_recovery_sentinel(
                    paths.recovery_sentinel_path,
                    printer_data_root=paths.printer_data_root,
                    external_root=paths.managed_klipper_root,
                    allowed_external_entries={
                        patch.id: patch.destination
                        for patch in manifest.install.source_patches
                    },
                    install_label_prefix=manifest.backup.label_prefix,
                )
                reporter.emit_clear_recovery_sentinel(removed)
                reporter.debug(
                    event="cli.complete",
                    mode=args.mode,
                    removed=removed,
                )
                return 0

        manifest = load_manifest(paths.installer_root / "package.yaml")
        reporter.debug(
            event="cli.manifest.loaded",
            package_version=manifest.package.version,
            install_label_prefix=manifest.backup.label_prefix,
        )

        if args.mode == "restore-backup":
            with acquire(paths.lock_path):
                reporter.debug(
                    event="cli.lock.acquired",
                    mode=args.mode,
                    lock_path=paths.lock_path,
                )
                safety.ensure_no_pending_firmware(paths.recovery_sentinel_path)
                rc = run_restore_helper(
                    paths,
                    manifest,
                    reporter=reporter,
                    input_stream=input_stream,
                    backup_path=args.backup,
                    debug=reporter.debug,
                    environ=env,
                )
                reporter.debug(event="cli.complete", mode=args.mode, return_code=rc)
                return rc

        compatibility = load_supported_upgrade_sources(
            paths.installer_root / "supported_upgrade_sources.yaml"
        )
        validate_manifest_compatibility(manifest, compatibility)
        reporter.debug(event="cli.compatibility.validated")

        lock_inherited = _lock_inherited(args.mode, env)
        firmware = None
        reboot_authorized = False
        with nullcontext() if lock_inherited else acquire(paths.lock_path):
            reporter.debug(
                event="cli.lock.inherited" if lock_inherited else "cli.lock.acquired",
                mode=args.mode,
                lock_path=paths.lock_path,
            )
            ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
            reporter.debug(
                event="cli.recovery_sentinel.clear",
                sentinel_path=paths.recovery_sentinel_path,
            )
            if not args.dry_run:
                recover_pending_system_optimization(
                    paths=paths,
                    manifest=manifest,
                    reporter=reporter,
                    input_stream=input_stream,
                    environ=env,
                )
                _verify_completed_host_reboot(
                    paths=paths,
                    manifest=manifest,
                    reporter=reporter,
                    environ=env,
                )
            if args.mode == "install":
                firmware = _install(args, paths=paths, manifest=manifest, reporter=reporter, env=env,
                                    input_stream=input_stream, in_firmware_update=in_firmware_update,
                                    front_end=front_end)
            else:
                uninstall_result = run_uninstall(
                    paths,
                    manifest,
                    compatibility,
                    reporter,
                    dry_run=args.dry_run,
                    input_stream=input_stream,
                    environ=env,
                    system_options=_system_options_from_args(args),
                )
                reboot_authorized = args.reboot_host or getattr(uninstall_result, "reboot_authorized", False)
        if firmware is not None and firmware.state == firmware_update.FAILED:
            reporter.debug(event="cli.complete", mode=args.mode, return_code=1, action="firmware-update")
            return 1
        if args.dry_run:
            if args.mode == "install":
                reporter.line("Host reboot dry-run: would schedule a delayed reboot after successful completion if a system change needs one.")
            elif args.reboot_host:
                reporter.line("Host reboot dry-run: would schedule a delayed reboot after successful completion.")
        elif firmware is not None and firmware.handled:
            pass  # the firmware update schedules the reboot its install needs when it ends
        elif front_end is not None:
            pass  # the USB installer restarts the printer when its drive comes out (usb_install.py)
        elif not lock_inherited and args.mode == "install" and _reboot_held_for_unfinished_install(paths, reporter):
            pass  # the reboot stays pending for a run that finishes
        elif (
            not lock_inherited
            and read_host_reboot_marker(paths) is not None
            and _host_reboot_operation_current(
                manifest=manifest,
                reporter=reporter,
                environ=env,
            )
        ):
            # Every question was asked before the run: an uninstall asked about this reboot with its other questions.
            maybe_schedule_host_reboot(
                paths,
                reporter=reporter,
                input_stream=None,
                environ=env,
                explicit=args.mode == "uninstall" and reboot_authorized,
                automatic=args.mode == "install",
            )
        elif not lock_inherited and args.mode == "install" and read_host_reboot_marker(paths) is None:
            # Without a pending Rockchip reboot, reboot for the host tuning changes.
            maybe_schedule_recommended_reboot(
                paths,
                reporter=reporter,
                input_stream=None,
                environ=env,
            )
        reporter.debug(event="cli.complete", mode=args.mode, return_code=0)
        return 0
    except KeyboardInterrupt:
        reporter.debug(
            event="cli.interrupted",
            mode=args.mode,
            return_code=130,
        )
        reporter.line(messages.INTERRUPTED)
        return 130
    except OperationCancelled as exc:
        reporter.debug(
            event="cli.complete",
            mode=args.mode,
            return_code=0,
            action="cancelled",
        )
        return 0
    except (
        ManifestValidationError,
        CompatibilityValidationError,
        StateValidationError,
        BackupArchiveError,
        RestoreHelperError,
        AutoUpdateError,
    ) as exc:
        reporter.debug(
            event="cli.failure",
            mode=args.mode,
            error_type=type(exc).__name__,
            message=getattr(exc, "message", str(exc)),
        )
        reporter.emit_error(exc)
        _name_log(reporter)
        if args.debug:
            traceback.print_exc(file=reporter.stream)
        return 1
    except InstallerError as exc:
        reporter.debug(
            event="cli.failure",
            mode=args.mode,
            error_type=type(exc).__name__,
            message=getattr(exc, "message", str(exc)),
        )
        reporter.emit_error(exc)
        _name_log(reporter)
        if args.debug:
            traceback.print_exc(file=reporter.stream)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        reporter.debug(
            event="cli.failure",
            mode=args.mode,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        reporter.emit_error(exc)
        _name_log(reporter)
        if args.debug:
            traceback.print_exc(file=reporter.stream)
        return 1



def _install(args, *, paths, manifest, reporter, env, input_stream, in_firmware_update, front_end=None):
    """Ask every question, then install without input. Returns the firmware update the install started, or None.

    The touchscreen shows Valve Oil installing from the first board read, which stops Klipper and the app, until the
    install is done; a firmware update started here takes it over (install_screen.py). A front end that holds the
    screen itself (usb_install.py) has it from its first screen."""
    firmware = None
    if not args.dry_run:
        # First of all: a printer QIDI's app left unable to start can also leave Klipper in error (boot_path.py).
        boot_path.protect(paths, reporter=reporter, environ=env, dry_run=False, input_stream=input_stream)
    if not in_firmware_update and not existing_installation(paths, manifest):
        # Before the touchscreen is taken or a board is read: a new install needs a printer that works as it is.
        ensure_klipper_ready_for_new_install(paths)
    if front_end is not None:
        screen = nullcontext(front_end.hold)
    elif args.dry_run:
        screen = nullcontext()
    else:
        screen = install_screen.held(paths=paths, reporter=reporter, environ=env, input_stream=input_stream)
    # Valve Oil UI follows the install's steps from the status service, however it was started.
    published = nullcontext() if args.dry_run else valve_oil_status.progress_published(paths, log=_log_name(reporter))
    try:
        with published as publisher, screen as hold:
            firmware = _install_held(args, hold, paths=paths, manifest=manifest, reporter=reporter, env=env,
                                     input_stream=input_stream, in_firmware_update=in_firmware_update,
                                     front_end=front_end)
            if publisher is not None and firmware is not None and firmware.handled:
                publisher.outcome = valve_oil_status.HANDED_OFF
    finally:
        progress.end()
    if not args.dry_run and (firmware is None or not firmware.handled):
        _settle_waiting_release(paths, manifest.package.version)
    return firmware


def _install_held(args, hold, *, paths, manifest, reporter, env, input_stream, in_firmware_update, front_end):
    firmware = None
    # Every board is read before anything changes; a new install with a board this release doesn't admit stops
    # here.
    if front_end is not None and not args.dry_run:
        front_end.reading_boards()
    printer, admission = board_preflight(
        paths=paths, reporter=reporter, manifest=manifest, dry_run=args.dry_run,
        input_stream=input_stream, environ=env,
    )
    options = _system_options_from_args(args)
    overview = None
    if in_firmware_update:
        # The session that started the firmware update asked the questions.
        answers = install_flow.load_pending_answers(paths)
    else:
        overview = install_flow.plan_install(paths, manifest, environ=env, system_options=options,
                                             admission=admission)
        reporter.debug(event="install.overview", printer=overview.printer_id, firmware=overview.firmware,
                       installed=overview.installed_version, firmware_update=overview.firmware_update,
                       questions=",".join(question.id for question in overview.questions))
        answers = install_flow.Answers()
        if front_end is not None:
            answers = front_end.ask(overview)
        elif input_stream is not None and not args.dry_run:
            answers = install_flow.ask_in_terminal(overview, reporter=reporter, input_stream=input_stream)
    options = install_flow.system_options_for(answers, options)
    if not args.dry_run:
        progress.begin(install_flow.install_steps(overview), origin=_progress_origin(env), reporter=reporter,
                       from_version=overview.installed_version if overview else None,
                       to_version=manifest.package.version)
    if not in_firmware_update:
        if not args.dry_run:
            install_flow.save_pending_answers(paths, answers)
        # On a firmware this release updates from, the detached firmware update does the rest of the install;
        # when it can't start, Valve Oil installs for the current firmware below.
        firmware = firmware_update.start_firmware_update(
            paths=paths, reporter=reporter, manifest=manifest, admission=admission,
            kind=env.get(KIND_ENV) or "install", environ=env, input_stream=None,
            dry_run=args.dry_run,
        )
        if front_end is not None:
            front_end.firmware_update(firmware)
    if firmware is None or not firmware.handled:
        if overview is not None and overview.firmware_update:
            progress.skip(progress.QIDI_FIRMWARE.format(target=overview.target_firmware))
        if hold is not None:
            hold.retake()
        run_install(
            paths,
            manifest,
            reporter,
            dry_run=args.dry_run,
            input_stream=None,
            environ=env,
            system_options=options,
            block_qidi_updates=block_qidi_package_updates(admission),
            auto_updates=answers.auto_updates,
        )
        deliver_board_firmware(
            paths=paths, reporter=reporter, manifest=manifest, printer=printer, admission=admission,
            dry_run=args.dry_run, input_stream=None, environ=env,
        )
        if not args.dry_run:
            install_flow.clear_pending_answers(paths)
            progress.step(progress.FINISH, reporter)
    return firmware


def _usb(args, *, env, bundle_root, reporter, paths) -> int:
    """The USB installer's session on the touchscreen (usb_install.py), which runs install and restore-backup here."""
    from . import qidi_package, usb_install

    drive = Path(args.drive)
    env[qidi_package.USB_DRIVE_ENV] = str(drive)
    env[USB_ORIGIN_ENV] = "1"
    manifest = load_manifest(paths.installer_root / "package.yaml")

    def dispatch(argv, *, front_end=None, input_stream=None, errors_to=None):
        """errors_to: who hears the error a run stops on, when it isn't front_end."""
        inner = parse_args(argv)
        listener = front_end or errors_to
        wrapped = listener.wrap(reporter) if listener is not None else reporter
        return _dispatch(inner, env=env, bundle_root=bundle_root, reporter=wrapped, paths=paths,
                         input_stream=input_stream, front_end=front_end)

    return usb_install.run(paths=paths, manifest=manifest, env=env, reporter=reporter, drive=drive,
                           dispatch=dispatch)


def _progress_origin(env) -> str:
    if env.get(safety.FIRMWARE_UPDATE_RUN_ENV):
        return progress.ORIGIN_FIRMWARE_UPDATE
    if env.get(LOCK_HELD_ENV) == "1":
        return progress.ORIGIN_AUTOMATIC
    return env.get(USB_ORIGIN_ENV) and progress.ORIGIN_USB or progress.ORIGIN_SSH


def _log_name(reporter) -> str | None:
    path = getattr(getattr(reporter, "log", None), "path", None)
    return path.name if path is not None else None


def _name_log(reporter) -> None:
    path = getattr(getattr(reporter, "log", None), "path", None)
    if path is not None:
        reporter.line(f"The log of this run is {path}; download it from Fluidd's Logs to report this.")


def _reboot_held_for_unfinished_install(paths, reporter) -> bool:
    """Whether a reboot this install would schedule is held because the install didn't finish: Klipper still has to
    restart onto the installed files. A Klipper that isn't ready holds every reboot (host_reboot._printer_allows_reboot).
    Holding leaves the reboot pending, so the next install that finishes schedules it."""
    if not reboot_pending(paths) or not paths.restart_marker_path.exists():
        return False
    reporter.line(messages.HOST_REBOOT_HELD_UNFINISHED.format(reason=messages.HOST_REBOOT_HELD_RESTART_PENDING))
    return True


def _verify_completed_host_reboot(
    *,
    paths: RuntimePaths,
    manifest,
    reporter,
    environ: dict[str, str],
) -> bool:
    if read_host_reboot_marker(paths) is None:
        return False
    spec = manifest.system_optimizations
    if spec is None:
        raise HostRebootError(messages.HOST_REBOOT_POSTFLIGHT_FAILED)
    return verify_completed_host_reboot(
        paths,
        reporter=reporter,
        verify_operation=lambda: verify_rockchip_postflight(
            spec=spec.rockchip_root_sync,
            environ=environ,
        ),
    )


def _auto_reboot_release_committed(*, paths: RuntimePaths, marker) -> bool:
    if marker is None:
        return False
    if marker.source != "auto_update_child":
        return True
    before = marker.auto_update_checksum_before
    return before is not None and current_auto_update_checksum(paths) not in {None, before}


def _host_reboot_operation_current(*, manifest, reporter, environ: dict[str, str]) -> bool:
    spec = manifest.system_optimizations
    if spec is None:
        reporter.line(messages.HOST_REBOOT_CURRENT_STATE_FAILED)
        return False
    try:
        verify_rockchip_postflight(
            spec=spec.rockchip_root_sync,
            environ=environ,
        )
    except InstallerError:
        reporter.line(messages.HOST_REBOOT_CURRENT_STATE_FAILED)
        return False
    return True


def _settle_waiting_release(paths, version: str) -> None:
    """This release is installed now: a release the last update check listed as waiting that isn't newer, such as this
    one, one this install went past, or this one installed again after QIDI's package emptied the config folder, is
    done, and the status records this release as installed. Until the next check the file would otherwise say an older
    release is waiting."""

    def done(item: valve_oil_status.PendingItem) -> bool:
        return item.kind == valve_oil_status.RELEASE and (item.version == version or newer_release(version, item.version))

    status = valve_oil_status.load_status(paths)
    if status.installed_release == version and not any(done(item) for item in status.pending):
        return
    try:
        valve_oil_status.update_status(paths, lambda current: replace(
            current, installed_release=version, pending=tuple(item for item in current.pending if not done(item))))
    except OSError:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valve Oil installer runtime")
    parser.add_argument(
        "mode",
        choices=[
            "install",
            "uninstall",
            "clear-recovery-sentinel",
            "restore-backup",
            "auto-update",
            "auto-update-check",
            "enable-auto-updates",
            "disable-auto-updates",
            "auto-update-status",
            "complete-host-reboot",
            "box-firmware",
            "mainboard-firmware",
            "usb",
        ],
    )
    parser.add_argument("box_action", nargs="?", choices=["status", "install", "restore", "rescue"], help="box-firmware or mainboard-firmware action: status, install, restore, or rescue.")
    parser.add_argument("--box", type=int, action="append", metavar="N", help="box-firmware: limit install or restore to Box N (repeatable); rescue needs exactly one.")
    parser.add_argument("-v", "--version", dest="show_version", action="store_true", help="Show the installer package version and exit.")
    parser.add_argument("--plain", action="store_true", help="Use plain text output instead of the rich terminal UI.")
    parser.add_argument("--debug", action="store_true", help="Print debug events and tracebacks on failure.")
    parser.add_argument("--dry-run", action="store_true", help="Preview install or uninstall actions without writing changes.")
    parser.add_argument("--demo-tui", action="store_true", help="Render a demo of the install/uninstall TUI without writing changes.")
    parser.add_argument("--backup", help="Backup label or path to restore with restore-backup mode.")
    parser.add_argument("--drive", help=argparse.SUPPRESS)  # usb: the USB drive's mount point
    parser.add_argument("--yes", action="store_true", help="Run non-interactively using default yes-mode choices.")
    # Every install now brings the THR to Valve Oil's firmware; the old opt-in is accepted and does nothing.
    parser.add_argument("--flash-thr-fifo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--disable-ai-detection", action="store_true", help="Disable the QIDI AI detection backend service when system optimizations run.")
    parser.add_argument("--keep-ai-detection", action="store_true", help="Keep the QIDI AI detection backend service enabled when system optimizations run.")
    parser.add_argument("--keep-system-optimizations", action="store_true", help="During uninstall, keep the DNS, APT, qidiclient GIF, Rockchip root-mount, VPN, Bluetooth, and AI service changes.")
    parser.add_argument("--reboot-host", action="store_true", help="Authorize a pending managed host OS reboot after successful uninstall; install reboots without it.")
    parser.add_argument("--require-klipper-idle", action="store_true", help=argparse.SUPPRESS)
    # complete-host-reboot: the operator approved this reboot (uninstall), so it doesn't wait for a ready Klipper.
    parser.add_argument("--operator-approved", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--uninstall", action="store_true", help="Compatibility alias for the uninstall mode.")
    parser.add_argument("--clear-recovery-sentinel", action="store_true", help="Compatibility alias for the clear-recovery-sentinel mode.")
    auto_update_actions = parser.add_mutually_exclusive_group()
    auto_update_actions.add_argument("--run", dest="auto_update_action", action="store_const", const="run", help="Run the update check now (auto-update mode only).")
    # The check is scheduled by every install; these only turn automatic updates on or off. The -systemd names are
    # what earlier releases documented.
    for flags, action, text in ((("--enable", "--enable-systemd"), "enable", "Turn automatic updates on"),
                                (("--disable", "--disable-systemd"), "disable", "Turn automatic updates off"),
                                (("--status",), "status", "Show whether automatic updates are on and what is waiting")):
        auto_update_actions.add_argument(*flags, dest="auto_update_action", action="store_const", const=action,
                                         help=text + " (auto-update mode only).")
    parser.add_argument("--window", metavar="HOURS", help="Limit automatic updates to these hours, such as 2-5 or 22-4 (up to, not including, the end hour), or any (auto-update mode only).")
    parser.add_argument("--timezone", metavar="ZONE", help="The IANA timezone the update window is read in, such as America/Chicago (auto-update mode only).")
    args = parser.parse_args(_normalize_command_aliases(argv))
    if args.uninstall:
        if args.mode != "install":
            parser.error("--uninstall cannot be combined with another mode.")
        args.mode = "uninstall"
    if args.clear_recovery_sentinel:
        if args.mode != "install" or args.uninstall:
            parser.error("--clear-recovery-sentinel cannot be combined with another mode.")
        args.mode = "clear-recovery-sentinel"
    if (args.auto_update_action or args.window is not None or args.timezone is not None) and args.mode != "auto-update":
        parser.error("auto-update action flags are only supported with auto-update.")
    if args.window is not None or args.timezone is not None:
        if args.auto_update_action == "run":
            parser.error("--window and --timezone go with --enable, --disable, --status, or on their own.")
        try:
            args.window = update_window.parse_window_argument(args.window) if args.window is not None else _UNCHANGED
            if args.timezone is not None and update_window.zone(args.timezone) is None:
                raise update_window.UpdateWindowError(
                    f"{args.timezone} isn't a known timezone; use an IANA name such as America/Chicago.")
        except update_window.UpdateWindowError as exc:
            parser.error(str(exc))
        args.auto_update_action = args.auto_update_action or "status"
    else:
        args.window = _UNCHANGED
    if args.mode == "auto-update":
        if args.auto_update_action == "run":
            args.mode = "auto-update-check"
        elif args.auto_update_action == "enable":
            args.mode = "enable-auto-updates"
        elif args.auto_update_action == "disable":
            args.mode = "disable-auto-updates"
        elif args.auto_update_action == "status":
            args.mode = "auto-update-status"
        else:
            parser.error("auto-update requires --run, --enable, --disable, or --status.")
    if args.dry_run and args.mode not in {"install", "uninstall"}:
        parser.error("--dry-run is only supported with install and uninstall.")
    if args.demo_tui and args.mode not in {"install", "uninstall"}:
        parser.error("--demo-tui is only supported with install and uninstall.")
    if args.demo_tui and args.dry_run:
        parser.error("--demo-tui cannot be combined with --dry-run.")
    if (args.mode == "usb") != (args.drive is not None):
        parser.error("usb needs --drive, which only usb takes.")
    if args.backup and args.mode != "restore-backup":
        parser.error("--backup is only supported with restore-backup.")
    if args.yes and args.mode not in {"install", "uninstall", "auto-update-check"}:
        parser.error("--yes is only supported with install, uninstall, and auto-update-check.")
    if args.disable_ai_detection and args.keep_ai_detection:
        parser.error("--disable-ai-detection and --keep-ai-detection cannot be combined.")
    if (args.disable_ai_detection or args.keep_ai_detection) and args.mode not in {"install", "uninstall"}:
        parser.error("system optimization flags are only supported with install and uninstall.")
    if args.keep_system_optimizations and args.mode != "uninstall":
        parser.error("--keep-system-optimizations is only supported with uninstall.")
    if args.flash_thr_fifo and args.mode != "install":
        parser.error("--flash-thr-fifo is only supported with install.")
    if (args.mode in FIRMWARE_MODES) != (args.box_action is not None):
        parser.error("box-firmware and mainboard-firmware need one of status, install, restore, or rescue; other modes take no action.")
    if args.box and args.mode != "box-firmware":
        parser.error("--box is only supported with box-firmware.")
    if args.reboot_host and args.mode not in {"install", "uninstall"}:
        parser.error("--reboot-host is only supported with install and uninstall.")
    if args.require_klipper_idle and args.mode != "complete-host-reboot":
        parser.error("--require-klipper-idle is only supported with complete-host-reboot.")
    if args.operator_approved and args.mode != "complete-host-reboot":
        parser.error("--operator-approved is only supported with complete-host-reboot.")
    return args

def _normalize_command_aliases(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    aliases = {"--uninstall", "--clear-recovery-sentinel"}
    values = list(argv)
    if values and values[0] in aliases:
        return ["install", values[0], *values[1:]]
    return values


def installer_version(bundle_root: Path) -> str:
    manifest = load_manifest(bundle_root / "installer/package.yaml")
    return f"{manifest.package.display_name} installer {manifest.package.version}"


def _system_options_from_args(args: argparse.Namespace) -> SystemOptimizationCliOptions:
    return SystemOptimizationCliOptions(
        disable_ai_detection=args.disable_ai_detection,
        keep_ai_detection=args.keep_ai_detection,
        keep_system_optimizations=args.keep_system_optimizations,
        reboot_host=args.reboot_host,
    )



def _lock_inherited(mode: str, environ: dict[str, str]) -> bool:
    # An earlier release's update check holds only its own lock, so a run it starts takes this release's.
    return mode == "install" and environ.get(LOCK_HELD_ENV) == "1" and not earlier_names.EARLIER_PARENT


def _environment(environ: dict[str, str] | None) -> dict[str, str]:
    """The run's environment, with values set under earlier releases' names under the current ones; for a real run
    the process's environment too, which the runs it starts inherit."""
    env = earlier_names.adopt_earlier_environment(os.environ if environ is None else environ)
    if environ is None:
        os.environ.update({name: env[name] for name in earlier_names.EARLIER_ENVIRONMENT if name in env})
    return env



def resolve_runtime_paths(*, bundle_root: Path, environ: dict[str, str]) -> RuntimePaths:
    printer_data_root = Path(
        environ.get("VO_PRINTER_DATA_ROOT", DEFAULT_PRINTER_DATA_ROOT)
    )
    firmware_manifest_path = Path(
        environ.get("VO_FIRMWARE_MANIFEST", DEFAULT_FIRMWARE_MANIFEST)
    )
    moonraker_url = environ.get("VO_MOONRAKER_URL", DEFAULT_MOONRAKER_URL)
    return RuntimePaths(
        bundle_root=bundle_root,
        installer_root=bundle_root / "installer",
        printer_data_root=printer_data_root,
        config_root=printer_data_root / "config",
        firmware_manifest_path=firmware_manifest_path,
        moonraker_url=moonraker_url,
        lock_path=printer_data_root / ".vo_installer.lock",
        recovery_sentinel_path=printer_data_root / ".vo_recovery_required",
        backup_root=printer_data_root,
        klipper_root=(
            Path(environ["VO_KLIPPER_ROOT"])
            if "VO_KLIPPER_ROOT" in environ
            else None
        ),
        host_reboot_marker_override=(
            Path(environ["VO_HOST_REBOOT_MARKER"])
            if "VO_HOST_REBOOT_MARKER" in environ
            else None
        ),
        boot_id_path_override=(
            Path(environ["VO_BOOT_ID_PATH"])
            if "VO_BOOT_ID_PATH" in environ
            else None
        ),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
