from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class RuntimePaths:
    bundle_root: Path
    installer_root: Path
    printer_data_root: Path
    config_root: Path
    firmware_manifest_path: Path
    moonraker_url: str
    lock_path: Path
    recovery_sentinel_path: Path
    backup_root: Path
    klipper_root: Path | None = None
    process_restart_marker_path: Path | None = None
    host_reboot_marker_override: Path | None = None
    boot_id_path_override: Path | None = None

    @property
    def managed_klipper_root(self) -> Path:
        return self.klipper_root or self.printer_data_root.parent / "klipper"

    @property
    def restart_marker_path(self) -> Path:
        return self.process_restart_marker_path or self.printer_data_root / ".tltg_optimized_klipper_restart_required"

    @property
    def host_reboot_marker_path(self) -> Path:
        return self.host_reboot_marker_override or self.printer_data_root / ".tltg_optimized_host_reboot_required"

    @property
    def reboot_recommended_path(self) -> Path:
        return self.printer_data_root / ".tltg_optimized_reboot_recommended"

    @property
    def boot_id_path(self) -> Path:
        return self.boot_id_path_override or Path("/proc/sys/kernel/random/boot_id")


@dataclass(frozen=True)
class SectionSpec:
    file: str
    section: str


@dataclass(frozen=True)
class LineSpec:
    file: str
    line: str


@dataclass(frozen=True)
class ManagedTreeSpec:
    id: str
    source: str
    destination: str
    mode: str
    required_files: tuple[str, ...]


@dataclass(frozen=True)
class EnsureLineSpec:
    id: str
    file: str
    line: str
    after: str


@dataclass(frozen=True)
class PatchVariantSpec:
    firmwares: tuple[str, ...]
    expected: str
    desired: str


@dataclass(frozen=True)
class SectionPatchVariantSpec:
    firmwares: tuple[str, ...]
    expected_normalized_sha256: str


@dataclass(frozen=True)
class PatchSpec:
    id: str
    file: str
    section: str
    option: str
    variants: tuple[PatchVariantSpec, ...]
    # A live value above this is lowered to the desired value, even one the
    # operator set.
    maximum: float | None = None

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class SectionPatchSpec:
    id: str
    file: str
    section: str
    variants: tuple[SectionPatchVariantSpec, ...]

    @property
    def option(self) -> str:
        return "__section__"

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class PackageMeta:
    id: str
    display_name: str
    version: str


@dataclass(frozen=True)
class FirmwareSpec:
    supported: tuple[str, ...]


# Boards whose firmware a QIDI package carries and Valve Oil tracks: the mainboard, the toolhead (THR), the USB Box,
# and the Max 4's closed-loop X/Y motor drivers.
PACKAGE_BOARDS = ("mcu", "thr", "box", "closed_loop")


@dataclass(frozen=True)
class PackageBoards:
    """Board firmware versions in QIDI's package for one firmware.

    A board has more than one version when QIDI published that firmware more than once with different board images.
    """
    versions: tuple[tuple[str, tuple[str, ...]], ...]

    def get(self, board: str) -> tuple[str, ...]:
        """The board's versions; empty when the package carries none for it."""
        return dict(self.versions).get(board, ())

    @property
    def boards(self) -> tuple[str, ...]:
        return tuple(board for board, _ in self.versions)


@dataclass(frozen=True)
class ImagePin:
    size: int
    sha256: str


