from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import os
import shutil
import signal
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from installer.runtime import thr_firmware
from installer.runtime.auto_update import LOCK_HELD_ENV
from installer.runtime.cli import main, resolve_runtime_paths
from installer.runtime.reporter import create_reporter
from installer.runtime.safety import THR_RECOVERY_MARKER, ensure_no_recovery_sentinel
from installer.runtime.errors import InstallerError
from installer.tests.helpers import REPO_ROOT, build_env, temp_path
from scripts import flash_thr_firmware as protocol
from scripts.build_thr_firmware import patch_thr


def superseded_image(stock):
    return b'\x01' + stock[1:]


class Printer:
    def __init__(self, root, manifest, stock, boot, *, patched=False, superseded=False, failure=None, timer=True):
        self.root, self.manifest = root, manifest
        self.stock, self.boot = stock, boot
        self.image = patch_thr(stock, manifest) if patched else superseded_image(stock) if superseded else stock
        self.transfer_backups = []
        self.failure = failure
        self.states = {s: 'active' for s in protocol.SERVICES}
        self.states['qidi-client.service'] = 'inactive'
        if not timer:
            del self.states['tltg-optimized-auto-update.timer']
        self.original = dict(self.states)
        self.transfers = 0
        self.recovery_requested = None
        self.snapshots = 0
        self.state = 'standby'
        self.idle_state = 'Ready'
        self.restarting_queries = 0
        self.targets = {'extruder': 0, 'heater_bed': 0, 'heater_generic chamber': 0, 'heater_generic extra': 0}

    def urlopen(self, *args, **kwargs):
        if self.restarting_queries:
            self.restarting_queries -= 1
            raise OSError('Klippy not connected')
        status = {'print_stats': {'state': self.state}, 'webhooks': {'state': 'ready'},
                  'idle_timeout': {'state': self.idle_state}, 'heaters': {'available_heaters': list(self.targets)}}
        status.update({name: {'target': value} for name, value in self.targets.items()})
        return io.BytesIO(json.dumps({'result': {'status': status}}).encode())

    def run(self, command, **kwargs):
        command = list(command)
        def result(code=0, output=''):
            return subprocess.CompletedProcess(command, code, stdout=output, stderr='')
        if command[:4] == ['sudo', '-S', '-p', '']:
            command = command[4:]
        if command == ['-v']:
            return result()
        if command[:2] == ['systemctl', 'show']:
            service = command[2]
            if service not in self.states:
                return result(1, 'LoadState=not-found\nActiveState=inactive\n')
            return result(output='LoadState=loaded\nActiveState=' + self.states[service] + '\n')
        if command[:1] == ['systemctl'] and command[1] in ('stop', 'start'):
            if self.failure == 'restore' and command[1] == 'start' and command[2] == 'klipper.service':
                return result(1)
            self.states[command[2]] = 'inactive' if command[1] == 'stop' else 'active'
            return result()
        if command[1].endswith('/scripts/flash_thr_firmware.py'):
            operation = command[2]
            if operation == 'snapshot':
                self.snapshots += 1
                destination = Path(command[command.index('--output-dir') + 1])
                destination.mkdir()
                image, boot = self.image, self.boot
                if self.failure == 'unknown-app' and self.snapshots == 1:
                    image = image[:-1] + bytes([image[-1] ^ 1])
                if self.failure == 'unknown-boot' and self.snapshots == 1:
                    boot = boot[:-1] + bytes([boot[-1] ^ 1])
                if self.failure == 'readback' and self.snapshots == 2:
                    image = self.stock
                supported = protocol.digest(boot) in protocol.thr_bootloaders.ADMITTED_BOOTLOADERS
                region = image + b'\xff' * (0x18000 - len(image)) if not supported else None
                protocol.save_snapshot(destination, boot, image, application_region=region)
                return result(0 if supported else protocol.UNSUPPORTED_BOOTLOADER)
            if operation == 'transfer':
                self.transfers += 1
                if getattr(self, 'on_transfer', None):
                    self.on_transfer(kwargs)
                self.recovery_requested = '--recover-to-stock' in command
                backup = Path(command[command.index('--backup-dir') + 1])
                self.transfer_backups.append(protocol.digest((backup / 'application.bin').read_bytes()))
                if self.failure == 'interrupt':
                    raise KeyboardInterrupt()
                if self.failure == 'transfer':
                    return result(1)
                if self.failure == 'fell-back':
                    return result(protocol.FELL_BACK)
                if self.failure == 'end-unconfirmed-stock':
                    return result(protocol.END_UNCONFIRMED)
                self.image = Path(command[command.index('--image') + 1]).read_bytes()
                return result(protocol.END_UNCONFIRMED if self.failure == 'end-unconfirmed' else 0)
        raise AssertionError('Unexpected external operation: ' + str(command))


