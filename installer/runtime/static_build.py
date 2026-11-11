from __future__ import annotations

import hashlib
import io
import shutil
import stat
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from .errors import InstallerError


# Package-built web front ends that nginx serves from their own directories. Each is a pinned zip archive that is
# verified and extracted whole, with a marker recording the archive it came from.
MAX_ENTRIES = 4096
MAX_BYTES = 128 * 1024 * 1024

ABSENT = "absent"
CURRENT = "current"
OUTDATED = "outdated"
FOREIGN = "foreign"


class StaticBuildError(InstallerError):
    pass


@dataclass(frozen=True)
class StaticBuild:
    operation: str
    # Names the build in messages.
    name: str
    destination: str
    # Marks the directory as package-owned and records the archive it was extracted from.
    marker: str

    # Updates stage the new build beside the destination and keep the replaced build until the host transaction ends.
    @property
    def staging(self) -> str:
        return f"{self.destination}.tltg-staging"

    @property
    def previous(self) -> str:
        return f"{self.destination}.tltg-previous"

    @property
    def removing(self) -> str:
        return f"{self.destination}.tltg-removing"

    # The build is only ever installed where nothing existed or over an earlier package build, so restoration is
    # removal.
    @property
    def preimage(self) -> dict[str, object]:
        return {"path": self.destination, "exists": False}

    @property
    def update_preimage(self) -> dict[str, object]:
        return {"path": self.destination, "exists": True, "previous": self.previous}

    def archive_files(self, archive: Path, sha256: str) -> dict[str, str]:
        """Return the verified archive's file hashes keyed by relative path."""
        return dict(_archive_files(self, str(archive), sha256))

    def installed_sha256(self, destination: Path) -> str | None:
        """Return the archive checksum recorded in a package-owned build directory, or None when it is not one."""
        marker = destination / self.marker
        if destination.is_symlink() or not destination.is_dir() or marker.is_symlink() or not marker.is_file():
            return None
        return marker.read_text(encoding="utf-8").strip()

    def state(self, destination: Path, *, hashes: dict[str, str], sha256: str) -> str:
        if not destination.exists() and not destination.is_symlink():
            return ABSENT
        installed = self.installed_sha256(destination)
        if installed is None:
            return FOREIGN
        if installed != sha256:
            return OUTDATED
        for relative, expected in hashes.items():
            target = destination / relative
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                return OUTDATED
        return CURRENT

    def extract(self, archive: Path, sha256: str, target: Path) -> None:
        """Extract the verified archive into a new directory readable by nginx, writing the marker last."""
        self.archive_files(archive, sha256)
        data = archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise StaticBuildError(f"{self.name} build checksum does not match the package manifest.")
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        with zipfile.ZipFile(io.BytesIO(data)) as archive_file:
            for member in archive_file.infolist():
                path = target / member.filename
                if member.is_dir():
                    path.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                with archive_file.open(member) as source, path.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                path.chmod(0o644)
        for directory in (item for item in target.rglob("*") if item.is_dir()):
            directory.chmod(0o755)
        marker = target / self.marker
        marker.write_text(f"{sha256}\n", encoding="utf-8")
        marker.chmod(0o644)

    def remove(self, directory: Path, removing: Path) -> None:
        """Remove a package-owned build, first moving it aside so nginx never serves a partial tree."""
        if self.installed_sha256(directory) is None:
            raise StaticBuildError(f"{directory} is not a package-owned {self.name} build; preserving it.")
        discard(removing)
        directory.rename(removing)
        discard(removing)


# The project's Fluidd fork; QIDI's bundled Fluidd stays untouched as the rollback.
FLUIDD = StaticBuild(
    operation="fluidd_build",
    name="Fluidd",
    destination="/home/qidi/tltg-fluidd",
    marker=".tltg-optimized-fluidd",
)
# Valve Oil UI, built from ui/ when the release bundle is built, and served beside Fluidd under /valve-oil/.
VALVE_OIL_UI = StaticBuild(
    operation="valve_oil_ui",
    name="Valve Oil UI",
    destination="/home/qidi/tltg-valve-oil-ui",
    marker=".tltg-optimized-valve-oil-ui",
)


@lru_cache(maxsize=4)
def _archive_files(build: StaticBuild, archive: str, sha256: str) -> tuple[tuple[str, str], ...]:
    try:
        data = Path(archive).read_bytes()
    except OSError as exc:
        raise StaticBuildError(f"{build.name} build is missing from the package.") from exc
    if hashlib.sha256(data).hexdigest() != sha256:
        raise StaticBuildError(f"{build.name} build checksum does not match the package manifest.")
    hashes: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive_file:
            members = archive_file.infolist()
            _validate_members(build, members)
            for member in members:
                if not member.is_dir():
                    hashes[member.filename] = hashlib.sha256(archive_file.read(member)).hexdigest()
    except (zipfile.BadZipFile, OSError) as exc:
        raise StaticBuildError(f"{build.name} build archive could not be read.") from exc
    if "index.html" not in hashes:
        raise StaticBuildError(f"{build.name} build archive has no index.html.")
    return tuple(sorted(hashes.items()))


def _validate_members(build: StaticBuild, members: list[zipfile.ZipInfo]) -> None:
    if len(members) > MAX_ENTRIES or sum(member.file_size for member in members) > MAX_BYTES:
        raise StaticBuildError(f"{build.name} build archive is too large.")
    for member in members:
        name = member.filename.rstrip("/") if member.is_dir() else member.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or path.as_posix() != name
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0] == build.marker
        ):
            raise StaticBuildError(f"{build.name} build archive contains an unsafe path.")
        kind = stat.S_IFMT(member.external_attr >> 16)
        if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (kind == stat.S_IFDIR) != member.is_dir():
            raise StaticBuildError(f"{build.name} build archive contains an unsafe entry type.")


def discard(directory: Path) -> None:
    """Delete a leftover staging, previous, or removal directory."""
    if directory.is_symlink():
        directory.unlink()
    elif directory.is_dir():
        shutil.rmtree(directory)
    elif directory.exists():
        directory.unlink()
