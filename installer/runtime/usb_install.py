"""The USB installer on the touchscreen: what the hand-off package starts after QIDI's Offline Update ran it.

The launcher the hand-off package starts (installer/usb/) runs `bootstrap.py usb --drive <mount>` once QIDI's app has
restarted and Klipper is ready. From its first screen this holds the touchscreen (install_screen.held, with the same
watcher that gives the app back if this process dies) and drives the same install an SSH run does (cli._install):

    checking -> what it found (or update available / up to date) -> each question -> ready -> installing -> done

Nothing changes before Install on the ready screen. A printer that is busy, on firmware this release doesn't support,
or with a board it doesn't admit gets a screen that says why, and the touchscreen goes back to QIDI's app when the
operator quits. When a run ends, its log is copied to the drive before the next screen shows. After an install the
finish screen waits for the drive to come out and restarts the printer; Restart now restarts it with the drive in.

On a firmware the release updates from, the QIDI firmware update the install starts takes the screen over and finishes
the install itself, as it does for an SSH install; it uses QIDI's package from the drive (qidi_package.fetch).

The UI is touch_ui's: ui.show(screen, **fields) draws a screen and returns its buttons, ui.wait(buttons, timeout)
returns the one tapped, ui.update(**fields) redraws what changed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from . import errors, install_flow, install_screen, progress, safety
from .progress_screen import ProgressScreen, clock_text as _clock
from .board_firmware import BoardAdmissionError
from .errors import ActivePrintError, InstallerError, OperationCancelled, PrinterStateError, RollbackFailedError
from .firmware import detect_supported_firmware
from .naming import REPOSITORY_URL
from .sudo import popen_sudo, run_sudo

DRIVE_LOG_NAME = "valve-oil-install.log"  # at the drive's root, where the finish and failure screens say it is
# Written by the hand-off package when it found no restore set for the printer's QIDI firmware, or one that didn't
# match its SHA256SUMS: QIDI's update folder was emptied by the Offline Update and could not be put back.
NO_RESTORE_SET_MARKERS = (Path("/home/qidi/update/.valve-oil-no-restore-set"),)
HANDOFF_STATUS = ".cache/valve-oil/usb/restore"  # in the printer user's home
# Also in the printer user's home: the launcher stopped QIDI's app to show its starting screen (installer/usb/launch.sh),
# and the session takes the app over from it.
TOUCHSCREEN_STOPPED = ".cache/valve-oil/usb/touchscreen-stopped"
SLICER_URL = f"{REPOSITORY_URL}/blob/main/docs/install.md#slicer-start-g-code"
FIRMWARE_DOWNLOAD_URL = "https://wiki.qidi3d.com/en/firmware"
DRIVE_POLL_SECONDS = 1.0

# How a session ended.
QUIT = "quit"
DONE = "done"
FIRMWARE_UPDATE = "firmware_update"

# About how long each part takes, for the ready screen.
INSTALL_MINUTES = 6
BOX_MINUTES = 4
FIRMWARE_UPDATE_MINUTES = 15


class _RestoreRequested(Exception):
    pass


class Session:
    """One USB installer session on the touchscreen; dispatch(argv, front_end=, input_stream=, errors_to=) runs the
    CLI for it in this process (cli._usb)."""

    def __init__(self, *, paths, manifest, env, reporter, ui, hold, drive: Path, dispatch, log_path,
                 run=subprocess.run, urlopen=urllib.request.urlopen, clock=time.monotonic, sleep=time.sleep,
                 no_restore_set_markers=NO_RESTORE_SET_MARKERS):
        self.paths = paths
        self.manifest = manifest
        self.env = env
        self.reporter = reporter
        self.ui = ui
        self.hold = hold
        self.drive = drive
        self.dispatch = dispatch
        self.log_path = log_path
        self.run = run
        self.urlopen = urlopen
        self.clock = clock
        self.sleep = sleep
        self.no_restore_set_markers = no_restore_set_markers
        self.error: BaseException | None = None
        self.firmware = None
        self.cancelled = False
        self.restore_requested = False
        self.started_at: float | None = None
        self.overview = None
        self._screen: ProgressScreen | None = None
        self._last_snapshot = None
        self._footer = f"Valve Oil {manifest.package.version} · USB installer"
        self._update_folder_restored = False
        # The printer the hand-off's zip was for, as its starting screen names it, until the firmware says.
        printer = next((printer for printer in manifest.printers
                        if printer.id == self._handoff_status().get("printer")), None)
        self._where = printer.display_name if printer is not None else "QIDI printer"

    # ---- the session ------------------------------------------------------------------------------------------------

    def loop(self) -> str:
        while True:
            self._show("checking")
            ended = self._check_printer()
            if ended == "again":
                continue
            if ended is not None:
                return ended
            ended = self._install()
            if ended != "again":
                return ended

    def _check_printer(self) -> str | None:
        """None when the install can go ahead; otherwise how the session goes on."""
        try:
            printer, firmware = detect_supported_firmware(self.paths.firmware_manifest_path, self.manifest)
        except InstallerError:
            # QIDI's Offline Update emptied the update folder, and the drive has no files for an unsupported firmware.
            return self._unsupported(emptied=any(marker.exists() for marker in self.no_restore_set_markers))
        self._footer = (f"Valve Oil {self.manifest.package.version} · USB installer · QIDI firmware "
                        f"{firmware}")
        self._where = printer.display_name
        if any(marker.exists() for marker in self.no_restore_set_markers):
            return self._unsupported(emptied=True)
        if not self._update_folder_restored:
            self._restore_update_folder()
        try:
            # The install itself refuses a halted Klipper unless Valve Oil is installed and may be what halted it.
            safety.ensure_printer_idle(self.paths.moonraker_url, urlopen=self.urlopen, wait_seconds=60.0,
                                       allow_halted=True)
        except ActivePrintError as exc:
            return self._busy(exc)
        except PrinterStateError:
            return self._busy(None)
        return None

    def _restore_update_folder(self) -> None:
        """QIDI's Offline Update emptied the update folder; the hand-off package put QIDI's manifest and board images
        back. On the target firmware the system package file comes back too, from QIDI's package on the drive, so the
        folder is as QIDI left it whether the operator installs, quits, or the run fails."""
        from .qidi_package import restore_soc_package_from_drive
        from .sudo import run_sudo

        password = getattr(self.hold, "password", None)
        if password is None:
            return
        copying = []

        def copy_started():
            copying.append(True)
            self._show("checking", text="Copying QIDI's system package from the drive…")

        result = restore_soc_package_from_drive(
            self.paths, self.manifest, environ=self.env, reporter=self.reporter,
            sudo=lambda command: run_sudo(command, run=self.run, password=password, timeout=300),
            copy_started=copy_started)
        self.reporter.debug(event="usb.update_folder", result=result.result)
        self._update_folder_restored = True
        if copying:
            self._show("checking")

    def _install(self) -> str:
        self.error = None
        self.firmware = None
        self.cancelled = False
        self.restore_requested = False
        self.started_at = None
        rc = self.dispatch(["install", "--plain"], front_end=self)
        if self._screen is not None:
            self._last_snapshot = self._screen.snapshot
            self._screen.stop()
            self._screen = None
        if self.restore_requested:
            return self._restore_backup()
        if self.cancelled and self.error is None:
            return QUIT
        if rc == 0 and self.error is None:
            if self.firmware is not None and self.firmware.handled:
                self.copy_log_to_drive()
                return FIRMWARE_UPDATE
            return DONE
        return self._failed(self.error)

    # ---- the front end cli._install calls ---------------------------------------------------------------------------

    def wrap(self, reporter):
        session = self

        class _Reporter:
            def __getattr__(self, name):
                return getattr(reporter, name)

            def emit_error(self, error):
                session.error = error
                reporter.emit_error(error)

        return _Reporter()

    def ask(self, overview) -> install_flow.Answers:
        """The screens before the run: what it found, the questions, and the summary. Raises OperationCancelled to
        quit, or after Restore a backup."""
        self.overview = overview
        self._where = overview.printer_name
        try:
            if overview.installed_version is None or overview.new_install:
                answers = self._new_install(overview)
            elif overview.installed_version != overview.package_version:
                answers = self._update_available(overview)
            else:
                answers = self._up_to_date(overview)
        except _RestoreRequested:
            self.restore_requested = True
            self.cancelled = True
            raise OperationCancelled("restore a backup")
        if answers is None:
            self.cancelled = True
            raise OperationCancelled("quit")
        self.reporter.debug(event="usb.answers", **answers.to_json())
        self.started_at = self.clock()
        self._screen = ProgressScreen(self.ui, reporter=self.reporter, where=self._where,
                                      updating=not overview.new_install, footer=self._footer, clock=self.clock)
        self._screen.start()
        return answers

    def firmware_update(self, result) -> None:
        self.firmware = result

    def reading_boards(self) -> None:
        """The install reads every board before it asks anything, which takes up to a minute."""
        self._show("checking", text="Reading the printer's board firmware…")

    # ---- screens before the run --------------------------------------------------------------------------------------

    def _new_install(self, overview):
        rows = [("Printer", overview.printer_name)]
        if overview.firmware_update:
            rows.append(("QIDI firmware", f"{overview.firmware}", f"updated to {overview.target_firmware} first"))
            text = (f"This Valve Oil runs on QIDI firmware {overview.target_firmware}, so the installer updates the "
                    "printer to it first, from this drive. That adds about 15 minutes.")
        else:
            rows.append(("QIDI firmware", overview.firmware, "supported"))
            text = (f"A few questions first, then the install runs by itself: about {INSTALL_MINUTES} minutes"
                    + (f", plus {BOX_MINUTES} minutes for each QIDI Box." if overview.box_count else "."))
        if overview.box_count:
            rows.append(("QIDI Box", f"{overview.box_count} connected"))
        rows.append(("Valve Oil", "Not installed"))
        tapped = self._ask_screen("found", ("quit", "start"), title=f"Install Valve Oil {overview.package_version}",
                                  rows=rows, text=text, firmware_first=overview.firmware_update)
        if tapped == "quit":
            return None
        return self._questions(overview, overview.questions)

    def _update_available(self, overview):
        rows = [("Installed", overview.installed_version),
                ("On this drive", overview.package_version, "newer"),
                ("Settings kept", self._settings_text(overview))]
        tapped = self._ask_screen("update", ("settings", "update", "quit"), title="Update available", rows=rows,
                                  text="Your settings and configuration are kept. Your configuration is backed up "
                                       "first.")
        if tapped == "quit":
            return None
        if tapped == "settings":
            return self._questions(overview, self._settings_questions(overview))
        return install_flow.Answers()

    def _up_to_date(self, overview):
        from .backup import list_installer_backups

        backups = list_installer_backups(self.paths.printer_data_root,
                                         install_label_prefix=self.manifest.backup.label_prefix)
        rows = [("Installed", overview.installed_version), ("Settings", self._settings_text(overview))]
        if backups:
            rows.append(("Last backup", backups[-1].display_created_at))
        buttons = ("restore", "settings", "again", "quit") if backups else ("settings", "again", "quit")
        tapped = self._ask_screen("up_to_date", buttons, title="Valve Oil is up to date", rows=rows,
                                  restore=bool(backups))
        if tapped == "quit":
            return None
        if tapped == "restore":
            raise _RestoreRequested()
        if tapped == "settings":
            return self._questions(overview, self._settings_questions(overview))
        return install_flow.Answers()

    def _settings_questions(self, overview):
        questions = [install_flow.QUESTIONS[install_flow.AUTO_UPDATES]]
        if overview.ai_detection_on:
            questions.append(install_flow.QUESTIONS[install_flow.AI_DETECTION])
        return tuple(questions)

    def _settings_text(self, overview) -> str:
        parts = ["Automatic updates " + ("on" if overview.auto_updates_on else "off"),
                 "AI detection " + ("on" if overview.ai_detection_on else "off")]
        if overview.box_count:
            parts.append("Box support " + ("on" if overview.box_support_on else "off"))
        return " · ".join(parts)

    def _questions(self, overview, questions):
        """Each question on its own screen, then the summary; Back goes to the screen before. None to quit."""
        values: dict[str, bool] = {}
        index = 0
        while True:
            if index < len(questions):
                question = questions[index]
                tapped = self._ask_screen("question", ("back", "no", "yes"), title=question.title,
                                          text=question.text, no_label=question.no, yes_label=question.yes,
                                          header_step=f"Question {index + 1} of {len(questions)}")
                if tapped == "back":
                    if index == 0:
                        return None
                    index -= 1
                    continue
                values[question.id] = tapped == "yes"
                index += 1
                continue
            answers = install_flow.Answers(auto_updates=values.get(install_flow.AUTO_UPDATES),
                                           disable_ai_detection=values.get(install_flow.AI_DETECTION),
                                           asked=tuple(values))
            minutes = self._minutes(overview)
            tapped = self._ask_screen("ready", ("back", "install"),
                                      title="Ready to install" if overview.new_install else "Ready to update",
                                      rows=install_flow.summary_rows(overview, answers),
                                      text=f"Backed up first. About {minutes} minutes. Keep the printer"
                                           + (" and Boxes on." if overview.box_count else " on."))
            if tapped == "back":
                if not questions:
                    return None
                index = len(questions) - 1
                continue
            return answers

    def _minutes(self, overview) -> int:
        boxes = sum(1 for board in overview.boards if board.startswith("Box "))
        return (INSTALL_MINUTES + BOX_MINUTES * boxes
                + (FIRMWARE_UPDATE_MINUTES if overview.firmware_update else 0))

    # ---- after the run -----------------------------------------------------------------------------------------------

    def finish(self) -> None:
        """The finish screen, once the log is on the drive: restart when the drive comes out, or on Restart now."""
        self.copy_log_to_drive()
        elapsed = _clock(self.clock() - self.started_at) if self.started_at is not None else None
        updated = self.overview is not None and not self.overview.new_install
        buttons = self._show(
            "done", buttons=("restart",),
            title="Valve Oil is updated" if updated else "Valve Oil is installed",
            lines=["Take out the USB drive. The log is already saved on it.",
                   "The printer restarts by itself. Wait for the home screen.",
                   f"Open Fluidd at {_fluidd_url()}",
                   "Set up your slicer: scan the code, or see github.com/thelegendtubaguy/valve-oil"],
            fluidd_url=_fluidd_url(), qr_url=SLICER_URL,
            waiting_text="Waiting for the USB drive to be taken out…",
            header_step=f"Finished in {elapsed}" if elapsed else None)
        while True:
            tapped = self.ui.wait(buttons, timeout=DRIVE_POLL_SECONDS)
            if tapped == "restart" or not self._drive_present():
                self.reporter.debug(event="usb.restart", drive_present=self._drive_present())
                self._restart()
                return

    def _drive_present(self) -> bool:
        return os.path.ismount(self.drive) or (self.drive / "QD_Update").is_dir()

    def _restart(self) -> None:
        from . import boot_path
        from .sudo import run_sudo

        while not boot_path.boot_files_allow_reboot(self.paths, reporter=self.reporter, environ=self.env):
            if self._not_restarted(boot_path.describe(boot_path.reboot_problems(self.paths, self.env))) == QUIT:
                return
        password = getattr(self.hold, "password", None) or self.env.get("TLTG_OPTIMIZED_SUDO_PASSWORD") or ""
        run_sudo(["systemctl", "reboot"], run=self.run, password=password, timeout=60)
        self.sleep(60)  # the reboot ends this process

    def _not_restarted(self, detail: str) -> str:
        """The printer would stop at its boot logo if it restarted: say so instead of restarting. Try again checks again,
        as after the files were repaired over SSH; Quit gives the screen back to QIDI's app, still not restarted."""
        from . import boot_path

        self.copy_log_to_drive()
        while True:
            tapped = self._ask_screen(
                "failed", ("quit", "view_log", "again"), title="The printer wasn't restarted",
                text=["Valve Oil is installed, but the printer would stop at its boot logo if it restarted now:",
                      ("bad", detail.rstrip(".") + "."),
                      "Don't turn the printer off. Run **~/valve-oil/install.sh** over SSH, which repairs this, then "
                      f"tap Try again. Or follow **{boot_path.RECOVERY_DOC_URL.split('://', 1)[-1]}**."],
                log_name=None)
            if tapped == "view_log":
                self._show_log()
                continue
            return QUIT if tapped == "quit" else "again"

    def copy_log_to_drive(self) -> Path | None:
        """Copy the run log to the drive's root and flush it; the path on the drive, or None."""
        if self.log_path is None:
            return None
        target = self.drive / DRIVE_LOG_NAME
        try:
            for handler in (getattr(self.reporter, "log", None),):
                if handler is not None and hasattr(handler, "_file") and handler._file is not None:
                    handler._file.flush()
            shutil.copyfile(self.log_path, target)
            with open(target, "rb+") as handle:
                os.fsync(handle.fileno())
            os.sync()
        except OSError as exc:
            self.reporter.debug(event="usb.log.copy_failed", error=str(exc))
            return None
        return target

    def _failed(self, error) -> str:
        self.copy_log_to_drive()
        if isinstance(error, BoardAdmissionError) and getattr(error, "admission", None) is not None:
            return self._board_not_supported(error.admission)
        if isinstance(error, ActivePrintError):
            return self._busy(error)
        snapshot = self._last_snapshot
        step = (f"Step {snapshot.index}, {snapshot.step}, did not finish"
                if snapshot is not None and snapshot.step else "The install did not finish")
        message = getattr(error, "message", None) or str(error or "the installer stopped")
        # runner._execute_install says on the error what the install left; before it starts, nothing was written.
        outcome = getattr(error, "install_outcome", None)
        if isinstance(error, RollbackFailedError):
            state = "Some changes could not be put back. Don't turn the printer off; the log says what to do."
        elif outcome == errors.OUTCOME_PUT_BACK:
            state = "Everything was put back the way it was. Your configuration backup is kept."
        elif outcome == errors.OUTCOME_UNCHANGED or (
                outcome is None and (snapshot is None or snapshot.step in (None, progress.CHECK, progress.BACKUP))):
            state = "Nothing was changed."
        else:
            state = "Valve Oil is installed; this step runs again when you try again."
        elapsed = _clock(self.clock() - self.started_at) if self.started_at is not None else None
        while True:
            tapped = self._ask_screen(
                "failed", ("quit", "view_log", "again"), title="Install stopped",
                text=f"{step}: {message.rstrip('.')}. {state}", log_name=DRIVE_LOG_NAME,
                header_step=f"Stopped after {elapsed}" if elapsed else None)
            if tapped == "quit":
                return QUIT
            if tapped == "again":
                return "again"
            self._show_log()

    def _busy(self, error) -> str:
        targets = safety.heater_targets(self.paths.moonraker_url, urlopen=self.urlopen)
        heating = {name: target for name, target in targets.items() if target}
        if heating:
            text = "A heater is on. Installing restarts Klipper, and that isn't safe while something is heating."
        elif error is not None:
            text = "A print is running or paused. Install once it has finished."
        else:
            text = "The printer's state can't be read yet. Klipper may still be starting."
        rows = [(_heater_name(name), f"{target:g} °C target" if target else "Off",
                 "turn off" if target else "") for name, target in targets.items()]
        tapped = self._ask_screen("busy", ("quit", "again"), title="Can't install right now", text=text,
                                  rows=rows, note="Nothing has been changed.")
        return QUIT if tapped == "quit" else "again"

    def _unsupported(self, *, emptied: bool = False) -> str:
        from .firmware import detect_firmware_version_best_effort

        found = (detect_firmware_version_best_effort(self.paths.firmware_manifest_path)
                 or self._handoff_status().get("qidi_version") or "unknown")
        supported_now = emptied and self._firmware_supported()
        supported = sorted({version for printer in self.manifest.printers for version in printer.firmware})
        rows = [("This printer", f"QIDI firmware {found}"),
                (f"Valve Oil {self.manifest.package.version}",
                 f"Supports {supported[0]} to {supported[-1]}" if supported else "")]
        text = ("Nothing has been changed. A newer Valve Oil may support it: download the latest USB installer from "
                "github.com/thelegendtubaguy/valve-oil and try again.")
        if emptied and not supported_now:
            text = ("Nothing else has been changed, but QIDI's Offline Update emptied the printer's update folder. To "
                    f"get its files back, download QIDI firmware {found} for this printer from {FIRMWARE_DOWNLOAD_URL} "
                    "and unzip it into /home/qidi/update. A newer Valve Oil may support this firmware: download the "
                    "latest USB installer from github.com/thelegendtubaguy/valve-oil.")
        elif emptied:
            text = ("QIDI's Offline Update emptied the printer's update folder, and this drive has no copy of QIDI's "
                    f"files for firmware {found}. Nothing else was changed. To get them back, download QIDI firmware "
                    f"{found} for this printer from {FIRMWARE_DOWNLOAD_URL} and unzip it into /home/qidi/update.")
        self._ask_screen("unsupported", ("back_to_qidi",), title="QIDI's update files are missing"
                         if supported_now else "This QIDI firmware isn't supported yet", rows=rows, text=text)
        return QUIT

    def _handoff_status(self) -> dict[str, str]:
        """What the hand-off package recorded (installer/usb/postinst): the installed QIDI version even when QIDI's
        manifest could not be put back."""
        path = self.paths.printer_data_root.parent / HANDOFF_STATUS
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def _firmware_supported(self) -> bool:
        try:
            detect_supported_firmware(self.paths.firmware_manifest_path, self.manifest)
        except InstallerError:
            return False
        return True

    def _board_not_supported(self, admission) -> str:
        status = admission.unadmitted[0]
        name = {"thr": "toolhead board", "mcu": "mainboard"}.get(status.board, status.label)
        from .board_firmware import ISSUE_FORM

        self._ask_screen("board_not_supported", ("quit", "back_to_qidi"), title=f"This {name} isn't supported yet",
                         rows=[("Board", {"thr": "Toolhead (THR)", "mcu": "Mainboard"}.get(status.board, status.label)),
                               ("Found", status.detail or "A firmware this release doesn't know")],
                         text="Nothing was changed. Scan the code to report it. The report is filled in with this "
                              "printer's details, so a release can add support for it.",
                         qr_url=status.issue_url or ISSUE_FORM)
        return QUIT

    def _restore_backup(self) -> str:
        from .backup import list_installer_backups
        from .restore_helper import CONFIRMATION_TOKEN

        backups = tuple(reversed(list_installer_backups(self.paths.printer_data_root,
                                                        install_label_prefix=self.manifest.backup.label_prefix)))[:4]
        if not backups:
            return "again"
        buttons = ("back",) + tuple(f"backup_{index}" for index in range(len(backups)))
        tapped = self._ask_screen("backups", buttons, title="Restore a backup",
                                  rows=[(backup.display_created_at, backup.label) for backup in backups])
        if tapped == "back":
            return "again"
        chosen = backups[int(tapped.split("_")[1])]
        tapped = self._ask_screen("confirm", ("back", "ok"), title="Restore this backup?",
                                  text=f"Your printer configuration is replaced by the backup from "
                                       f"{chosen.display_created_at}. The configuration you have now is not kept.",
                                  ok_label="Restore")
        if tapped != "ok":
            return "again"
        self.error = None
        self._show("checking", text="Restoring the backup…", note="Klipper restarts when it's done.")
        import io

        rc = self.dispatch(["restore-backup", "--plain", "--backup", str(chosen.path)], front_end=None,
                           input_stream=io.StringIO(CONFIRMATION_TOKEN + "\n"), errors_to=self)
        self.copy_log_to_drive()
        if rc != 0 or self.error is not None:
            return self._failed(self.error)
        self._ask_screen("confirm", ("ok",), title="Backup restored",
                         text=f"The configuration from {chosen.display_created_at} is back.", ok_label="Done")
        return "again"

    # ---- drawing -----------------------------------------------------------------------------------------------------

    def _show(self, screen: str, *, buttons=None, **fields):
        fields.setdefault("footer", self._footer)
        fields.setdefault("where", self._where)
        try:
            shown = self.ui.show(screen, **fields)
        except Exception as exc:  # a screen that can't be drawn is logged; the run goes on
            self.reporter.debug(event="usb.screen.failed", screen=screen, error=f"{type(exc).__name__}: {exc}")
            shown = ()
        self.reporter.debug(event="usb.screen", screen=screen)
        return tuple(shown) if shown else tuple(buttons or ())

    def _ask_screen(self, screen: str, buttons, **fields) -> str:
        """Show a screen and return the button tapped; Log, wherever it is, shows the log and comes back."""
        while True:
            shown = self._show(screen, buttons=buttons, **fields)
            choices = tuple(dict.fromkeys(tuple(buttons) + tuple(button for button in shown if button == "log")))
            tapped = self.ui.wait(choices)
            self.reporter.debug(event="usb.tap", screen=screen, button=tapped)
            if tapped == "log":
                self._show_log()
                continue
            if tapped in buttons:
                return tapped

    def _show_log(self) -> None:
        lines = _tail(self.log_path, 12)
        self._show("log", buttons=("close",), lines=lines, title="Installer log")
        while self.ui.wait(("close",)) != "close":
            pass


