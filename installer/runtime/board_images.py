"""The firmware each board must end on: the image its firmware manifest pins for the printer's target QIDI firmware.

THR and USB Box get this project's build on every printer. The mainboard build fixes closed-loop motor replies, so it
goes only to printers with closed-loop drivers; the Plus 5 mainboard is pinned to QIDI's stock image. A board no
manifest pins for the printer's target firmware is not managed there: it is neither read nor flashed, as the Smart 5's
i.MX RT mainboard, which this project's tools can't read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .manifest import ManifestValidationError
from .models import Manifest, PrinterSpec

# Board firmware manifests in the bundle, per board: one per QIDI board image, each pinning its image for the target
# firmware of the printers that carry it. The Max 4 and Plus 5 THR run 02.02.01.08, the Smart 5's 02.02.01.09.
BOARD_MANIFESTS = {
    "thr": ("firmware/max4-thr-valve-oil-v1.json", "firmware/smart5-thr-valve-oil-v1.json"),
    "box": ("firmware/max4-box-valve-oil-v1.json",),
    "mcu": ("firmware/max4-mcu-valve-oil-v1.json",),
}


@dataclass(frozen=True)
class PinnedImage:
    board: str  # "thr", "box", or "mcu"
    firmware: str  # the target QIDI firmware it is pinned for
    build: str  # "patched": this project's build; "stock": QIDI's image
    version: str  # the version the board reports while running it
    size: int
    sha256: str

    @property
    def is_project_build(self) -> bool:
        return self.build == "patched"


def board_manifest_path(bundle_root: Path, printer: PrinterSpec, board: str) -> str | None:
    """The bundle path of the manifest pinning `board`'s image for `printer`'s target firmware; None when the board
    isn't managed on that printer."""
    firmware = printer.target_firmware.version
    for path in BOARD_MANIFESTS[board]:
        if firmware in json.loads((bundle_root / path).read_text(encoding="utf-8")).get("targets", {}):
            return path
    return None


def managed_boards(bundle_root: Path, printer: PrinterSpec) -> tuple[str, ...]:
    """The boards this release reads and flashes on `printer`, in BOARD_MANIFESTS order."""
    return tuple(board for board in BOARD_MANIFESTS if board_manifest_path(bundle_root, printer, board) is not None)


def load_board_manifest(bundle_root: Path, board: str, printer: PrinterSpec) -> dict:
    """The manifest pinning `board`'s image for `printer`; ValueError when the board isn't managed there."""
    path = board_manifest_path(bundle_root, printer, board)
    if path is None:
        raise ValueError(f"No {board} image is pinned for QIDI firmware {printer.target_firmware.version}")
    return json.loads((bundle_root / path).read_text(encoding="utf-8"))


def pinned_board_image(bundle_root: Path, printer: PrinterSpec, board: str) -> PinnedImage:
    """The image `board` must end on for `printer`'s target firmware; ValueError when the board isn't managed there."""
    from scripts.build_box_firmware import pinned_image

    firmware = printer.target_firmware.version
    return PinnedImage(board=board, firmware=firmware,
                       **pinned_image(load_board_manifest(bundle_root, board, printer), firmware))


def installed_printer(paths) -> tuple[Manifest, PrinterSpec, str]:
    """The bundle's manifest, the printer its installed QIDI firmware belongs to, and that firmware."""
    from .firmware import detect_supported_firmware
    from .manifest import load_manifest

    manifest = load_manifest(paths.installer_root / "package.yaml")
    printer, firmware = detect_supported_firmware(paths.firmware_manifest_path, manifest)
    return manifest, printer, firmware


def pinned_for_installed_printer(paths, board: str) -> PinnedImage:
    """The image `board` must end on for the printer this bundle runs on."""
    _, printer, _ = installed_printer(paths)
    return pinned_board_image(paths.bundle_root, printer, board)


