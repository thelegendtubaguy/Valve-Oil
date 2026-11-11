from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import FirmwareDetectionError, UnsupportedFirmwareError
from .models import Manifest, PrinterSpec


@dataclass(frozen=True)
class DetectedFirmware:
    version: str
    soc_file: str | None


def detect_firmware(path: Path) -> DetectedFirmware:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise FirmwareDetectionError() from exc

    try:
        soc = raw["SOC"]
        version = soc["version"]
    except (KeyError, TypeError) as exc:
        raise FirmwareDetectionError() from exc
    if not isinstance(version, str) or not version:
        raise FirmwareDetectionError()
    soc_file = soc.get("file")
    return DetectedFirmware(version=version, soc_file=soc_file if isinstance(soc_file, str) else None)


def firmware_key(manifest: Manifest, detected: DetectedFirmware) -> str:
    """The detected firmware as the installer records it: the SOC version, with the printer's key prefix.

    Firmware the installer doesn't support keeps its printer's prefix when the SOC file names a known printer,
    so a state record or backup from another printer's firmware never matches it.
    """
    printer = manifest.printer_for_soc_file(detected.soc_file) if detected.soc_file is not None else None
    return printer.firmware_key(detected.version) if printer is not None else detected.version


def supported_printer(manifest: Manifest, detected: DetectedFirmware) -> PrinterSpec | None:
    if detected.soc_file is not None:
        printer = manifest.printer_for_soc_file(detected.soc_file)
        if printer is None or printer.firmware_key(detected.version) not in printer.firmware:
            return None
        return printer
    # Without a SOC file name, only a version that a single printer lists identifies the printer.
    candidates = [
        printer for printer in manifest.printers if printer.firmware_key(detected.version) in printer.firmware
    ]
    return candidates[0] if len(candidates) == 1 else None


def detect_supported_firmware(path: Path, manifest: Manifest) -> tuple[PrinterSpec, str]:
    detected = detect_firmware(path)
    printer = supported_printer(manifest, detected)
    if printer is None:
        raise UnsupportedFirmwareError()
    return printer, printer.firmware_key(detected.version)


def detect_supported_firmware_best_effort(path: Path, manifest: Manifest) -> tuple[PrinterSpec, str] | None:
    try:
        return detect_supported_firmware(path, manifest)
    except (FirmwareDetectionError, UnsupportedFirmwareError):
        return None


def detect_firmware_key_best_effort(path: Path, manifest: Manifest) -> Optional[str]:
    try:
        return firmware_key(manifest, detect_firmware(path))
    except FirmwareDetectionError:
        return None