def run(*, paths, manifest, env, reporter, drive: Path, dispatch, ui=None, run=subprocess.run,
        urlopen=urllib.request.urlopen) -> int:
    """The USB installer session. dispatch(argv, front_end=None, input_stream=None) runs the CLI in this process."""
    from . import touch_ui

    log = getattr(reporter, "log", None)
    log_path = getattr(log, "path", None)
    ended = QUIT
    with install_screen.held(paths=paths, reporter=reporter, environ=env, require_idle=False,
                             progress_screen=False, run=run, urlopen=urlopen,
                             stopped_marker=paths.printer_data_root.parent / TOUCHSCREEN_STOPPED) as hold:
        password = hold.password
        if ui is None and password is not None:
            ui = touch_ui.open(reporter, sudo=lambda command: run_sudo(command, run=run, password=password, timeout=60),
                               root=lambda command: popen_sudo(command, password=password))
        if ui is None or not getattr(ui, "touch_available", True):
            # Without the screen, or touches, nobody can answer: QIDI's app gets the screen back.
            reporter.line("The USB installer needs the touchscreen, which could not be used. Nothing was changed.")
            if ui is not None:
                ui.close()
            _copy_plain(log_path, drive)
            return 1
        try:
            session = Session(paths=paths, manifest=manifest, env=env, reporter=reporter, ui=ui, hold=hold,
                              drive=drive, dispatch=dispatch, log_path=log_path, run=run, urlopen=urlopen)
            ended = session.loop()
            reporter.debug(event="usb.ended", how=ended)
            session.copy_log_to_drive()
            if ended == DONE:
                session.finish()
        finally:
            try:
                ui.close()
            except Exception:
                pass
    return 0


def _copy_plain(log_path, drive: Path) -> None:
    if log_path is None:
        return
    try:
        shutil.copyfile(log_path, drive / DRIVE_LOG_NAME)
        os.sync()
    except OSError:
        pass


def _tail(path, count: int) -> list[str]:
    if path is None:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [_short(line) for line in lines[-count:]]


def _short(line: str) -> str:
    # The log's lines start with a date; the screen shows the time only.
    parts = line.split(" ", 2)
    text = parts[1] + " " + parts[2] if len(parts) == 3 and len(parts[0]) == 10 else line
    return text if len(text) <= 90 else text[:89] + "…"


def _heater_name(name: str) -> str:
    if name == "heater_bed":
        return "Bed"
    if name.startswith("extruder"):
        return "Nozzle"
    if "chamber" in name:
        return "Chamber"
    if "box" in name.lower() or "dryer" in name.lower():
        return "Box dryer"
    return name.split(" ")[-1]


def _fluidd_url() -> str:
    """The printer's address, for the finish screen; its host name when no address can be read."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no packet is sent; this picks the address of the default route
            return f"http://{probe.getsockname()[0]}"
    except OSError:
        return f"http://{socket.gethostname()}.local"