def deliverable_on(printer: PrinterSpec, board: str, installed_firmware: str, bundle_root: Path, *,
                   from_predecessor: bool = False) -> bool:
    """Whether the board's pinned image can go on now.

    The THR and the mainboard images are validated only under the Klipper of the firmware they are pinned for, so they
    wait until the printer runs the target firmware, as the THR fix always did. A USB Box gets Valve Oil's Box firmware
    on any supported firmware, as it did before firmware delivery, unless it runs one of QIDI's predecessor images
    (`from_predecessor`): its version changes with the flash, which the installed Klipper may not support.
    """
    if installed_firmware == printer.target_firmware.version:
        return True
    return board == "box" and not from_predecessor


def runs_target_firmware(paths, board: str, target_firmware: str | None = None) -> bool:
    """Whether the printer runs the target QIDI firmware (`target_firmware`, or its printer's when not given), as a
    board leaving a predecessor needs; False when the installed firmware can't be read or isn't supported."""
    from .errors import InstallerError
    from .firmware import detect_firmware_key_best_effort
    from .manifest import load_manifest

    try:
        installed = detect_firmware_key_best_effort(paths.firmware_manifest_path,
                                                    load_manifest(paths.installer_root / "package.yaml"))
    except (InstallerError, ValueError):
        return False
    if target_firmware is None:
        try:
            target_firmware = pinned_for_installed_printer(paths, board).firmware
        except (InstallerError, ValueError):
            return False
    return installed is not None and installed == target_firmware


def validate_board_targets(manifest: Manifest, bundle_root: Path) -> None:
    """Each board manifest pins images only for printers' target firmware, each target firmware is pinned by at most
    one manifest per board, and the THR and USB Box are pinned for every printer. A pinned image is built from the
    board image the target package carries, with the mainboard build only where the package carries closed-loop
    drivers."""
    from scripts.build_box_firmware import board_spec, pinned_image

    targets = {printer.target_firmware.version: printer for printer in manifest.printers}
    for board, paths in BOARD_MANIFESTS.items():
        pinned_by: dict[str, str] = {}
        for path in paths:
            board_manifest = json.loads((bundle_root / path).read_text(encoding="utf-8"))
            pinned = set(board_manifest.get("targets", {}))
            if not pinned or not pinned <= set(targets):
                raise ManifestValidationError(f"{path} must pin images only for target firmware: "
                                              f"{', '.join(sorted(targets))}.")
            stock_version = board_spec(board_manifest)["stock_version"]
            for firmware in sorted(pinned):
                if firmware in pinned_by:
                    raise ManifestValidationError(f"{path} and {pinned_by[firmware]} both pin a {board} image for "
                                                  f"{firmware}.")
                pinned_by[firmware] = path
                printer = targets[firmware]
                try:
                    image = pinned_image(board_manifest, firmware)
                except ValueError as exc:
                    raise ManifestValidationError(f"{path}: {exc}") from exc
                carried = printer.target_firmware.board_version(board)
                if carried != stock_version:
                    raise ManifestValidationError(f"{path} is built from {board} {stock_version}, but "
                                                  f"{printer.display_name} firmware {firmware} carries {carried}.")
                if (board == "mcu" and image["build"] == "patched"
                        and printer.target_firmware.board_version("closed_loop") is None):
                    raise ManifestValidationError(f"{path} pins this project's mainboard build for "
                                                  f"{printer.display_name}, which has no closed-loop drivers.")
            # A predecessor is QIDI's image of an earlier version that a package of a firmware the release updates
            # from carries; it is replaced by the pinned image, which a target package carries.
            older = {version for firmware in pinned for _, boards in targets[firmware].target_firmware.update_from
                     for version in boards.get(board)}
            for entry in board_spec(board_manifest).get("predecessors", ()):
                if entry["version"] == stock_version or entry["version"] not in older:
                    raise ManifestValidationError(f"{path}: predecessor {entry['version']} is not {board} firmware "
                                                  "that a package of a firmware this release updates from carries.")
        if board in ("thr", "box") and set(pinned_by) != set(targets):
            raise ManifestValidationError(f"No {board} image is pinned for "
                                          f"{', '.join(sorted(set(targets) - set(pinned_by)))}.")
