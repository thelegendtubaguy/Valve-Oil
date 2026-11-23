"""Valve Oil's boot logo in place of QIDI's, in the printer's boot partition.

The bootloader (Rockchip U-Boot) shows logo.bmp from the resource image in the boot partition's Android boot image from
power-on, and the kernel keeps logo_kernel.bmp up until Linux draws. QIDI's are the same full-screen 24-bit BMP: 480x800
turned on the Plus 5's portrait panel. QIDI's firmware packages don't write the boot partition.

Only those two BMPs and the boot image's SHA-1 id over its parts change; the kernel and device tree are left byte for
byte. A boot image whose layout isn't the one described here, or whose id doesn't match its parts, is left alone. The
bootloader doesn't check the id (only the device tree carries a hash it checks) and shows no logo when one can't be
read, so a write cut short by a power loss leaves a printer that boots; the logos are written first and the id last,
in whole 512-byte blocks, and the partition is read back.

Before the first write, the whole partition is saved under printer_data, which QIDI's package leaves alone, with a
record of what was written. Uninstall writes QIDI's bytes back while the partition still holds what Valve Oil wrote.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import boot_screen, update_screen
from .errors import InstallerError
from .fs_atomic import atomic_delete, atomic_write_text
from .sudo import SudoError, authenticate_sudo, run_sudo, sudo_command

DEVICE = "/dev/block/by-name/boot"
SYSTEM_ROOT_ENV = "VO_SYSTEM_ROOT"  # system_optimizations.SYSTEM_ROOT_ENV: a test's stand-in for /
DEFAULT_PRINTER_DATA_ROOT = Path("/home/qidi/printer_data")
RECORD = ".vo_boot_logo.json"
BACKUP_DIR = "vo_system_backups"
LOGOS = (b"logo.bmp", b"logo_kernel.bmp")
BLOCK = 512
BMP_HEADER = 54
SCREEN = (800, 480)


class BootLogoError(InstallerError):
    pass


@dataclass(frozen=True)
class Layout:
    page: int
    id_offset: int
    logos: tuple[tuple[int, int], ...]  # (offset, size) of each logo BMP in the image


def layout(image: bytes) -> Layout:
    """Where the logos and the id are in a boot image of the reviewed layout; BootLogoError for any other."""
    try:
        magic, kernel_size, _, ramdisk_size, _, second_size, _, _, page = struct.unpack_from("<8s8I", image, 0)
        (version,) = struct.unpack_from("<I", image, 40)
    except struct.error as exc:
        raise BootLogoError("The boot partition is too short to hold a boot image.") from exc
    if magic != b"ANDROID!" or version != 0 or page not in (2048, 4096):
        raise BootLogoError("The boot partition doesn't hold a boot image of the reviewed layout.")
    parts, offset = [], page
    for size in (kernel_size, ramdisk_size, second_size):
        parts.append((offset, size))
        offset += -(-size // page) * page
    if offset > len(image):
        raise BootLogoError("The boot image is longer than the boot partition.")
    id_offset = 576
    if image[id_offset:id_offset + 20] != _image_id(image, parts) or any(image[id_offset + 20:id_offset + 32]):
        raise BootLogoError("The boot image's id doesn't match its contents.")
    second_offset, second_size = parts[2]
    resource = image[second_offset:second_offset + second_size]
    try:
        magic, _, _, header_blocks, entry_blocks, count = struct.unpack_from("<4sHHBBI", resource, 0)
    except struct.error as exc:
        raise BootLogoError("The boot image has no resource image.") from exc
    if magic != b"RSCE" or entry_blocks != 1:
        raise BootLogoError("The boot image has no resource image of the reviewed layout.")
    found = {}
    for index in range(min(count, 64)):
        entry = resource[(header_blocks + index) * BLOCK:(header_blocks + index + 1) * BLOCK]
        if entry[:4] != b"ENTR":
            break
        name = entry[4:260].split(b"\0")[0]
        start, size = struct.unpack_from("<II", entry, 260)
        if name in LOGOS:
            found[name] = (second_offset + start * BLOCK, size)
    if set(found) != set(LOGOS):
        raise BootLogoError("The boot image has no logo.bmp and logo_kernel.bmp.")
    for start, size in found.values():
        _bmp_geometry(image[start:start + size])
        if start + size > second_offset + second_size:
            raise BootLogoError("A boot logo runs past the resource image.")
    return Layout(page, id_offset, tuple(found[name] for name in LOGOS))


def _image_id(image: bytes, parts) -> bytes:
    digest = hashlib.sha1()
    for offset, size in parts:
        digest.update(image[offset:offset + size])
        digest.update(struct.pack("<I", size))
    return digest.digest()


def _bmp_geometry(bmp: bytes) -> tuple[int, int]:
    """The (width, height) of a full-screen 24-bit uncompressed BMP, negative height for top-down rows."""
    try:
        magic, size, _, offset, info_size, width, height, planes, bits, compression = struct.unpack_from(
            "<2sIIIIiiHHI", bmp, 0)
    except struct.error as exc:
        raise BootLogoError("A boot logo isn't a BMP.") from exc
    if (magic != b"BM" or offset != BMP_HEADER or info_size != 40 or planes != 1 or bits != 24 or compression != 0
            or sorted((width, abs(height))) != sorted(SCREEN) or size != len(bmp)
            or len(bmp) != BMP_HEADER + width * abs(height) * 3):
        raise BootLogoError("A boot logo isn't the full-screen 24-bit BMP of the reviewed layout.")
    return width, height


def logo_bmp(original: bytes, upright_rows: list[bytes]) -> bytes:
    """Our logo as a BMP of the original's header and geometry. A portrait BMP holds the upright screen turned 90
    degrees counterclockwise, as the framebuffer does (update_screen)."""
    width, height = _bmp_geometry(original)
    upright_width, upright_height = len(upright_rows[0]) // 3, len(upright_rows)
    if (upright_width, upright_height) != SCREEN:
        raise BootLogoError("The boot logo image isn't 800x480.")
    turned = abs(height) > width
    upright = b"".join(upright_rows)
    stride = upright_width * 3
    rows = []
    for y in range(abs(height)):
        row = bytearray(width * 3)
        if turned:
            # Stored (x, y) shows upright (upright_width - 1 - y, x): the stored row is an upright column.
            ux = upright_width - 1 - y
            for channel in range(3):
                row[2 - channel::3] = upright[ux * 3 + channel::stride]
        else:
            line = upright[y * stride:(y + 1) * stride]
            for channel in range(3):
                row[2 - channel::3] = line[channel::3]
        rows.append(bytes(row))  # BGR
    if height > 0:
        rows.reverse()  # bottom-up
    return original[:BMP_HEADER] + b"".join(rows)


def patched(image: bytes, logo_rows: list[bytes]) -> bytes:
    """The boot image with our logo in both BMPs and its id updated."""
    found = layout(image)
    result = bytearray(image)
    for start, size in found.logos:
        result[start:start + size] = logo_bmp(image[start:start + size], logo_rows)
    _, kernel_size, _, ramdisk_size, _, second_size = struct.unpack_from("<8sIIIII", image, 0)
    parts, offset = [], found.page
    for size in (kernel_size, ramdisk_size, second_size):
        parts.append((offset, size))
        offset += -(-size // found.page) * found.page
    result[found.id_offset:found.id_offset + 20] = _image_id(bytes(result), parts)
    return bytes(result)


def changed_blocks(old: bytes, new: bytes) -> list[tuple[int, int]]:
    """The (first block, count) runs of 512-byte blocks that differ, the header's block last."""
    runs, start = [], None
    for block in range(-(-len(new) // BLOCK)):
        differs = old[block * BLOCK:(block + 1) * BLOCK] != new[block * BLOCK:(block + 1) * BLOCK]
        if differs and start is None:
            start = block
        if not differs and start is not None:
            runs.append((start, block - start))
            start = None
    if start is not None:
        runs.append((start, -(-len(new) // BLOCK) - start))
    header = [run for run in runs if run[0] < 4]
    return [run for run in runs if run[0] >= 4] + header


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Partition:
    """The boot partition, read and written as root through sudo, or, under a test system root, a plain file."""

    def __init__(self, path: Path, *, password: str | None, run=subprocess.run, fake: bool = False):
        self.path, self.password, self.run, self.fake = path, password, run, fake

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> bytes:
        if self.fake:
            return self.path.read_bytes()
        result = self.run(sudo_command(["cat", str(self.path)]), input=(self.password or "").encode() + b"\n",
                          capture_output=True, timeout=120)
        if result.returncode:
            raise BootLogoError("The boot partition could not be read.")
        return result.stdout

    def write(self, data: bytes, runs: list[tuple[int, int]]) -> None:
        for first, count in runs:
            chunk = data[first * BLOCK:(first + count) * BLOCK]
            if self.fake:
                with self.path.open("r+b") as handle:
                    handle.seek(first * BLOCK)
                    handle.write(chunk)
                continue
            with tempfile.NamedTemporaryFile(prefix="vo-boot-logo-") as handle:
                handle.write(chunk)
                handle.flush()
                result = run_sudo(["dd", f"if={handle.name}", f"of={self.path}", f"bs={BLOCK}", f"seek={first}",
                                   f"count={count}", "conv=notrunc,fsync", "status=none"],
                                  run=self.run, password=self.password or "", timeout=120)
            if result.returncode:
                raise BootLogoError("The boot partition could not be written.")


def record_path(paths) -> Path:
    return paths.printer_data_root / RECORD


def _load_record(paths) -> dict | None:
    try:
        record = json.loads(record_path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and {"original", "written", "backup"} <= set(record) else None


def _backup_path(paths, sha256: str) -> Path:
    return paths.printer_data_root / BACKUP_DIR / f"boot-partition-{sha256[:16]}.img"


def _logo_rows() -> list[bytes]:
    return update_screen.decode_png(boot_screen.BOOT_LOGO_IMAGE.read_bytes())


def plan(partition: Partition) -> str:
    """What an install would do: "write", "current", "missing", or why the logo is left alone."""
    if not partition.exists():
        return "missing"
    try:
        current = partition.read()
        return "current" if patched(current, _logo_rows()) == current else "write"
    except BootLogoError as exc:
        return exc.message


def apply(paths, partition: Partition, *, reporter) -> bool:
    """Put Valve Oil's logo in the boot partition; whether it was written. Leaves an unreviewed partition alone."""
    if not partition.exists():
        return False
    current = partition.read()
    try:
        desired = patched(current, _logo_rows())
    except BootLogoError as exc:
        reporter.line(f"  Kept QIDI's boot logo: {exc.message}")
        return False
    if desired == current:
        return False
    record = _load_record(paths)
    if record is None or record.get("written") != _sha256(current):
        # QIDI's partition (or one QIDI changed since): save it before anything is written.
        backup = _backup_path(paths, _sha256(current))
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(current)
        if _sha256(backup.read_bytes()) != _sha256(current):
            raise BootLogoError("The boot partition's backup could not be verified.")
        record = {"original": _sha256(current), "backup": str(backup)}
    reporter.line("  Writing Valve Oil's boot logo...")
    runs = changed_blocks(current, desired)
    partition.write(desired, runs)
    if _sha256(partition.read()) != _sha256(desired):
        partition.write(current, runs)
        raise BootLogoError("The boot logo didn't read back as written; QIDI's was put back.")
    atomic_write_text(record_path(paths), json.dumps({**record, "written": _sha256(desired)}, indent=2) + "\n",
                      mode=0o644)
    return True


def restore(paths, partition: Partition, *, reporter) -> None:
    """Put QIDI's logo back while the partition holds what Valve Oil wrote; then forget the record and backup."""
    record = _load_record(paths)
    if record is None:
        return
    backup = Path(record["backup"])
    if partition.exists():
        current = partition.read()
        if _sha256(current) == record["written"]:
            try:
                original = backup.read_bytes()
            except OSError as exc:
                raise BootLogoError(f"The boot partition's backup is missing: {backup}") from exc
            if _sha256(original) != record["original"] or len(original) != len(current):
                raise BootLogoError(f"The boot partition's backup doesn't match its record: {backup}")
            reporter.line("  Putting QIDI's boot logo back...")
            partition.write(original, changed_blocks(current, original))
            if _sha256(partition.read()) != record["original"]:
                raise BootLogoError("QIDI's boot logo didn't read back as written.")
        elif _sha256(current) != record["original"]:
            reporter.line("  The boot partition changed since Valve Oil wrote its logo; it was left as it is.")
    atomic_delete(record_path(paths))
    backup.unlink(missing_ok=True)


def _partition(paths, environ, *, reporter, input_stream, run) -> Partition | None:
    """The printer's boot partition, or under a test system root its stand-in; None for any other test run."""
    root = environ.get(SYSTEM_ROOT_ENV)
    if root:
        return Partition(Path(root) / DEVICE.lstrip("/"), password=None, run=run, fake=True)
    if paths.printer_data_root != DEFAULT_PRINTER_DATA_ROOT:
        return None
    password = authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
    return Partition(Path(DEVICE), password=password, run=run)


def install(paths, *, reporter, environ, input_stream=None, run=subprocess.run) -> None:
    """An install's step: Valve Oil's boot logo, or QIDI's kept with the reason. Never fails the install."""
    try:
        partition = _partition(paths, environ, reporter=reporter, input_stream=input_stream, run=run)
        if partition is not None:
            apply(paths, partition, reporter=reporter)
    except (InstallerError, SudoError, OSError, subprocess.SubprocessError) as exc:
        reporter.line(f"  Kept QIDI's boot logo: {getattr(exc, 'message', None) or exc}")


def uninstall(paths, *, reporter, environ, input_stream=None, run=subprocess.run) -> None:
    """Uninstall's step: QIDI's boot logo back. A failure is reported with what to do; the uninstall goes on."""
    if _load_record(paths) is None:
        return
    try:
        partition = _partition(paths, environ, reporter=reporter, input_stream=input_stream, run=run)
        if partition is not None:
            restore(paths, partition, reporter=reporter)
    except (InstallerError, SudoError, OSError, subprocess.SubprocessError) as exc:
        reporter.line(f"  QIDI's boot logo could not be put back ({getattr(exc, 'message', None) or exc}); the "
                      f"partition QIDI shipped is saved in {_load_record(paths)['backup']}.")


def dry_run_line() -> str:
    return ("Boot logo dry-run: would replace QIDI's boot logo with Valve Oil's when the boot partition holds a boot "
            "image of the reviewed layout. Nothing was read or written.")
