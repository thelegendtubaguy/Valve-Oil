#!/bin/sh
# shellcheck disable=SC2015 # every `A && B || fail` fails when A or B does
# Valve Oil USB installer launcher (docs/under-the-hood.md, "USB installer").
#
# The USB hand-off's postinst copies this to /run/valve-oil-usb/ and starts it as root in a transient systemd unit:
#   launch.sh launch <printer_data> <drive mount point> <bundle sha256> <qidi-client InvocationID at the hand-off>
# It waits for QIDI's touchscreen app to restart after the package install and for Klipper, removes the hand-off
# package's dpkg record, gives the printer's user passwordless sudo for the unit's life, unpacks the bundle the
# postinst copied into the user's cache to ~/valve-oil as install-latest.sh does, and execs the installer's USB mode,
# which drops root to the printer's user. A failure before that leaves the app running and writes its log to the drive.
#
#   launch.sh cleanup
# is the unit's ExecStopPost: it removes the sudoers drop-in, however the unit ended.
#
# Overridable for tests: VO_USB_SUDOERS_DIR, VO_USB_TMPFILES_DIR, VO_USB_RUN_DIR, VO_USB_MOONRAKER_URL, and the
# waits in seconds, VO_USB_CLIENT_TIMEOUT, VO_USB_KLIPPER_TIMEOUT, VO_USB_KLIPPER_SETTLE, VO_USB_DPKG_TIMEOUT,
# VO_USB_POLL; and VO_USB_PYTHON, the interpreter the installer is exec'd with.