@dataclass(frozen=True)
class TargetFirmwareSpec:
    """The one QIDI firmware a release brings a printer to, and its package on this project's mirror."""
    version: str
    mirror: str  # GitHub owner/repository
    release: str  # release tag
    archive: str  # release asset file name
    sha256: str
    size: int
    boards: PackageBoards
    # Every other firmware of the printer the release supports, with its package's board versions.
    update_from: tuple[tuple[str, PackageBoards], ...]
    # The package's closed-loop motor driver image, which a firmware update sends; None without closed-loop drivers.
    closed_loop_image: ImagePin | None = None

    @property
    def url(self) -> str:
        return f"https://github.com/{self.mirror}/releases/download/{self.release}/{self.archive}"

    @property
    def update_from_versions(self) -> tuple[str, ...]:
        return tuple(version for version, _ in self.update_from)

    def can_update_from(self, firmware: str) -> bool:
        return firmware in self.update_from_versions

    def board_version(self, board: str) -> str | None:
        """The board's version in the target package; None when the package carries none for it."""
        versions = self.boards.get(board)
        return versions[0] if versions else None

    def boards_for(self, firmware: str) -> PackageBoards:
        """Board versions in QIDI's package for the target or for a firmware it updates from."""
        if firmware == self.version:
            return self.boards
        for version, boards in self.update_from:
            if version == firmware:
                return boards
        raise ValueError(f"Firmware {firmware} is not updated to {self.version}.")

    def changes_board(self, board: str, installed_firmware: str) -> bool:
        """Whether installing the target package over `installed_firmware` can change the board's QIDI version.

        True when the target package carries the board and a package QIDI published for the installed firmware carried
        another version of it. Raises ValueError for a firmware that is neither the target nor updated from.
        """
        target = self.boards.get(board)
        return bool(target) and set(self.boards_for(installed_firmware).get(board)) != set(target)


@dataclass(frozen=True)
class PrinterSpec:
    id: str
    display_name: str
    soc_file_prefix: str
    firmware: tuple[str, ...]
    stock_snapshots: str
    macros: str
    target_firmware: TargetFirmwareSpec


@dataclass(frozen=True)
class BackupSpec:
    source_directory: str
    label_prefix: str


@dataclass(frozen=True)
class StateSpec:
    record_file: str


@dataclass(frozen=True)
class PreflightSpec:
    required_files: tuple[str, ...]
    required_sections: tuple[SectionSpec, ...]
    required_lines: tuple[LineSpec, ...]


@dataclass(frozen=True)
class SourcePatchVariantSpec:
    firmware: str
    source: str
    expected_sha256: str
    desired_sha256: str


@dataclass(frozen=True)
class SourcePatchSpec:
    id: str
    source: str
    destination: str
    variants: tuple[SourcePatchVariantSpec, ...]


@dataclass(frozen=True)
class ExtraModuleSpec:
    id: str
    source: str
    destination: str


@dataclass(frozen=True)
class InstallSpec:
    ensure_directories: tuple[str, ...]
    managed_tree: ManagedTreeSpec
    ensure_lines: tuple[EnsureLineSpec, ...]
    source_patches: tuple[SourcePatchSpec, ...] = ()
    extra_modules: tuple[ExtraModuleSpec, ...] = ()


@dataclass(frozen=True)
class PatchSetSpec:
    set_options: tuple[PatchSpec, ...]
    delete_sections: tuple[SectionPatchSpec, ...]


@dataclass(frozen=True)
class PostflightSpec:
    verify_lines: tuple[LineSpec, ...]


@dataclass(frozen=True)
class SystemDnsSpec:
    resolv_conf: str
    resolvconf_head: str
    resolvconf_tail: str
    target_symlink: str
    fallback_nameservers: tuple[str, ...]


@dataclass(frozen=True)
class SystemAptSourcesSpec:
    file: str
    content: str


@dataclass(frozen=True)
class SystemOptionalServiceSpec:
    id: str
    service: str
    prompt: str


@dataclass(frozen=True)
class SystemServicesSpec:
    disable: tuple[str, ...]
    optional_disable: tuple[SystemOptionalServiceSpec, ...]


@dataclass(frozen=True)
class SystemQidiClientStaticGifsSpec:
    archive: str
    sha256: str
    destination: str
    restart_service: str


@dataclass(frozen=True)
class SystemMoonrakerMetadata3mfSpec:
    file: str
    restart_service: str


@dataclass(frozen=True)
class SystemFluiddBundleSpec:
    file: str
    service_worker: str


@dataclass(frozen=True)
class SystemFluiddBuildSpec:
    version: str
    url: str
    archive: str
    sha256: str


@dataclass(frozen=True)
class SystemValveOilUiSpec:
    # Repository directory the bundle builder builds the archive from.
    source: str
    archive: str
    # The bundle builder pins the archive it built in the bundled manifest; repository checkouts have none.
    sha256: str | None


@dataclass(frozen=True)
class SystemWheelSpec:
    url: str
    archive: str
    sha256: str


@dataclass(frozen=True)
class SystemMoonrakerSpeedupsSpec:
    wheels: tuple[SystemWheelSpec, ...]