class ThrFirmwareFlowTests(unittest.TestCase):
    def fixture(self, *, profile=protocol.thr_bootloaders.FIRST, **kwargs):
        root = temp_path('thr-firmware-flow-')
        bundle = root / 'bundle'
        (bundle / 'scripts').mkdir(parents=True)
        (bundle / 'firmware').mkdir()
        for name in ('flash_thr_firmware.py', 'thr_bootloaders.py', 'build_thr_firmware.py', 'build_box_firmware.py'):
            shutil.copy2(REPO_ROOT / 'scripts' / name, bundle / 'scripts' / name)
        manifest = json.loads((REPO_ROOT / 'firmware/max4-thr-valve-oil-v1.json').read_text())
        stock = bytearray(manifest['thr']['size'])
        for entry in manifest['thr']['patches']:
            if 'before' in entry:
                start = int(entry['offset'], 0)
                value = bytes.fromhex(entry['before'])
                stock[start:start + len(value)] = value
        stock = bytes(stock)
        for entry in manifest['thr']['patches']:
            if 'before_sha256' in entry:
                start = int(entry['offset'], 0)
                entry['before_sha256'] = hashlib.sha256(stock[start:start + len(bytes.fromhex(entry['after']))]).hexdigest()
        manifest['thr']['superseded'][0]['sha256'] = hashlib.sha256(superseded_image(stock)).hexdigest()
        image = bytearray(stock)
        for entry in manifest['thr']['patches']:
            start = int(entry['offset'], 0)
            value = bytes.fromhex(entry['after'])
            image[start:start + len(value)] = value
        manifest['thr']['stock_sha256'] = hashlib.sha256(stock).hexdigest()
        manifest['thr']['patched_sha256'] = hashlib.sha256(image).hexdigest()
        (bundle / 'firmware/max4-thr-valve-oil-v1.json').write_text(json.dumps(manifest))
        (bundle / 'installer').mkdir()
        shutil.copy2(REPO_ROOT / 'installer/package.yaml', bundle / 'installer/package.yaml')
        data = root / 'printer_data'
        (data / 'config').mkdir(parents=True)
        shutil.copy2(REPO_ROOT / 'config/printer.cfg', data / 'config/printer.cfg')
        (data / 'firmware_manifest.json').write_text('{"SOC":{"version":"01.01.07.01"}}')
        python = root / 'klippy-env/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('transport fixture')
        paths = resolve_runtime_paths(bundle_root=bundle, environ=build_env(data, moonraker_url='http://printer.invalid/printer/objects/query'))
        boot = bytes(0x8000)
        printer = Printer(root, manifest, stock, boot, **kwargs)
        printer.profile = replace(profile, sha256=protocol.digest(boot))
        reporter = create_reporter(io.StringIO(), prefer_plain=True, bundle_root=REPO_ROOT, environ={})
        return paths, printer, reporter

    def execute(self, paths, printer, reporter, *, admit=True, **kwargs):
        profiles = protocol.thr_bootloaders
        with patch.dict(profiles.PROFILES, {printer.profile.sha256: printer.profile}), patch.object(
            profiles, 'ADMITTED_BOOTLOADERS', frozenset((printer.profile.sha256,)) if admit else frozenset(),
        ):
            return thr_firmware.flash_thr_firmware(
                paths=paths, reporter=reporter, run=printer.run, urlopen=printer.urlopen,
                sleep=lambda seconds: None, clock=itertools.count(0, 10).__next__, **kwargs,
            )

    def test_stock_and_already_patched_boards_preserve_evidence_and_service_states(self):
        for profile in protocol.thr_bootloaders.PROFILES.values():
            for patched, timer in itertools.product((False, True), repeat=2):
                with self.subTest(profile=profile.sha256, patched=patched, timer=timer):
                    paths, printer, reporter = self.fixture(profile=profile, patched=patched, timer=timer)
                    config = (paths.config_root / 'printer.cfg').read_bytes()
                    if not timer:
                        printer.idle_state = 'Idle'
                    printer.restarting_queries = 3
                    outcome = self.execute(paths, printer, reporter)
                    self.assertEqual(outcome, thr_firmware.CURRENT if patched else thr_firmware.FLASHED)
                    self.assertEqual(printer.transfers, 0 if patched else 1)
                    self.assertEqual(printer.states, printer.original)
                    self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
                    directories = list((paths.printer_data_root / '.tltg_thr_firmware_backups').iterdir())
                    self.assertEqual(len(directories), 1)
                    evidence = json.loads((directories[0] / 'verification.json').read_text())
                    self.assertTrue(evidence['verified'])
                    self.assertEqual(evidence['application_sha256'], printer.manifest['thr']['patched_sha256'])
                    self.assertEqual((directories[0] / 'before/bootloader.bin').read_bytes(), printer.boot)
                    self.assertEqual((paths.config_root / 'printer.cfg').read_bytes(), config)
                    if not patched:
                        self.assertEqual((directories[0] / 'before/application.bin').read_bytes(), printer.stock)
                        self.execute(paths, printer, reporter)
                        self.assertEqual(printer.transfers, 1)
                        self.assertEqual(printer.states, printer.original)

    def test_earlier_fix_is_updated_from_qidi_stock_image(self):
        paths, printer, reporter = self.fixture(superseded=True)
        with self.assertRaises(thr_firmware.ThrFirmwareError):
            self.execute(paths, printer, reporter)
        self.assertEqual((printer.transfers, printer.states), (0, printer.original))
        self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
        update = paths.printer_data_root.parent / 'update'
        update.mkdir()
        (update / 'QD_MAX4_THR_02.02.01.08_20260303.bin').write_bytes(printer.stock)
        self.execute(paths, printer, reporter)
        self.assertEqual(printer.transfers, 1)
        self.assertEqual(printer.transfer_backups, [printer.manifest['thr']['stock_sha256']])
        self.assertEqual(protocol.digest(printer.image), printer.manifest['thr']['patched_sha256'])
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())

    def test_unsafe_preconditions_and_unknown_images_never_program(self):
        for case in ('printing', 'paused', 'unknown-state', 'chamber-hot', 'extra-heater-hot', 'malformed-target', 'unknown-app', 'never-ready'):
            with self.subTest(case=case):
                paths, printer, reporter = self.fixture(failure=case)
                if case in ('printing', 'paused', 'unknown-state'):
                    printer.state = case
                if case == 'chamber-hot':
                    printer.targets['heater_generic chamber'] = 60
                if case == 'extra-heater-hot':
                    printer.targets['heater_generic extra'] = 40
                if case == 'never-ready':
                    printer.restarting_queries = 1000
                if case == 'malformed-target':
                    printer.targets['extruder'] = False
                with self.assertRaises(thr_firmware.ThrFirmwareError):
                    self.execute(paths, printer, reporter)
                self.assertEqual(printer.transfers, 0)
                self.assertEqual(printer.states, printer.original)
                self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())

    def test_unrecognized_and_unadmitted_bootloaders_capture_without_firmware_blocker(self):
        for failure, profile, admit in (
            ('unknown-boot', protocol.thr_bootloaders.FIRST, True),
            (None, protocol.thr_bootloaders.SECOND, False),
        ):
            with self.subTest(failure=failure, profile=profile.sha256):
                paths, printer, reporter = self.fixture(failure=failure, profile=profile)
                config = (paths.config_root / 'printer.cfg').read_bytes()
                self.execute(paths, printer, reporter, admit=admit)
                self.assertEqual(printer.transfers, 0)
                self.assertEqual(printer.states, printer.original)
                self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
                self.assertEqual((paths.config_root / 'printer.cfg').read_bytes(), config)
                directory, = (paths.printer_data_root / '.tltg_thr_firmware_backups').iterdir()
                capture = directory / 'before'
                self.assertEqual((capture / 'application-region.bin').stat().st_size, 0x18000)
                for path in capture.iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                summary = json.loads((capture / 'report.json').read_text())
                self.assertFalse(summary['supported_bootloader'])
                self.assertEqual(len(summary['bootloader_page_sha256']), 16)
                self.assertFalse((directory / 'verification.json').exists())
                self.assertIn('THR firmware was not changed', reporter.stream.getvalue())
                self.assertIn(str(capture / 'report.json'), reporter.stream.getvalue())
                ensure_no_recovery_sentinel(paths.recovery_sentinel_path)

    def test_unverified_service_restoration_retains_blocker(self):
        paths, printer, reporter = self.fixture(failure='restore')
        with self.assertRaises(thr_firmware.ThrFirmwareError):
            self.execute(paths, printer, reporter, admit=False)
        self.assertTrue((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
        self.assertEqual(printer.transfers, 0)
        # Klipper wouldn't start; the services after it are started anyway.
        self.assertEqual((printer.states['klipper.service'], printer.states['tltg-optimized-auto-update.timer']),
                         ('inactive', 'active'))

    def test_the_terminal_going_away_or_ctrl_c_does_not_stop_a_flash(self):
        paths, printer, reporter = self.fixture()
        seen = []

        def hang_up(kwargs):
            seen.append((kwargs.get('start_new_session'), signal.getsignal(signal.SIGHUP)))
            reporter.stream.close()  # the SSH session is gone
            os.kill(os.getpid(), signal.SIGINT)  # and Ctrl-C was pressed

        printer.on_transfer = hang_up
        interrupt = signal.getsignal(signal.SIGINT)
        try:
            outcome = self.execute(paths, printer, reporter)
        except KeyboardInterrupt:
            self.fail('Ctrl-C stopped the flash mid-write')
        self.assertEqual((outcome, seen), (thr_firmware.FLASHED, [(True, signal.SIG_IGN)]))
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
        self.assertIs(signal.getsignal(signal.SIGINT), interrupt)  # Ctrl-C works again afterwards

    def test_tool_transfer_ignores_the_terminal_until_it_ends(self):
        paths, printer, reporter = self.fixture()
        backup = paths.printer_data_root / 'backup'
        backup.mkdir()
        protocol.save_snapshot(backup, printer.boot, printer.stock)
        image = paths.printer_data_root / 'image.bin'
        image.write_bytes(patch_thr(printer.stock, printer.manifest))
        seen = []

        def reset(*args, **kwargs):
            seen.append((signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGINT)))
            raise ValueError('stop before the bootloader')

        profiles = protocol.thr_bootloaders
        with patch.object(protocol, 'MANIFEST', paths.bundle_root / 'firmware/max4-thr-valve-oil-v1.json'), \
                patch.dict(profiles.PROFILES, {printer.profile.sha256: printer.profile}), \
                patch.object(profiles, 'ADMITTED_BOOTLOADERS', frozenset((printer.profile.sha256,))), \
                patch.object(protocol, 'require_ownership'), patch.object(protocol, 'application_session', side_effect=reset), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(protocol.main(['transfer', '--image', str(image), '--backup-dir', str(backup), '--reset',
                                            '--confirm-write', '--printer-idle-confirmed']), 1)
        self.assertEqual(seen, [(signal.SIG_IGN, signal.SIG_IGN)])
        self.assertIsNot(signal.getsignal(signal.SIGINT), signal.SIG_IGN)

    def test_ambiguous_writes_retain_blocker_and_do_not_roll_back_configuration(self):
        for failure in ('transfer', 'readback', 'interrupt'):
            with self.subTest(failure=failure):
                paths, printer, reporter = self.fixture(failure=failure)
                config = paths.printer_data_root / 'committed-config'
                config.write_text('verified configuration')
                error = KeyboardInterrupt if failure == 'interrupt' else thr_firmware.ThrFirmwareError
                with self.assertRaises(error):
                    self.execute(paths, printer, reporter)
                self.assertTrue((paths.printer_data_root / THR_RECOVERY_MARKER).is_file())
                self.assertTrue(all(state == 'inactive' for state in printer.states.values()))
                self.assertEqual(config.read_text(), 'verified configuration')
                self.assertIn('#*# shaper_freq_x', (paths.config_root / 'printer.cfg').read_text())
                with self.assertRaises(InstallerError):
                    ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
                output = io.StringIO()
                self.assertEqual(main(['install', '--yes', '--plain'], bundle_root=REPO_ROOT, environ=build_env(paths.printer_data_root, moonraker_url='http://printer.invalid'), stream=output), 1)
                self.assertEqual(config.read_text(), 'verified configuration')

    def test_transfer_recovery_is_requested_only_for_recoverable_bootloaders(self):
        for profile in protocol.thr_bootloaders.PROFILES.values():
            with self.subTest(profile=profile.sha256):
                paths, printer, reporter = self.fixture(profile=profile)
                self.execute(paths, printer, reporter)
                self.assertEqual(printer.recovery_requested, profile.recoverable)

    def test_fallen_back_transfer_restores_services_without_blocker_or_calibration_loss(self):
        paths, printer, reporter = self.fixture(profile=protocol.thr_bootloaders.SECOND, failure='fell-back')
        self.assertEqual(self.execute(paths, printer, reporter), thr_firmware.FELL_BACK)
        self.assertEqual(printer.transfers, 1)
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
        directory, = (paths.printer_data_root / '.tltg_thr_firmware_backups').iterdir()
        evidence = json.loads((directory / 'verification.json').read_text())
        self.assertEqual(evidence['application_sha256'], printer.manifest['thr']['stock_sha256'])
        self.assertIn('#*# shaper_freq_x', (paths.config_root / 'printer.cfg').read_text())

    def test_unconfirmed_end_is_settled_by_readback(self):
        for failure, verified in (('end-unconfirmed', True), ('end-unconfirmed-stock', False)):
            with self.subTest(failure=failure):
                paths, printer, reporter = self.fixture(profile=protocol.thr_bootloaders.SECOND, failure=failure)
                if verified:
                    self.execute(paths, printer, reporter)
                    self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
                    self.assertIn('#*# shaper_freq_x', (paths.config_root / 'printer.cfg').read_text())
                else:
                    with self.assertRaises(thr_firmware.ThrFirmwareError):
                        self.execute(paths, printer, reporter)
                    self.assertTrue((paths.printer_data_root / THR_RECOVERY_MARKER).is_file())
                    self.assertTrue(all(state == 'inactive' for state in printer.states.values()))

    def test_recovery_exit_codes_fail_without_requested_recovery(self):
        paths, printer, reporter = self.fixture(profile=protocol.thr_bootloaders.FIRST, failure='fell-back')
        with self.assertRaises(thr_firmware.ThrFirmwareError):
            self.execute(paths, printer, reporter)
        self.assertFalse(printer.recovery_requested)
        self.assertTrue((paths.printer_data_root / THR_RECOVERY_MARKER).is_file())

    def test_dry_run_and_unsupported_firmware_do_not_access_uart_or_services(self):
        paths, printer, reporter = self.fixture()
        def forbidden(*args, **kwargs):
            raise AssertionError('Preview or admission accessed services/UART')
        thr_firmware.flash_thr_firmware(paths=paths, reporter=reporter, dry_run=True, run=forbidden, urlopen=printer.urlopen)
        self.assertEqual(sorted(paths.printer_data_root.iterdir()), [paths.config_root, paths.firmware_manifest_path])
        # Unsupported firmware, and a supported one that isn't the target (another Klipper), never reach the THR.
        for firmware in ('09.09.09.09', '01.01.06.05'):
            paths.firmware_manifest_path.write_text('{"SOC":{"version":"%s"}}' % firmware)
            with self.subTest(firmware=firmware), self.assertRaises(InstallerError):
                thr_firmware.flash_thr_firmware(paths=paths, reporter=reporter, run=forbidden, urlopen=printer.urlopen)

    def test_services_stopped_by_the_caller_are_left_to_it(self):
        # A firmware update stops Klipper, Moonraker, and the touchscreen itself and starts them again afterwards.
        paths, printer, reporter = self.fixture()
        printer.states = dict.fromkeys(printer.states, 'inactive')
        def offline(*args, **kwargs):
            raise OSError('Moonraker is stopped')
        printer.urlopen = offline
        self.assertEqual(self.execute(paths, printer, reporter, services_stopped=True, environ={LOCK_HELD_ENV: '1'}),
                         thr_firmware.FLASHED)
        self.assertEqual((printer.transfers, set(printer.states.values())), (1, {'inactive'}))
        self.assertFalse((paths.printer_data_root / THR_RECOVERY_MARKER).exists())
