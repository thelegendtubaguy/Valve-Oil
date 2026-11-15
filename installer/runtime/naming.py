from __future__ import annotations


REPOSITORY_URL = "https://github.com/thelegendtubaguy/valve-oil"
ISSUES_URL = f"{REPOSITORY_URL}/issues"
BUNDLE_NAME_PREFIX = "tltg-optimized-macros"
# The release archive: valve-oil.tar.gz, or valve-oil-dev.tar.gz for dev builds.
ARCHIVE_NAME_PREFIX = "valve-oil"
ARCHIVE_NAME = f"{ARCHIVE_NAME_PREFIX}.tar.gz"
# The bundle's folder, in the archive and in the printer user's home.
BUNDLE_ROOT_NAME = "valve-oil"
# Installs from 26.11.12.2 and earlier live in ~/tltg-optimized-macros and update themselves from the archive's
# earlier name, which they accept only with the earlier folder at its root. Releases also publish that archive.
LEGACY_BUNDLE_ROOT_NAME = BUNDLE_NAME_PREFIX
LEGACY_ARCHIVE_NAME = f"{BUNDLE_NAME_PREFIX}.tar.gz"
# Valve Oil's working files in the printer user's home: a folder each for the update check's downloads
# (auto-update), a QIDI firmware update's package and logs (firmware-update), and the jobs of the status and add-ons
# services (status, addons), whose scripts in installer/system name these paths themselves.
CACHE_DIRECTORY = ".cache/valve-oil"
# The same folders under their earlier names, which an install removes (auto_update.retire_legacy_bundle).
LEGACY_CACHE_DIRECTORIES = (".cache/tltg-auto-update", ".cache/tltg-firmware-update", ".cache/tltg-status",
                            ".cache/tltg-addons")
INSTALL_BACKUP_LABEL_PREFIX = f"{BUNDLE_NAME_PREFIX}-before-optimize"
UNINSTALL_BACKUP_LABEL_PREFIX = f"{BUNDLE_NAME_PREFIX}-before-uninstall"