SUDOERS_DIR=${VO_USB_SUDOERS_DIR:-/etc/sudoers.d}
TMPFILES_DIR=${VO_USB_TMPFILES_DIR:-/etc/tmpfiles.d}
RUN_DIR=${VO_USB_RUN_DIR:-/run/valve-oil-usb}
MOONRAKER_URL=${VO_USB_MOONRAKER_URL:-http://127.0.0.1:7125}
CLIENT_TIMEOUT=${VO_USB_CLIENT_TIMEOUT:-180}
KLIPPER_TIMEOUT=${VO_USB_KLIPPER_TIMEOUT:-300}
KLIPPER_SETTLE=${VO_USB_KLIPPER_SETTLE:-60}
DPKG_TIMEOUT=${VO_USB_DPKG_TIMEOUT:-120}
POLL=${VO_USB_POLL:-2}
# The interpreter the installer runs under.
PYTHON=${VO_USB_PYTHON:-python3}
HANDOFF_PACKAGE=valve-oil-usb-handoff
TOUCHSCREEN=qidi-client.service
# No dot in the name: sudo skips drop-ins whose names contain one. tmpfiles removes both at the next boot, in case
# power is lost before the unit's ExecStopPost runs.
SUDOERS_NAME=valve-oil-usb-install
TMPFILES_NAME=valve-oil-usb-install.conf
BUNDLE=valve-oil.tar.gz
BUNDLE_NAME=valve-oil

cleanup() {
  rm -f "$SUDOERS_DIR/$SUDOERS_NAME" "$SUDOERS_DIR/.$SUDOERS_NAME.tmp" "$TMPFILES_DIR/$TMPFILES_NAME"
  rm -rf "$RUN_DIR"
}

log() {
  printf '%s launch: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

# Leave QIDI's app running, and copy the log to the drive, where the owner can read it without SSH.
fail() {
  log "FAILED: $*"
  cleanup
  systemctl start "$TOUCHSCREEN" || log "could not start $TOUCHSCREEN"
  if [ -n "$DRIVE" ] && [ -d "$DRIVE/valve-oil" ]; then
    copy="$DRIVE/valve-oil/logs/valve-oil-usb-handoff-$(date '+%Y%m%d-%H%M%S').log"
    if mkdir -p "$DRIVE/valve-oil/logs" && cp "$LOG" "$copy"; then
      sync
      log "copied this log to $copy"
    fi
  fi
  exit 1
}

# Seconds since $1, a `date +%s`.
since() {
  echo $(($(date +%s) - $1))
}

wait_for_touchscreen() {
  start=$(date +%s)
  while :; do
    current=$(systemctl show -p InvocationID --value "$TOUCHSCREEN" 2>/dev/null)
    if [ "$current" != "$CLIENT" ] && [ "$(systemctl is-active "$TOUCHSCREEN" 2>/dev/null)" = active ]; then
      log "$TOUCHSCREEN restarted after $(since "$start")s"
      return 0
    fi
    [ "$(since "$start")" -lt "$CLIENT_TIMEOUT" ] || fail "$TOUCHSCREEN did not restart within ${CLIENT_TIMEOUT}s"
    sleep "$POLL"
  done
}

# Klipper's state through Moonraker: ready, or error or shutdown held for KLIPPER_SETTLE seconds, which the installer
# then reports itself. Exits 1 at the timeout.
KLIPPER_WAIT='
import json, sys, time, urllib.request
url, timeout, settle, poll = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
start = time.monotonic()
state, since = None, start
while time.monotonic() - start < timeout:
    try:
        with urllib.request.urlopen(url + "/printer/info", timeout=5) as response:
            now = json.load(response)["result"]["state"]
    except Exception:
        now = None
    if now != state:
        state, since = now, time.monotonic()
    if state == "ready" or (state in ("error", "shutdown") and time.monotonic() - since >= settle):
        print(state)
        sys.exit(0)
    time.sleep(poll)
print(state)
sys.exit(1)
'

wait_for_klipper() {
  state=$(python3 -I -c "$KLIPPER_WAIT" "$MOONRAKER_URL" "$KLIPPER_TIMEOUT" "$KLIPPER_SETTLE" "$POLL") \
    || fail "Klipper was not ready within ${KLIPPER_TIMEOUT}s (last state: ${state:-no answer from Moonraker})"
  log "Klipper is $state"
}

# The app's dpkg holds the lock until the postinst has returned; the record is harmless if it stays.
purge_handoff_package() {
  start=$(date +%s)
  while status=$(dpkg-query -W -f '${Status}' "$HANDOFF_PACKAGE" 2>/dev/null); do
    case $status in ''|*not-installed) return 0 ;; esac
    if dpkg --purge "$HANDOFF_PACKAGE"; then
      log "removed the $HANDOFF_PACKAGE package record"
      return 0
    fi
    if [ "$(since "$start")" -ge "$DPKG_TIMEOUT" ]; then
      log "could not remove the $HANDOFF_PACKAGE package record within ${DPKG_TIMEOUT}s; continuing"
      return 0
    fi
    sleep "$POLL"
  done
}

grant_sudo() {
  case $USER_NAME in
    ''|*[!a-z0-9_-]*) fail "the printer's user name '$USER_NAME' is not one sudo can be given to" ;;
  esac
  mkdir -p "$TMPFILES_DIR" "$SUDOERS_DIR"
  printf 'r %s\nr %s\n' "$SUDOERS_DIR/$SUDOERS_NAME" "$TMPFILES_DIR/$TMPFILES_NAME" > "$TMPFILES_DIR/$TMPFILES_NAME" \
    || fail "could not write $TMPFILES_DIR/$TMPFILES_NAME"
  temporary="$SUDOERS_DIR/.$SUDOERS_NAME.tmp"
  printf '# Valve Oil USB installer: removed when its unit stops, or at the next boot.\n%s ALL=(ALL) NOPASSWD: ALL\n' \
    "$USER_NAME" > "$temporary" || fail "could not write $temporary"
  chown 0:0 "$temporary" && chmod 0440 "$temporary" || fail "could not set the owner and mode of $temporary"
  if command -v visudo >/dev/null 2>&1 && ! visudo -cqf "$temporary"; then
    fail "visudo rejected the sudoers drop-in"
  fi
  mv "$temporary" "$SUDOERS_DIR/$SUDOERS_NAME" || fail "could not install the sudoers drop-in"
  log "gave $USER_NAME passwordless sudo until the unit stops"
}

