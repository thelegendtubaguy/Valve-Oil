from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path

from installer.runtime.backup import requires_external_backup_manifest
from installer.runtime.board_images import BOARD_MANIFESTS, managed_boards, pinned_board_image, validate_board_targets
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import (
    CompatibilityValidationError,
    load_supported_upgrade_sources,
    parse_supported_upgrade_sources,
    validate_installed_state_identity,
    validate_manifest_compatibility,
    validate_patch_ledger,
)
import yaml

from installer.runtime.manifest import ManifestValidationError, load_manifest, parse_manifest
from installer.runtime.models import (
    InstalledState,
    ManagedTreeState,
    PatchLedgerEntry,
    SourcePatchState,
)
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.source_patches import SourcePatchError, validate_source_state
from installer.runtime.state_file import load_installed_state, write_installed_state
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import REPO_ROOT, build_env, copy_base_runtime, load_source, moonraker_urlopen


class InstallerCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.envelope = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )

    def test_cumulative_envelope_matches_captured_old_profile_union(self) -> None:
        canonical = {
            "allowed_patch_targets": [item.__dict__ for item in self.envelope.allowed_patch_targets],
            "source_patches": [item.__dict__ for item in self.envelope.source_patches],
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(self.envelope.schema_version, 2)
        self.assertEqual(len(self.envelope.allowed_patch_targets), 35)
        self.assertEqual(len(self.envelope.source_patches), 13)
        self.assertEqual(
            digest,
            "85ce713aaf2f2213535aeac6e8f55ab1fcbf1ee9abbff33331a3d99cdfb08910",
        )

    def test_manifest_rejects_printers_that_share_an_identity(self) -> None:
        # Patch variants are keyed by firmware version, so a version or SOC file prefix
        # claimed by two printers would let one printer's patches apply to the other.
        raw = yaml.safe_load((REPO_ROOT / "installer/package.yaml").read_text(encoding="utf-8"))
        max4 = raw["printers"][0]
        shared_firmware = {**max4, "id": "other", "soc_file_prefix": "QD_OTHER_SOC_", "firmware": [max4["firmware"][-1]]}
        shared_prefix = {**max4, "id": "other", "soc_file_prefix": "QD_MAX4_", "firmware": ["09.09.09.09"]}
        # A printer whose versions repeat another's must key every firmware with its prefix.
        unkeyed = {**max4, "id": "other", "soc_file_prefix": "QD_OTHER_SOC_", "firmware_key_prefix": "other-",
                   "firmware": ["other-09.09.09.09", "09.09.09.08"]}
        for other in (shared_firmware, shared_prefix, unkeyed):
            with self.subTest(other=other["soc_file_prefix"]):
                with self.assertRaises(ManifestValidationError):
                    parse_manifest({**raw, "printers": [max4, other]})

    def test_target_firmware_comes_only_from_the_mirror_and_updates_every_older_firmware(self) -> None:
        raw = yaml.safe_load((REPO_ROOT / "installer/package.yaml").read_text(encoding="utf-8"))
        target = raw["printers"][0]["target_firmware"]
        older = next(iter(target["update_from"]))
        invalid = {
            "other mirror": {"mirror": "someone/Qidi-Max4-Defaults"},
            "tag of another archive": {"sha256": "0" * 64},
            "another printer's archive": {"archive": target["archive"].replace("QD_MAX4_", "QD_PLUS5_")},
            "empty archive": {"size": 0},
            "not the newest firmware": {"version": older},
            "older firmware left out": {"update_from": {k: v for k, v in target["update_from"].items() if k != older}},
            "older firmware without a board": {"update_from": {**target["update_from"], older: {"mcu": "02.01.01.11", "thr": "02.02.01.08", "box": "02.03.01.21"}}},
            "two target board versions": {"boards": {**target["boards"], "mcu": ["02.01.01.11", "02.01.01.12"]}},
        }
        for case, change in invalid.items():
            with self.subTest(case=case):
                printers = copy.deepcopy(raw["printers"])
                printers[0]["target_firmware"].update(change)
                with self.assertRaises(ManifestValidationError):
                    parse_manifest({**raw, "printers": printers})

        # A firmware QIDI published twice with different board images may change that board.
        printers = copy.deepcopy(raw["printers"])
        printers[0]["target_firmware"]["update_from"][older] = {
            **target["boards"], "mcu": ["02.01.01.11", target["boards"]["mcu"]]}
        spec = parse_manifest({**raw, "printers": printers}).printers[0].target_firmware
        self.assertEqual([spec.changes_board(board, older) for board in ("mcu", "thr")], [True, False])
        self.assertFalse(any(spec.changes_board(board, spec.version) for board in spec.boards.boards))
        with self.assertRaises(ValueError):
            spec.changes_board("mcu", "09.09.09.09")

    def test_restore_sets_pin_qidi_update_folder_for_every_firmware(self) -> None:
        # The USB hand-off restores QIDI's update folder from these, so every listed firmware needs pinned files from
        # its own mirror package, without the system package.
        raw = yaml.safe_load((REPO_ROOT / "installer/package.yaml").read_text(encoding="utf-8"))
        sets = raw["printers"][0]["restore_sets"]
        pin = {"size": 1, "sha256": "0" * 64}
        twice = {name: entry for name, entry in sets.items() if name.startswith("01.01.06.04_")}
        invalid = {
            "firmware left out": {k: v for k, v in sets.items() if k != "01.01.06.05"},
            "unlisted firmware": {**sets, "09.09.09.09": sets["01.01.06.05"]},
            "file without a size": {**sets, "01.01.06.05": {**sets["01.01.06.05"], "files": {
                **sets["01.01.06.05"]["files"], "QD_MAX4_MCU.bin": {"sha256": "0" * 64}}}},
            "system package": {**sets, "01.01.06.05": {**sets["01.01.06.05"], "files": {
                **sets["01.01.06.05"]["files"], "QD_MAX4_SOC_01.01.06.05_20260804_Release_NA": pin}}},
            "path in a file name": {**sets, "01.01.06.05": {**sets["01.01.06.05"], "files": {
                **sets["01.01.06.05"]["files"], "../MCU.bin": pin}}},
            "another firmware's archive": {**sets, "01.01.06.05": {**sets["01.01.06.05"],
                                                                   "archive": "QD_MAX4_01.01.06.03_20260507_Release.zip"}},
            "another printer's release": {**sets, "01.01.06.05": {**sets["01.01.06.05"],
                                                                  "release": "qidi-plus5-firmware-01.01.06.05"}},
            "a twice-published firmware without its date": {
                **{k: v for k, v in sets.items() if k not in twice}, "01.01.06.04": next(iter(twice.values())),
                **dict(list(twice.items())[1:])},
            "target set from another archive": {**sets, "01.01.07.01": {
                **sets["01.01.07.01"], "archive": "QD_MAX4_01.01.07.01_20261017_Release.zip"}},
        }
        for case, change in invalid.items():
            with self.subTest(case=case):
                printers = copy.deepcopy(raw["printers"])
                printers[0]["restore_sets"] = change
                with self.assertRaises(ManifestValidationError):
                    parse_manifest({**raw, "printers": printers})
        max4 = self.manifest.printers[0]
        self.assertEqual([item.name for item in max4.restore_sets if item.firmware == "01.01.06.04"],
                         ["01.01.06.04_20260609", "01.01.06.04_20260612"])

    def test_board_images_are_pinned_for_each_target_firmware(self) -> None:
        validate_board_targets(self.manifest, REPO_ROOT)
        # THR and Box get this project's build everywhere; the mainboard build only with closed-loop drivers, and the
        # Smart 5's i.MX RT mainboard is not managed.
        expected = {"max4": {"thr": True, "box": True, "mcu": True}, "plus5": {"thr": True, "box": True, "mcu": False},
                    "smart5": {"thr": True, "box": True}}
        self.assertEqual({printer.id: {board: pinned_board_image(REPO_ROOT, printer, board).is_project_build
                                       for board in managed_boards(REPO_ROOT, printer)}
                          for printer in self.manifest.printers}, expected)
        smart5 = next(printer for printer in self.manifest.printers if printer.id == "smart5")
        with self.assertRaises(ValueError):
            pinned_board_image(REPO_ROOT, smart5, "mcu")
        plus5 = next(printer for printer in self.manifest.printers if printer.target_firmware.board_version("closed_loop") is None)
        changes = {
            "mainboard build without closed-loop drivers": ("mcu", lambda m: m["targets"].update({plus5.target_firmware.version: "patched"})),
            "no image for a target": ("thr", lambda m: m["targets"].pop(plus5.target_firmware.version)),
            "two images for a target": ("thr", lambda m: m["targets"].update({smart5.target_firmware.version: "patched"})),
            "image for firmware no printer targets": ("box", lambda m: m["targets"].update({"09.09.09.09": "patched"})),
            "unknown build": ("box", lambda m: m["targets"].update({plus5.target_firmware.version: "latest"})),
            "built from another board image": ("box", lambda m: m["box"].update(stock_version="02.03.01.22")),
            "predecessor no older package carries": ("mcu", lambda m: m["mcu"]["predecessors"][0].update(version="02.01.01.10")),
        }
        for case, (board, change) in changes.items():
            with self.subTest(case=case):
                bundle = Path(tempfile.mkdtemp(prefix="board-targets-"))
                self.addCleanup(shutil.rmtree, bundle)
                shutil.copytree(REPO_ROOT / "firmware", bundle / "firmware")
                path = bundle / BOARD_MANIFESTS[board][0]
                data = json.loads(path.read_text())
                change(data)
                path.write_text(json.dumps(data))
                with self.assertRaises(ManifestValidationError):
                    validate_board_targets(self.manifest, bundle)

    def test_parser_rejects_malformed_duplicate_and_unsafe_envelope_entries(self) -> None:
        raw = {
            "schema_version": 2,
            "allowed_patch_targets": [
                {"file": "config/printer.cfg", "section": "stepper_x", "option": "homing_speed"}
            ],
            "source_patches": [
                {
                    "id": "homing",
                    "destination": "klippy/extras/homing.py",
                    "firmware": "01.01.06.03",
                    "original_sha256": "a" * 64,
                    "desired_sha256": "b" * 64,
                }
            ],
        }
        invalid_documents = []
        duplicate_target = copy.deepcopy(raw)
        duplicate_target["allowed_patch_targets"].append(
            duplicate_target["allowed_patch_targets"][0]
        )
        invalid_documents.append(duplicate_target)
        duplicate_source = copy.deepcopy(raw)
        duplicate_source["source_patches"].append(duplicate_source["source_patches"][0])
        invalid_documents.append(duplicate_source)
        bad_hash = copy.deepcopy(raw)
        bad_hash["source_patches"][0]["desired_sha256"] = "not-a-hash"
        invalid_documents.append(bad_hash)
        escaped_path = copy.deepcopy(raw)
        escaped_path["source_patches"][0]["destination"] = "klippy/../secrets.py"
        invalid_documents.append(escaped_path)
        unsupported_root = copy.deepcopy(raw)
        unsupported_root["allowed_patch_targets"][0]["file"] = "outside/printer.cfg"
        invalid_documents.append(unsupported_root)

        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(CompatibilityValidationError):
                    parse_supported_upgrade_sources(document)

    def test_current_manifest_requires_envelope_coverage(self) -> None:
        missing_target = replace(
            self.envelope,
            allowed_patch_targets=self.envelope.allowed_patch_targets[1:],
        )
        with self.assertRaises(CompatibilityValidationError):
            validate_manifest_compatibility(self.manifest, missing_target)

        needed = self.manifest.install.source_patches[0].variants[0].desired_sha256
        missing_source = replace(
            self.envelope,
            source_patches=tuple(
                item for item in self.envelope.source_patches
                if item.desired_sha256 != needed
            ),
        )
        with self.assertRaises(CompatibilityValidationError):
            validate_manifest_compatibility(self.manifest, missing_source)

    def test_installed_identity_and_patch_ledger_are_admitted_independently(self) -> None:
        allowed = self.envelope.allowed_patch_targets[0]
        state = _state(
            package_id=self.manifest.package.id,
            package_version="26.04.21.1",
            patch_ledger=(
                PatchLedgerEntry(
                    "historical",
                    allowed.file,
                    allowed.section,
                    allowed.option,
                    "old",
                    "new",
                    "applied",
                ),
            ),
        )
        for package_version in ("26.04.21.1", "26.10.28.4", "99.01.01.1"):
            validate_installed_state_identity(
                replace(state, package_version=package_version), self.manifest
            )
        validate_patch_ledger(state, self.envelope)

        with self.assertRaises(CompatibilityValidationError):
            validate_installed_state_identity(
                replace(state, package_id="unrelated-package"), self.manifest
            )
        with self.assertRaises(CompatibilityValidationError):
            validate_patch_ledger(
                replace(
                    state,
                    patch_ledger=(
                        replace(state.patch_ledger[0], file="config/unknown.cfg"),
                    ),
                ),
                self.envelope,
            )

    def test_external_backup_manifest_boundary_requires_numeric_versions(self) -> None:
        cases = (
            ("26.07.13.2", False, False),
            ("26.07.26.1", False, True),
            ("26.99.99.1", False, True),
            ("not-a-version", False, True),
            (None, False, True),
            ("26.04.21.1", True, True),
        )
        for package_version, declares_source, expected in cases:
            with self.subTest(package_version=package_version, declares_source=declares_source):
                self.assertEqual(
                    requires_external_backup_manifest(
                        package_version=package_version,
                        state_declares_source_patches=declares_source,
                    ),
                    expected,
                )

    def test_prior_state_identity_rejection_precedes_backup(self) -> None:
        printer_root, paths = _runtime_paths()
        write_installed_state(
            printer_root / self.manifest.state_file,
            _state(
                package_id="unrelated-package",
                package_version="26.04.21.1",
                patch_ledger=(),
            ),
        )
        with mock.patch("installer.runtime.runner.create_config_backup") as backup:
            with self.assertRaises(Exception) as raised:
                run_install(
                    paths,
                    self.manifest,
                    PlainReporter(io.StringIO()),
                    urlopen=moonraker_urlopen(),
                )
        self.assertEqual(type(raised.exception).__name__, "PreviousPackageValidationError")
        backup.assert_not_called()

    def test_direct_historical_update_and_uninstall_need_no_migration_chain(self) -> None:
        # Identity admission of every version is covered above; one end-to-end pass from the oldest is enough.
        version = "26.04.21.1"
        printer_root, paths = _runtime_paths()
        run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(
                saved_variables_path=printer_root / "config/saved_variables.cfg"
            ),
        )
        state_path = printer_root / self.manifest.state_file
        prior = load_installed_state(state_path)
        write_installed_state(state_path, replace(prior, package_version=version))

        run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(
                saved_variables_path=printer_root / "config/saved_variables.cfg"
            ),
        )
        self.assertEqual(
            load_installed_state(state_path).package_version,
            self.manifest.package.version,
        )

        write_installed_state(
            state_path,
            replace(load_installed_state(state_path), package_version=version),
        )
        run_uninstall(
            paths,
            self.manifest,
            self.envelope,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("Y\nN\n"),
            urlopen=moonraker_urlopen(),
        )
        self.assertFalse(state_path.exists())

    def test_source_ledger_requires_cumulative_provenance(self) -> None:
        patch = self.manifest.install.source_patches[0]
        variant = patch.variants[0]
        entry = SourcePatchState(
            patch.id,
            patch.destination,
            variant.firmware,
            variant.expected_sha256,
            variant.desired_sha256,
            0o644,
            b"source preimage",
            "applied",
        )
        state = _state(
            package_id=self.manifest.package.id,
            package_version="26.07.26.1",
            patch_ledger=(),
            source_patches=(entry,),
        )
        validate_source_state(
            state,
            self.manifest.install.source_patches,
            upgrade_sources=self.envelope,
            expected_firmware=variant.firmware,
        )
        for changed_entry in (
            replace(entry, destination="klippy/extras/unknown.py"),
            replace(entry, firmware="01.99.99.99"),
            replace(entry, desired_sha256="f" * 64),
        ):
            with self.subTest(entry=changed_entry):
                with self.assertRaises(SourcePatchError):
                    validate_source_state(
                        replace(state, source_patches=(changed_entry,)),
                        self.manifest.install.source_patches,
                        upgrade_sources=self.envelope,
                        expected_firmware=variant.firmware,
                    )
    def test_ordinary_version_bump_leaves_cumulative_envelope_unchanged(self) -> None:
        module = _load_version_bump_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for relative in (
                "installer/package.yaml",
                "installer/supported_upgrade_sources.yaml",
                "installer/klipper/macros/globals.cfg",
            ):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO_ROOT / relative, root / relative)
            envelope_path = root / "installer/supported_upgrade_sources.yaml"
            before = envelope_path.read_bytes()

            changed = module.bump_version(root, "99.01.01.1")

            self.assertEqual(
                changed,
                ["installer/package.yaml", "installer/klipper/macros/globals.cfg"],
            )
            self.assertEqual(envelope_path.read_bytes(), before)


def _state(
    *, package_id: str, package_version: str, patch_ledger, source_patches=()
) -> InstalledState:
    return InstalledState(
        schema_version=1,
        package_id=package_id,
        package_version=package_version,
        runtime_firmware="01.01.06.03",
        backup_label="test",
        installed_at="2026-01-01T00:00:00Z",
        managed_tree=ManagedTreeState("config/tltg-optimized-macros", ()),
        patch_ledger=patch_ledger,
        source_patches=source_patches,
    )


def _runtime_paths():
    printer_root = copy_base_runtime()
    (printer_root / "firmware_manifest.json").write_text(
        json.dumps({"SOC": {"version": "01.01.06.03"}}), encoding="utf-8"
    )
    paths = resolve_runtime_paths(
        bundle_root=REPO_ROOT,
        environ=build_env(
            printer_root,
            moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
        ),
    )
    return printer_root, paths


def _load_version_bump_module():
    return load_source("version_bump", REPO_ROOT / "scripts/bump_installer_version.py")
