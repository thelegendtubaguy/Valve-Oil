#!/bin/sh
set -eu

# TLTG_INSTALLER_ARCHIVE_URL and TLTG_INSTALLER_CHECKSUM_URL are the names instructions before 26.11.22.3 used.
ARCHIVE_URL=${VO_INSTALLER_ARCHIVE_URL:-${TLTG_INSTALLER_ARCHIVE_URL:-https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/valve-oil.tar.gz}}
CHECKSUM_URL=${VO_INSTALLER_CHECKSUM_URL:-${TLTG_INSTALLER_CHECKSUM_URL:-${ARCHIVE_URL}.sha256}}
ARCHIVE_NAME=${ARCHIVE_URL##*/}
ARCHIVE_NAME=${ARCHIVE_NAME%%\?*}
ARCHIVE_NAME=${ARCHIVE_NAME%%#*}
CHECKSUM_NAME=${ARCHIVE_NAME}.sha256

if [ -z "${HOME:-}" ]; then
  echo "HOME is not set." >&2
  exit 1
fi
INSTALL_PARENT=${HOME}
PRINTER_OWNER=
if [ "$(id -u)" -eq 0 ]; then
  # The installer runs as the user who owns printer_data, so unpack it in that
  # user's home, owned by that user, rather than root's.
  PRINTER_UID=$(stat -c %u "${VO_PRINTER_DATA_ROOT:-${TLTG_OPTIMIZED_PRINTER_DATA_ROOT:-/home/qidi/printer_data}}" 2>/dev/null || true)
  if [ -n "$PRINTER_UID" ] && [ "$PRINTER_UID" != 0 ]; then
    PRINTER_ENTRY=$(getent passwd "$PRINTER_UID" || true)
    if [ -n "$PRINTER_ENTRY" ]; then
      INSTALL_PARENT=$(printf '%s\n' "$PRINTER_ENTRY" | cut -d: -f6)
      PRINTER_OWNER=$(printf '%s\n' "$PRINTER_ENTRY" | cut -d: -f3,4)
    fi
  fi
fi
BUNDLE_NAME=valve-oil
INSTALL_DIR=${INSTALL_PARENT}/${BUNDLE_NAME}

for tool in curl tar mktemp rm mkdir mv grep find; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required tool: $tool" >&2
    exit 1
  fi
done

TMP_ROOT=${TMPDIR:-/tmp}
TMP_DIR=$(mktemp -d "$TMP_ROOT/valve-oil.XXXXXX")
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT
trap 'trap - EXIT; cleanup; exit 130' INT HUP TERM

echo "Downloading Valve Oil Installer..."
curl -fL --retry 3 --connect-timeout 15 --progress-bar "$ARCHIVE_URL" -o "$TMP_DIR/$ARCHIVE_NAME"
curl -fsSL --retry 3 --connect-timeout 15 "$CHECKSUM_URL" -o "$TMP_DIR/$CHECKSUM_NAME"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$TMP_DIR" && sha256sum -c "$CHECKSUM_NAME")
elif command -v shasum >/dev/null 2>&1; then
  (cd "$TMP_DIR" && shasum -a 256 -c "$CHECKSUM_NAME")
else
  echo "Missing required tool: sha256sum or shasum" >&2
  exit 1
fi

MEMBERS_FILE="$TMP_DIR/archive.members"
LISTING_FILE="$TMP_DIR/archive.listing"
tar -tzf "$TMP_DIR/$ARCHIVE_NAME" > "$MEMBERS_FILE"
tar -tvzf "$TMP_DIR/$ARCHIVE_NAME" > "$LISTING_FILE"
if grep -E '(^/|(^|/)\.\.(/|$))' "$MEMBERS_FILE" >/dev/null 2>&1; then
  echo "Archive contains unsafe member paths." >&2
  exit 1
fi
if grep -Ev "^${BUNDLE_NAME}(/|\$)" "$MEMBERS_FILE" >/dev/null 2>&1; then
  echo "Archive does not use the expected ${BUNDLE_NAME}/ root." >&2
  exit 1
fi
if ! grep -Fx "${BUNDLE_NAME}/install.sh" "$MEMBERS_FILE" >/dev/null 2>&1; then
  echo "Archive is missing ${BUNDLE_NAME}/install.sh." >&2
  exit 1
fi
if grep -Ev '^[-d]' "$LISTING_FILE" >/dev/null 2>&1; then
  echo "Archive contains unsupported member types." >&2
  exit 1
fi

STAGE_DIR="$TMP_DIR/extract"
mkdir -p "$STAGE_DIR"
tar -xzf "$TMP_DIR/$ARCHIVE_NAME" -C "$STAGE_DIR"
if find "$STAGE_DIR/${BUNDLE_NAME}" -type l | grep . >/dev/null 2>&1; then
  echo "Archive contains symbolic links." >&2
  exit 1
fi
if [ ! -f "$STAGE_DIR/${BUNDLE_NAME}/install.sh" ]; then
  echo "Archive is missing ${BUNDLE_NAME}/install.sh." >&2
  exit 1
fi

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_PARENT"
mv "$STAGE_DIR/${BUNDLE_NAME}" "$INSTALL_DIR"
if [ -n "$PRINTER_OWNER" ]; then
  chown -R "$PRINTER_OWNER" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
./install.sh "$@"