# Unpack the bundle as install-latest.sh does: one valve-oil/ root holding install.sh, regular files and folders only,
# no absolute or parent paths, no links; then replace ~/valve-oil, owned by the printer's user.
unpack_bundle() {
  archive="$HOME_DIR/.cache/valve-oil/usb/$BUNDLE"
  [ -f "$archive" ] || fail "the bundle was not copied from the drive (see the postinst lines above)"
  [ "$(sha256sum < "$archive" | cut -d' ' -f1)" = "$BUNDLE_SHA256" ] || fail "$archive does not match the zip's pin"
  stage=$(mktemp -d "$HOME_DIR/.valve-oil-usb.XXXXXX") || fail "could not make a staging folder in $HOME_DIR"
  members="$stage/archive.members"
  listing="$stage/archive.listing"
  tar -tzf "$archive" > "$members" && tar -tvzf "$archive" > "$listing" || { rm -rf "$stage"; fail "could not list $archive"; }
  problem=
  if grep -E '(^/|(^|/)\.\.(/|$))' "$members" >/dev/null 2>&1; then
    problem="unsafe member paths"
  elif grep -Ev "^${BUNDLE_NAME}(/|\$)" "$members" >/dev/null 2>&1; then
    problem="a root other than ${BUNDLE_NAME}/"
  elif ! grep -Fx "${BUNDLE_NAME}/install.sh" "$members" >/dev/null 2>&1; then
    problem="no ${BUNDLE_NAME}/install.sh"
  elif grep -Ev '^[-d]' "$listing" >/dev/null 2>&1; then
    problem="unsupported member types"
  fi
  [ -z "$problem" ] || { rm -rf "$stage"; fail "the bundle has $problem"; }
  mkdir "$stage/extract" && tar -xzf "$archive" -C "$stage/extract" --no-same-owner \
    || { rm -rf "$stage"; fail "could not unpack $archive"; }
  if find "$stage/extract/$BUNDLE_NAME" -type l | grep . >/dev/null 2>&1; then
    rm -rf "$stage"
    fail "the bundle has symbolic links"
  fi
  [ -f "$stage/extract/$BUNDLE_NAME/install.sh" ] || { rm -rf "$stage"; fail "the bundle has no install.sh"; }
  chown -R "$OWNER" "$stage/extract/$BUNDLE_NAME" || { rm -rf "$stage"; fail "could not give the bundle to $USER_NAME"; }
  rm -rf "$INSTALL_DIR"
  mv "$stage/extract/$BUNDLE_NAME" "$INSTALL_DIR" || { rm -rf "$stage"; fail "could not move the bundle to $INSTALL_DIR"; }
  rm -rf "$stage"
  rm -f "$archive"
  log "unpacked the bundle to $INSTALL_DIR"
}

launch() {
  PRINTER_DATA=$1
  DRIVE=$2
  BUNDLE_SHA256=$3
  CLIENT=$4
  HOME_DIR=${PRINTER_DATA%/*}
  INSTALL_DIR=$HOME_DIR/$BUNDLE_NAME
  LOG=$PRINTER_DATA/logs/valve-oil-usb-handoff.log
  umask 022
  # The installer's own output goes to the unit's journal; this script's to the hand-off log.
  exec 3>&1 4>&2 >> "$LOG" 2>&1
  log "started (drive: ${DRIVE:-none})"
  # shellcheck disable=SC2046 # ls's fields
  set -- $(ls -ld "$PRINTER_DATA" 2>/dev/null)
  USER_NAME=$3
  # shellcheck disable=SC2046
  set -- $(ls -ldn "$PRINTER_DATA" 2>/dev/null)
  OWNER="$3:$4"
  [ -n "$4" ] && [ "$3" != 0 ] || fail "could not find the user that owns $PRINTER_DATA"

  wait_for_touchscreen
  wait_for_klipper
  purge_handoff_package
  grant_sudo
  unpack_bundle
  set -- "$PYTHON" -I -S "$INSTALL_DIR/installer/runtime/bootstrap.py" usb
  [ -n "$DRIVE" ] && set -- "$@" --drive "$DRIVE"
  log "running: $*"
  exec "$@" 1>&3 2>&4 3>&- 4>&-
}

case ${1:-} in
  launch)
    shift
    launch "$@"
    ;;
  cleanup)
    cleanup
    ;;
  *)
    echo "usage: $0 launch <printer_data> <drive> <bundle sha256> <qidi-client invocation> | cleanup" >&2
    exit 2
    ;;
esac
