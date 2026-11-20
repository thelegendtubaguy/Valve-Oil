#!/bin/sh
# shellcheck disable=SC2015 # every `A && B || fail` fails when A or B does
# Valve Oil USB installer launcher (docs/under-the-hood.md, "USB installer").
#
# The USB hand-off's postinst copies this, update_screen.py, and starting.png to /run/valve-oil-usb/ and starts it as
# root in a transient systemd unit:
#   launch.sh launch <printer_data> <drive mount point> <bundle sha256> <qidi-client InvocationID at the hand-off>
# It waits for QIDI's touchscreen app to restart after the package install, then stops the app and shows the starting
# screen, so Valve Oil is on the touchscreen from then on. While Klipper starts, it removes the hand-off package's dpkg
# record, gives the printer's user passwordless sudo for the unit's life, and unpacks the bundle the postinst copied into
# the user's cache to ~/valve-oil as install-latest.sh does. Once Klipper is ready, it execs the installer's USB mode,
# which drops root to the printer's user and takes the stopped app over (install_screen.held). A failure before that
# starts the app again and writes its log to the drive.
#
#   launch.sh cleanup
# is the unit's ExecStopPost: it removes the sudoers drop-in, however the unit ended, and starts the app when the
# installer never took it over.
#
# Overridable for tests: VO_USB_SUDOERS_DIR, VO_USB_TMPFILES_DIR, VO_USB_RUN_DIR, VO_USB_MOONRAKER_URL, and the
# waits in seconds, VO_USB_CLIENT_TIMEOUT, VO_USB_KLIPPER_TIMEOUT, VO_USB_KLIPPER_SETTLE, VO_USB_DPKG_TIMEOUT,
# VO_USB_POLL; VO_USB_PYTHON, the interpreter the installer is exec'd with; and VO_USB_SCREEN_PYTHON, the one that
# draws the starting screen.

