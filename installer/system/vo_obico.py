# tltg-optimized:obico-launcher
"""Starts moonraker-obico for Valve Oil's Obico add-on.

Valve Oil installs this beside the plugin's source and runs it in place of `python3 -m moonraker_obico.app`. Before the
plugin starts, it changes three things:

- The plugin adds `[include moonraker_obico_macros.cfg]` to printer.cfg and links its macros into the config folder
  whenever Klipper lacks them. Add-ons don't change printer_data/config, so that step does nothing here. Without the
  macros the plugin only logs that they're missing; they serve nozzle-camera first-layer scans and KlipperScreen.
- The plugin picks its bundled Janus WebRTC server by board name and knows only Raspberry Pi and MKS boards, so on a
  QIDI printer it finds none and streams no video. QIDI's Debian 11 runs the Raspberry Pi Debian 11 build.
- The plugin shows its link code only through a Klipper macro. Here it is written to TLTG_OBICO_STATUS instead,
  where Valve Oil's add-ons service reads it for Fluidd.
- Without an H.264 encoder, which QIDI's boards lack, the plugin streams the camera by fetching full-size snapshots
  and sending them to Janus about 1.5 times a second, whether or not anyone is watching: a quarter of a CPU core on a
  Max 4. Here it sends them only while Obico's server says someone is viewing the printer.

A plugin release without what these changes replace fails to start, so its install is rolled back.
"""

from __future__ import annotations

import json
import logging
import os
import runpy
import sys
import threading
import time

STATUS_PATH = os.environ.get("TLTG_OBICO_STATUS", "")
JANUS_BOARD = "rpi"
# How often the paused camera stream checks whether someone started viewing.
VIEWING_POLL_SECONDS = 0.5
# Obico's server reports nobody viewing for a few seconds at a time while someone is; the stream keeps running until
# that has lasted this long.
VIEWING_GRACE_SECONDS = 60.0

_logger = logging.getLogger("obico.tltg_launcher")
_app_model = None
_viewing = None
_last_viewed = float("-inf")

_status_lock = threading.Lock()
_status: dict = {"discovering": False, "one_time_passcode": ""}


def _write_status(**changes) -> None:
    with _status_lock:
        updated = {**_status, **changes}
        if updated == _status and changes:
            return
        _status.update(updated)
        if not STATUS_PATH:
            return
        tmp = f"{STATUS_PATH}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(_status, handle)
            os.replace(tmp, STATUS_PATH)
        except OSError:
            pass


def _require(owner, name: str):
    if not hasattr(owner, name):
        raise SystemExit(f"moonraker-obico has no {owner.__name__}.{name}; Valve Oil's Obico launcher needs updating.")
    return getattr(owner, name)


def install_patches() -> None:
    from moonraker_obico import utils

    _require(utils, "board_id")
    # janus_config_builder reads board_id when it is first imported, so this goes before anything imports it.
    if "moonraker_obico.janus_config_builder" in sys.modules:
        raise SystemExit("moonraker_obico.janus_config_builder was imported before the launcher could set its board.")
    utils.board_id = lambda: JANUS_BOARD

    from moonraker_obico import moonraker_conn, printer_discovery

    _require(moonraker_conn.MoonrakerConn, "_setup_include_cfgs")
    moonraker_conn.MoonrakerConn._setup_include_cfgs = lambda self: None

    discovery = printer_discovery.PrinterDiscovery
    _require(discovery, "set_obico_link_status")
    start_and_block = _require(discovery, "start_and_block")

    # The plugin's version only sets the link macro's variables, and without the macro it logs that every 2 seconds.
    def set_obico_link_status(self, is_linked, one_time_passcode, one_time_passlink):
        _write_status(one_time_passcode=str(one_time_passcode or ""))

    def start_and_block_reporting(self, *args, **kwargs):
        _write_status(discovering=True)
        try:
            return start_and_block(self, *args, **kwargs)
        finally:
            _write_status(discovering=False, one_time_passcode="")

    discovery.set_obico_link_status = set_obico_link_status
    discovery.start_and_block = start_and_block_reporting

    from moonraker_obico import webcam_stream

    streamer = webcam_stream.WebcamStreamer
    streamer_init = _require(streamer, "__init__")
    capture_jpeg = _require(webcam_stream, "capture_jpeg")
    nested = {const.co_name for const in _require(streamer, "mjpeg_webrtc").__code__.co_consts if hasattr(const, "co_name")}
    if "mjpeg_loop" not in nested:
        raise SystemExit("moonraker-obico's mjpeg_webrtc has no mjpeg_loop; Valve Oil's Obico launcher needs updating.")

    def init_recording_model(self, server_conn, moonrakerconn, client_conn, app_model, sentry):
        global _app_model
        _app_model = app_model
        streamer_init(self, server_conn, moonrakerconn, client_conn, app_model, sentry)

    def capture_jpeg_while_viewed(webcam_config, *args, **kwargs):
        # Only the live stream's loop waits; the resolution check and the snapshots for AI detection don't.
        if sys._getframe(1).f_code.co_name == "mjpeg_loop":
            _wait_for_viewer()
        return capture_jpeg(webcam_config, *args, **kwargs)

    streamer.__init__ = init_recording_model
    webcam_stream.capture_jpeg = capture_jpeg_while_viewed


def _viewed() -> bool:
    global _last_viewed
    status = getattr(_app_model, "remote_status", None)
    # Before the plugin has its model, stream as it would.
    if not isinstance(status, dict) or status.get("viewing"):
        _last_viewed = time.monotonic()
        return True
    return time.monotonic() - _last_viewed < VIEWING_GRACE_SECONDS


def _wait_for_viewer() -> None:
    global _viewing
    while not _viewed():
        if _viewing is not False:
            _viewing = False
            _logger.info("Nobody has viewed the printer for a minute; the camera stream is paused.")
        time.sleep(VIEWING_POLL_SECONDS)
    if _viewing is not True:
        _viewing = True
        _logger.info("Someone is viewing the printer; the camera stream is running.")


def main() -> None:
    # A code from an earlier run is no longer valid.
    _write_status()
    install_patches()
    sys.argv[0] = "moonraker_obico.app"
    runpy.run_module("moonraker_obico.app", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