@dataclass(frozen=True)
class SystemKernelModuleSpec:
    name: str
    archive: str
    sha256: str


@dataclass(frozen=True)
class SystemZramSwapSpec:
    kernel_release: str
    # Exact `/proc/sys/kernel/version` strings of the builds the modules load on.
    kernel_versions: tuple[str, ...]
    # In load order.
    modules: tuple[SystemKernelModuleSpec, ...]
    size_mb: int
    algorithm: str
    swappiness: int
    page_cluster: int


@dataclass(frozen=True)
class SystemFluiddFilamentConfigSpec:
    link: str
    target: str


@dataclass(frozen=True)
class SystemRockchipRootSyncSpec:
    id: str
    unit: str
    unit_file: str
    script: str
    dropin: str
    dropin_content: str
    mount_target: str
    defective_unit_markers: tuple[str, ...]
    defective_script_markers: tuple[str, ...]
    ordered_script_markers: tuple[str, ...]
    vendor_exec_start: str
    desired_exec_start: str


@dataclass(frozen=True)
class SystemOptimizationsSpec:
    enabled_by_default: bool
    dns: SystemDnsSpec
    apt_sources: SystemAptSourcesSpec
    services: SystemServicesSpec
    qidiclient_static_gifs: SystemQidiClientStaticGifsSpec
    moonraker_metadata_3mf: SystemMoonrakerMetadata3mfSpec
    moonraker_file_manager: SystemMoonrakerMetadata3mfSpec
    fluidd_box_filament_ids: SystemFluiddBundleSpec
    fluidd_build: SystemFluiddBuildSpec
    valve_oil_ui: SystemValveOilUiSpec
    moonraker_speedups: SystemMoonrakerSpeedupsSpec
    zram_swap: SystemZramSwapSpec
    klipper_box_config: SystemMoonrakerMetadata3mfSpec
    klipper_print_stats_manager: SystemMoonrakerMetadata3mfSpec
    fluidd_filament_config: SystemFluiddFilamentConfigSpec
    rockchip_root_sync: SystemRockchipRootSyncSpec


@dataclass(frozen=True)
class AddonSpec:
    id: str
    version: str
    source: SystemWheelSpec
    # Empty for an add-on that is a program, not a Python package; it gets no Python environment.
    wheels: tuple[SystemWheelSpec, ...]
    # Pure-Python packages PyPI publishes only as source; installed after the wheels, built with their setuptools.
    sdists: tuple[SystemWheelSpec, ...] = ()
    # The source isn't in the bundle; the printer downloads it from source.url when the add-on is installed or updated.
    download: bool = False


@dataclass(frozen=True)
class AddonsSpec:
    pip_wheel: SystemWheelSpec
    catalog: tuple[AddonSpec, ...]

    def find(self, addon_id: str) -> AddonSpec | None:
        return next((addon for addon in self.catalog if addon.id == addon_id), None)


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    package: PackageMeta
    printers: tuple[PrinterSpec, ...]
    firmware: FirmwareSpec
    backup: BackupSpec
    state: StateSpec
    preflight: PreflightSpec
    install: InstallSpec
    patches: PatchSetSpec
    postflight: PostflightSpec
    system_optimizations: Optional[SystemOptimizationsSpec] = None
    addons: Optional[AddonsSpec] = None

    @property
    def state_file(self) -> str:
        return self.state.record_file

    def printer_for_firmware(self, firmware_version: str) -> PrinterSpec | None:
        for printer in self.printers:
            if firmware_version in printer.firmware:
                return printer
        return None

    # The managed tree installs the common macros plus the printer's own macro folder.
    def managed_tree_sources(self, firmware_version: str) -> tuple[str, ...]:
        printer = self.printer_for_firmware(firmware_version)
        if printer is None:
            raise ValueError(f"No printer supports firmware {firmware_version}.")
        return (self.install.managed_tree.source, printer.macros)

    @property
    def managed_tree(self) -> ManagedTreeSpec:
        return self.install.managed_tree

    @property
    def include_line(self) -> EnsureLineSpec:
        if len(self.install.ensure_lines) != 1:
            raise ValueError("Manifest must define exactly one install.ensure_lines entry.")
        return self.install.ensure_lines[0]


