#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/build_copy_dry_run.sh --host <printer-host> [--user <ssh-user>] [--password <ssh-password>]
         [--install] [--sudo-password <password>] [--install-timeout <seconds>] [--debug] [--no-smoke-test]

Environment:
  PRINTER_HOST            Printer hostname or IP. Required if --host is not passed.
  PRINTER_USER            SSH user. Default: qidi
  PRINTER_PASSWORD        SSH password. Optional. Requires sshpass when set.
  PRINTER_SUDO_PASSWORD   The printer user's sudo password for --install. Default: PRINTER_PASSWORD if set,
                          else none, and the installer uses QIDI's default.
  INSTALL_TIMEOUT         Seconds --install waits for the installer before stopping it. Default: 3600
  BUILD_ID                Dev bundle build id. Default: local

This script:
  1. Builds dist/valve-oil-dev.tar.gz
  2. Copies it to the printer home directory
  3. Extracts ~/valve-oil on the printer
  4. Runs ./install.sh --dry-run --plain
  5. With --install, runs ./install.sh --yes --plain and fails unless it installed

--install runs the installer without a terminal and returns as soon as it ends. The sudo password goes to the
printer on the installer's standard input, never on a command line; a wrong one fails the install at once
instead of waiting for someone to type. When the installer only reports how an earlier QIDI firmware update
ended, which it does once and then exits, the install is run once more.
EOF
}

die() {
  printf '%s\n' "$1" >&2
  exit 1
}

require_value() {
  local flag="$1"
  local value="${2-}"
  [ -n "$value" ] || die "Missing value for $flag"
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PRINTER_HOST=${PRINTER_HOST:-}
PRINTER_USER=${PRINTER_USER:-qidi}
PRINTER_PASSWORD=${PRINTER_PASSWORD:-}
BUILD_ID=${BUILD_ID:-local}
PRINTER_SUDO_PASSWORD=${PRINTER_SUDO_PASSWORD:-}
INSTALL_TIMEOUT=${INSTALL_TIMEOUT:-3600}
DEBUG_INSTALL=0
SMOKE_TEST=1
INSTALL=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      shift
      require_value "--host" "${1-}"
      PRINTER_HOST="$1"
      ;;
    --user)
      shift
      require_value "--user" "${1-}"
      PRINTER_USER="$1"
      ;;
    --password)
      shift
      require_value "--password" "${1-}"
      PRINTER_PASSWORD="$1"
      ;;
    --build-id)
      shift
      require_value "--build-id" "${1-}"
      BUILD_ID="$1"
      ;;
    --install)
      INSTALL=1
      ;;
    --sudo-password)
      shift
      require_value "--sudo-password" "${1-}"
      PRINTER_SUDO_PASSWORD="$1"
      ;;
    --install-timeout)
      shift
      require_value "--install-timeout" "${1-}"
      INSTALL_TIMEOUT="$1"
      ;;
    --debug)
      DEBUG_INSTALL=1
      ;;
    --no-smoke-test)
      SMOKE_TEST=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unsupported argument: $1"
      ;;
  esac
  shift
done

[ -n "$PRINTER_HOST" ] || die "Missing printer host. Use --host or PRINTER_HOST."
case "$INSTALL_TIMEOUT" in
  ''|*[!0-9]*) die "--install-timeout must be a whole number of seconds." ;;
esac
if [ -z "$PRINTER_SUDO_PASSWORD" ] && [ -n "$PRINTER_PASSWORD" ]; then
  PRINTER_SUDO_PASSWORD="$PRINTER_PASSWORD"
fi

for tool in python3 ssh scp; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing required tool: $tool"
done

SSH_CMD=(ssh)
SCP_CMD=(scp)
if [ -n "$PRINTER_PASSWORD" ]; then
  command -v sshpass >/dev/null 2>&1 || die "sshpass is required when --password or PRINTER_PASSWORD is set."
  SSH_CMD=(sshpass -p "$PRINTER_PASSWORD" ssh -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no)
  SCP_CMD=(sshpass -p "$PRINTER_PASSWORD" scp -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no)
fi

cd "$REPO_ROOT"

BUILD_CMD=(python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id "$BUILD_ID")
if [ "$SMOKE_TEST" -eq 1 ]; then
  BUILD_CMD+=(--smoke-test)
fi

printf 'Building dev installer bundle...\n'
"${BUILD_CMD[@]}"

