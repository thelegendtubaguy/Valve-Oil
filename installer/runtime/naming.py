from __future__ import annotations


REPOSITORY_URL = "https://github.com/thelegendtubaguy/valve-oil"
ISSUES_URL = f"{REPOSITORY_URL}/issues"
# The release archive: valve-oil.tar.gz, or valve-oil-dev.tar.gz for dev builds.
ARCHIVE_NAME_PREFIX = "valve-oil"
ARCHIVE_NAME = f"{ARCHIVE_NAME_PREFIX}.tar.gz"
# The bundle's folder, in the archive and in the printer user's home.
BUNDLE_ROOT_NAME = "valve-oil"
# Installs from 26.11.12.2 and earlier live in ~/tltg-optimized-macros and update themselves from the archive's
# earlier name, which they accept only with the earlier folder at its root. Releases also publish that archive.
LEGACY_BUNDLE_ROOT_NAME = "tltg-optimized-macros"
LEGACY_ARCHIVE_NAME = f"{LEGACY_BUNDLE_ROOT_NAME}.tar.gz"
# Valve Oil's working files in the printer user's home: a folder each for the update check's downloads
# (auto-update), a QIDI firmware update's package and logs (firmware-update), the jobs of the status and add-ons
# services (status, addons), whose scripts in installer/system name these paths themselves, and the USB hand-off's
# bundle copy and update-folder result (usb), which installer/usb/ names itself.
CACHE_DIRECTORY = ".cache/valve-oil"
# The same folders under their earlier names, which an install removes (auto_update.retire_legacy_bundle).
LEGACY_CACHE_DIRECTORIES = (".cache/tltg-auto-update", ".cache/tltg-firmware-update", ".cache/tltg-status",
                            ".cache/tltg-addons")
INSTALL_BACKUP_LABEL_PREFIX = "vo-before-install"
UNINSTALL_BACKUP_LABEL_PREFIX = "vo-before-uninstall"
# Configuration backups releases before 26.11.22.1 made keep their names and are listed and restored like new ones.
EARLIER_INSTALL_BACKUP_LABEL_PREFIX = "tltg-optimized-macros-before-optimize"
EARLIER_UNINSTALL_BACKUP_LABEL_PREFIX = "tltg-optimized-macros-before-uninstall"
# Files the package writes start with a marker line, "# vo:<name>"; releases before 26.11.22.1 wrote
# "# tltg-optimized:<name>". Either marks the file as the package's own.
MARKER_PREFIX = "# vo:"
EARLIER_MARKER_PREFIX = "# tltg-optimized:"


def earlier_marker(marker: str) -> str:
    """The marker an earlier release wrote in place of `marker`, a start or "# /vo:" end marker, keeping its
    indentation."""
    return marker.replace(MARKER_PREFIX, EARLIER_MARKER_PREFIX, 1).replace("# /vo:", "# /tltg-optimized:", 1)


def has_marker(text: str, marker: str) -> bool:
    """Whether `text` starts with `marker`, or with the marker an earlier release wrote in its place."""
    return text.startswith(marker) or text.startswith(earlier_marker(marker))