SUDOERS_DIR=${VO_USB_SUDOERS_DIR:-/etc/sudoers.d}
TMPFILES_DIR=${VO_USB_TMPFILES_DIR:-/etc/tmpfiles.d}
RUN_DIR=${VO_USB_RUN_DIR:-/run/valve-oil-usb}
MOONRAKER_URL=${VO_USB_MOONRAKER_URL:-http://127.0.0.1:7125}
CLIENT_TIMEOUT=${VO_USB_CLIENT_TIMEOUT:-180}
KLIPPER_TIMEOUT=${VO_USB_KLIPPER_TIMEOUT:-300}
KLIPPER_SETTLE=${VO_USB_KLIPPER_SETTLE:-60}
DPKG_TIMEOUT=${VO_USB_DPKG_TIMEOUT:-120}
POLL=${VO_USB_POLL:-2}
# The interpreter the installer runs under, and the one that draws the starting screen.
PYTHON=${VO_USB_PYTHON:-python3}
SCREEN_PYTHON=${VO_USB_SCREEN_PYTHON:-python3}
HANDOFF_PACKAGE=valve-oil-usb-handoff
TOUCHSCREEN=qidi-client.service
# No dot in the name: sudo skips drop-ins whose names contain one. tmpfiles removes both at the next boot, in case
# power is lost before the unit's ExecStopPost runs.
SUDOERS_NAME=valve-oil-usb-install
TMPFILES_NAME=valve-oil-usb-install.conf
BUNDLE=valve-oil.tar.gz
BUNDLE_NAME=valve-oil
# In the printer user's cache while the app is stopped for the starting screen; the installer removes it when it takes
# the app over (usb_install.TOUCHSCREEN_STOPPED). Its path is kept in $RUN_DIR/touchscreen for the cleanup.
TOUCHSCREEN_STOPPED=touchscreen-stopped

# Start the app when the launcher stopped it and the installer didn't take it over.
release_touchscreen() {
  stopped=$(cat "$RUN_DIR/touchscreen" 2>/dev/null)
  [ -n "$stopped" ] && [ -e "$stopped" ] || return 0
  rm -f "$stopped"
  systemctl start "$TOUCHSCREEN"
}

cleanup() {
  rm -f "$SUDOERS_DIR/$SUDOERS_NAME" "$SUDOERS_DIR/.$SUDOERS_NAME.tmp" "$TMPFILES_DIR/$TMPFILES_NAME"
  release_touchscreen
  rm -rf "$RUN_DIR"
}

log() {
  printf '%s launch: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

# Leave QIDI's app running, and copy the log to the drive, where the owner can read it without SSH.
fail() {
  log "FAILED: $*"
  rm -f "$(cat "$RUN_DIR/touchscreen" 2>/dev/null)"
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

# Wait for Klipper in the background, while the bundle unpacks; klipper_ready collects the answer.
wait_for_klipper() {
  rm -f "$RUN_DIR/klipper"
  (
    state=$(python3 -I -c "$KLIPPER_WAIT" "$MOONRAKER_URL" "$KLIPPER_TIMEOUT" "$KLIPPER_SETTLE" "$POLL")
    echo "$? $state" > "$RUN_DIR/klipper"
  ) &
  KLIPPER_WAIT_PID=$!
}

klipper_ready() {
  wait "$KLIPPER_WAIT_PID"
  read -r code state < "$RUN_DIR/klipper" 2>/dev/null || code=1
  [ "$code" = 0 ] \
    || fail "Klipper was not ready within ${KLIPPER_TIMEOUT}s (last state: ${state:-no answer from Moonraker})"
  log "Klipper is $state"
}

# Draw $1, a still image, with update_screen.py beside this script; exit 1 when it can't be drawn.
DRAW_SCREEN='
import subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import update_screen as screen
screen.light(lambda command: subprocess.run(command))
fallback = ((4, screen.TEXT, "Starting the Valve Oil"), (4, screen.TEXT, "installer..."))
sys.exit(0 if screen.draw(screen.Image(Path(sys.argv[2]), fallback)) else 1)
'

# Stop the app the package install restarted and show the starting screen until the installer draws its own. Without
# the screen's files, or when it can't be drawn, the app stays on, and the installer takes it over as before.
show_starting_screen() {
  [ -f "$RUN_DIR/update_screen.py" ] && [ -f "$RUN_DIR/starting.png" ] \
    || { log "the hand-off has no starting screen; $TOUCHSCREEN stays on"; return 0; }
  stopped="$HOME_DIR/.cache/valve-oil/usb/$TOUCHSCREEN_STOPPED"
  mkdir -p "${stopped%/*}" && printf 'launch.sh\n' > "$stopped" && chown "$OWNER" "$stopped" \
    && printf '%s\n' "$stopped" > "$RUN_DIR/touchscreen" \
    || { rm -f "$stopped"; log "could not record the stopped app; $TOUCHSCREEN stays on"; return 0; }
  if ! systemctl stop "$TOUCHSCREEN"; then
    release_touchscreen
    log "could not stop $TOUCHSCREEN; it stays on"
    return 0
  fi
  if "$SCREEN_PYTHON" -I -c "$DRAW_SCREEN" "$RUN_DIR" "$RUN_DIR/starting.png"; then
    log "showing the starting screen"
  else
    release_touchscreen
    log "could not draw the starting screen; started $TOUCHSCREEN again"
  fi
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

# Members of the bundle, checked as each is read and unpacked in one pass: one valve-oil/ root, regular files and
# folders only, no absolute or parent paths. Prints the problem and exits 3 for a bundle that breaks a rule.
UNPACK='
import sys, tarfile
archive, destination, root = sys.argv[1], sys.argv[2], sys.argv[3]
# Python 3.12 and later also apply their own checks; the printer runs 3.9.
safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
with tarfile.open(archive, "r|gz") as tar:
    for member in tar:
        parts = member.name.split("/")
        if member.name.startswith("/") or ".." in parts:
            problem = "unsafe member paths"
        elif parts[0] != root:
            problem = "a root other than " + root + "/"
        elif not (member.isfile() or member.isdir()):
            problem = "unsupported member types"
        else:
            tar.extract(member, destination, **safe)
            continue
        print(problem)
        sys.exit(3)
'

# Unpack the bundle as install-latest.sh does: one valve-oil/ root holding install.sh, regular files and folders only,
# no absolute or parent paths, no links; then replace ~/valve-oil, owned by the printer's user.
unpack_bundle() {
  archive="$HOME_DIR/.cache/valve-oil/usb/$BUNDLE"
  [ -f "$archive" ] || fail "the bundle was not copied from the drive (see the postinst lines above)"
  [ "$(sha256sum < "$archive" | cut -d' ' -f1)" = "$BUNDLE_SHA256" ] || fail "$archive does not match the zip's pin"
  stage=$(mktemp -d "$HOME_DIR/.valve-oil-usb.XXXXXX") || fail "could not make a staging folder in $HOME_DIR"
  mkdir "$stage/extract" || { rm -rf "$stage"; fail "could not make a staging folder in $HOME_DIR"; }
  problem=$(python3 -I -c "$UNPACK" "$archive" "$stage/extract" "$BUNDLE_NAME")
  case $? in
    0) ;;
    3) rm -rf "$stage"; fail "the bundle has $problem" ;;
    *) rm -rf "$stage"; fail "could not unpack $archive" ;;
  esac
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
  show_starting_screen
  wait_for_klipper
  purge_handoff_package
  grant_sudo
  unpack_bundle
  klipper_ready
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