BUNDLE_PATH="$REPO_ROOT/dist/valve-oil-dev.tar.gz"
[ -f "$BUNDLE_PATH" ] || die "Expected bundle not found: $BUNDLE_PATH"

SSH_TARGET="${PRINTER_USER}@${PRINTER_HOST}"
REMOTE_BUNDLE='~/valve-oil-dev.tar.gz'
REMOTE_DIR='~/valve-oil'

printf 'Copying bundle to %s...\n' "$SSH_TARGET"
"${SCP_CMD[@]}" "$BUNDLE_PATH" "$SSH_TARGET:$REMOTE_BUNDLE"

printf 'Extracting bundle on %s...\n' "$SSH_TARGET"
"${SSH_CMD[@]}" "$SSH_TARGET" "rm -rf $REMOTE_DIR && tar -xzf $REMOTE_BUNDLE -C ~/"

REMOTE_INSTALL_CMD='cd ~/valve-oil && ./install.sh --dry-run --plain'
if [ "$DEBUG_INSTALL" -eq 1 ]; then
  REMOTE_INSTALL_CMD="$REMOTE_INSTALL_CMD --debug"
fi

printf 'Running remote dry-run on %s...\n' "$SSH_TARGET"
"${SSH_CMD[@]}" "$SSH_TARGET" "$REMOTE_INSTALL_CMD"

[ "$INSTALL" -eq 1 ] || exit 0

INSTALL_FLAGS='--yes --plain'
if [ "$DEBUG_INSTALL" -eq 1 ]; then
  INSTALL_FLAGS="$INSTALL_FLAGS --debug"
fi
# The first line of standard input is the sudo password, if any; the installer itself reads none.
REMOTE_RUN_CMD="IFS= read -r p || p=''; cd ~/valve-oil || exit 1; if [ -n \"\$p\" ]; then VO_SUDO_PASSWORD=\"\$p\" ./install.sh $INSTALL_FLAGS </dev/null; else ./install.sh $INSTALL_FLAGS </dev/null; fi"
LOG_DIR=$(mktemp -d "${TMPDIR:-/tmp}/valve-oil-install.XXXXXX")
REPORTED_FIRMWARE_UPDATE='A QIDI firmware update ran after the session that started it ended.'

# Runs the installer once, printing its output as it comes and keeping a copy in $1. Returns the installer's exit
# code, or 124 when it was stopped after INSTALL_TIMEOUT seconds.
run_install() {
  local log="$1" pid watchdog rc
  printf '%s\n' "$PRINTER_SUDO_PASSWORD" | "${SSH_CMD[@]}" "$SSH_TARGET" "$REMOTE_RUN_CMD" >"$log" 2>&1 &
  pid=$!
  tail -n +1 -f "$log" &
  local tailer=$!
  (
    # Stopping the watchdog stops its sleep too.
    trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
    sleep "$INSTALL_TIMEOUT" &
    sleeper=$!
    wait "$sleeper"
    printf 'The installer had not finished after %s s; stopping ssh.\n' "$INSTALL_TIMEOUT" >>"$log"
    kill -TERM "$pid" 2>/dev/null
  ) &
  watchdog=$!
  rc=0
  wait "$pid" || rc=$?
  kill "$watchdog" 2>/dev/null || true
  wait "$watchdog" 2>/dev/null || true
  sleep 0.2
  kill "$tailer" 2>/dev/null || true
  wait "$tailer" 2>/dev/null || true
  if grep -q '^The installer had not finished after' "$log"; then
    return 124
  fi
  return "$rc"
}

for attempt in 1 2; do
  LOG="$LOG_DIR/install-$attempt.log"
  printf 'Installing on %s...\n' "$SSH_TARGET"
  rc=0
  run_install "$LOG" || rc=$?
  if [ "$rc" -eq 124 ]; then
    die "The installer was stopped after $INSTALL_TIMEOUT s. Its output is in $LOG."
  fi
  if [ "$rc" -ne 0 ]; then
    die "The installer failed with exit code $rc. Its output is in $LOG."
  fi
  if grep -q '^Installed\.' "$LOG"; then
    printf 'Installed on %s.\n' "$SSH_TARGET"
    exit 0
  fi
  if [ "$attempt" -eq 1 ] && grep -qF "$REPORTED_FIRMWARE_UPDATE" "$LOG"; then
    printf 'The installer only reported an earlier firmware update; running it again.\n'
    continue
  fi
  break
done
die "The installer exited without installing. Its output is in $LOG."