@dataclass(frozen=True)
class AllowedPatchTarget:
    file: str
    section: str
    option: str

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class UpgradeSourcePatch:
    id: str
    destination: str
    firmware: str
    original_sha256: str
    desired_sha256: str


@dataclass(frozen=True)
class UpgradeSources:
    schema_version: int
    allowed_patch_targets: tuple[AllowedPatchTarget, ...]
    source_patches: tuple[UpgradeSourcePatch, ...] = ()


@dataclass(frozen=True)
class ManagedTreeFileRecord:
    path: str
    sha256: str


@dataclass(frozen=True)
class ManagedTreeState:
    root: str
    files: tuple[ManagedTreeFileRecord, ...]


@dataclass(frozen=True)
class PatchLedgerEntry:
    id: str
    file: str
    section: str
    option: str
    expected: str
    desired: str
    install_result: str

    @property
    def target_tuple(self) -> tuple[str, str, str]:
        return (self.file, self.section, self.option)


@dataclass(frozen=True)
class SourcePatchState:
    id: str
    destination: str
    firmware: str
    original_sha256: str
    desired_sha256: str
    original_mode: int
    original_bytes: bytes
    install_result: str


@dataclass(frozen=True)
class InstalledState:
    schema_version: int
    package_id: str
    package_version: str
    runtime_firmware: str
    backup_label: str
    installed_at: str
    managed_tree: ManagedTreeState
    patch_ledger: tuple[PatchLedgerEntry, ...]
    source_patches: tuple[SourcePatchState, ...] = ()
    system_ledger: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class SystemOptimizationCliOptions:
    disable_ai_detection: bool = False
    keep_ai_detection: bool = False
    keep_system_optimizations: bool = False
    reboot_host: bool = False


@dataclass(frozen=True)
class PatchTargetIssue:
    id: str
    file: str
    section: str
    option: str
    reason: str


@dataclass(frozen=True)
class PreflightReport:
    missing_files: tuple[str, ...]
    missing_sections: tuple[SectionSpec, ...]
    missing_lines: tuple[LineSpec, ...]
    patch_target_issues: tuple[PatchTargetIssue, ...]

    def is_empty(self) -> bool:
        return not (
            self.missing_files
            or self.missing_sections
            or self.missing_lines
            or self.patch_target_issues
        )


@dataclass(frozen=True)
class PatchResult:
    id: str
    file: str
    section: str
    option: str
    current: str
    expected: str
    desired: str
    classification: str
    # The value uninstall restores when it isn't the variant's expected value:
    # an operator's value lowered to a patch's maximum.
    ledger_expected: str | None = None


@dataclass(frozen=True)
class DriftRecord:
    path: str
    sha256_before_remove: str


@dataclass(frozen=True)
class FileChange:
    path: Path
    preimage: bytes | None
    desired: bytes | None


@dataclass(frozen=True)
class ManagedTreeIntent:
    id: str
    source: Optional[str]
    destination: str
    action: str


@dataclass(frozen=True)
class IncludeLineIntent:
    id: str
    file: str
    line: str
    after: Optional[str]
    action: str


@dataclass(frozen=True)
class StateFileIntent:
    path: str
    action: str


@dataclass(frozen=True)
class InstallPlan:
    backup_label: str
    file_changes: tuple[FileChange, ...]
    managed_tree_intent: ManagedTreeIntent
    include_line_intents: tuple[IncludeLineIntent, ...]
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    managed_tree_files: tuple[ManagedTreeFileRecord, ...]
    state_file_intent: StateFileIntent


@dataclass(frozen=True)
class UninstallPlan:
    backup_label: str
    file_changes: tuple[FileChange, ...]
    managed_tree_intent: ManagedTreeIntent
    include_line_intents: tuple[IncludeLineIntent, ...]
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    state_file_intent: StateFileIntent


@dataclass(frozen=True)
class InstallResult:
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    backup_label: str
    backup_zip_path: Optional[Path]
    dry_run: bool = False


@dataclass(frozen=True)
class UninstallResult:
    patch_results: tuple[PatchResult, ...]
    managed_tree_drift: tuple[DriftRecord, ...]
    backup_label: Optional[str]
    backup_zip_path: Optional[Path]
    dry_run: bool = False
