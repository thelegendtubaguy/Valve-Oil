## Why

QIDI's touchscreen app does far more than draw the screen. It records each print's position for power-loss resume and resumes prints after some Klipper errors, mounts USB drives, rewrites `MCU_ID.cfg`, saves `box_count`, pauses prints for the AI service, decrypts the QIDI cloud credentials for QIDI's Moonraker `mqtt` component, reports the printer to QIDI's cloud and runs its P2P remote access, and announces the printer to QIDI Studio on the LAN. Change `add-valve-oil-touchscreen` lets an operator stop QIDI's app and put Valve Oil's on the screen, which would silently take all of that away. Valve Oil already took `box.cfg` from the app for the same reason: two programs doing one job race, and the app's version restarted Klipper during prints.

The owner also wants QIDI's AI detection off for good, so nothing is left for the app's AI pause to do, and the printer off QIDI's servers for good. QIDI's P2P remote access raised security concerns (`installer-lifecycle`), and QIDI's Moonraker `mqtt` component connects only to QIDI's cloud, so an operator can't point Moonraker's MQTT at their own broker, for example for Home Assistant.

This change makes every duty of QIDI's app on the printer and its network Valve Oil's, on every supported printer, whichever app is on the screen, and patches them out of QIDI's app so nothing races. The app's QIDI cloud work is patched out and done by nothing, and Valve Oil keeps the printer from reaching QIDI's servers. It ships before the Valve Oil touchscreen, with QIDI's app still on the screen, so each duty is proven before QIDI's app is ever stopped.

## What Changes

- Valve Oil takes over, on the Max 4, Plus 5, and Smart 5, whichever app is on the screen:
  - recording the print state power-loss resume reads, in the keys and form QIDI's Moonraker reads;
  - resuming a print after a Klipper error that QIDI's app resumes from;
  - writing `MCU_ID.cfg` and removing `SAVE_CONFIG`'s `printer-*.cfg` backups at startup, as QIDI's app does, and saving `box_count`;
  - the Moonraker agent QIDI's components call: the print file list and statistics, the official filament list, and the error list;
  - the SSDP announcement and `mkswifi` discovery reply QIDI Studio finds the printer with.
- Valve Oil keeps the printer from reaching QIDI and the third-party services QIDI's software uses, on every install and whichever program tries: their names resolve nowhere and their addresses are refused, while the printer's network and the rest of the internet stay reachable. QIDI's LAN-only mode is kept on, so QIDI's app shows LAN Only Mode instead of a cloud login and stops its own cloud requests.
- QIDI's AI detection is off on every install. Every install and update turns off QIDI's AI service, `algo_app`, without the question the installer asked, and turns it off again after a QIDI firmware update starts it. The touchscreen app's AI detection settings in QIDI's config table stay off as LAN-only mode stays on. The installer's `--keep-ai-detection` option goes. The app's AI pause is patched out with its other duties and done by nothing.
- QIDI's Moonraker loads upstream Moonraker's MQTT component in place of QIDI's: it connects to the broker in an operator's `[mqtt]` section of `moonraker.conf`, and nowhere without one.
- Patch each duty out of QIDI's app, along with its QIDI cloud work (the device report, the error list push, the online update check, and P2P), its USB mounting, which Valve Oil takes over in place of both the app and QIDI's `makerbase-automount`, and its `ntpdate` call, which never runs because `ntpdate` isn't installed. Patch them in every reviewed build, by guarded instruction patches like the existing ones. The app no longer registers as a Moonraker agent at all, so Valve Oil's agent is the only one QIDI's components can reach.
- QIDI's app keeps drawing the screen and everything an operator starts from it, including the power-loss resume prompt, which now reads the state Valve Oil records.
- A touchscreen app that is not a reviewed build keeps all its duties, and Valve Oil takes none of them; the printer is kept off QIDI's servers either way.
- Uninstall puts QIDI's app back unpatched, removes Valve Oil's services and the block, and brings QIDI's MQTT component back, so the app does its duties again. QIDI's LAN-only mode stays on until the operator turns it off, and its AI detection settings stay off until the operator turns them on. QIDI's AI service comes back with the other OS optimizations when the operator chooses to restore them.

## Capabilities

### New Capabilities

- `qidi-app-duties`: what Valve Oil does in place of QIDI's touchscreen app besides drawing the screen, what the app keeps, the printer kept off QIDI's servers with Moonraker's own MQTT, and QIDI's AI detection kept off.

### Modified Capabilities

- `qidi-box-config`: `MCU_ID.cfg` and `box_count` move from the patched app to Valve Oil.
- `installer-lifecycle`: the OS optimizations no longer ask whether to turn off QIDI's AI detection.

## Impact

- `installer/runtime/qidiclient_firmware_popup.py`: new patch sites and reviewed-build hashes for the Max 4 `01.01.07.01` and `01.01.07.02`, Plus 5 `01.01.02.01` and `01.01.02.02`, and Smart 5 `01.01.02.01` apps.
- `vo-qidi-agent.service`, a new Valve Oil service for the Moonraker agent, print state, resume, `box_count`, the error list, and discovery; `vo-qidi-block.service` for the block of QIDI's servers; `vo_moonraker_fixes.py` for the MQTT component, the LAN-only mode and AI detection settings, and agent routing, with upstream Moonraker's MQTT component installed beside it; the Box monitor for `MCU_ID.cfg` and `printer-*.cfg`; the USB mounter with its udev rules; uninstall.
- `installer/package.yaml` (`algo_app.service` among the services always disabled), `system_optimizations.py`, `install_flow.py`, `usb_install.py`, and `cli.py`: the AI detection question goes.
- Valve Oil UI and the Fluidd fork lose their LAN-only switches; `installer/package.yaml` pins a fork release without its switch.
- `openspec/observations/qidi-platform.md`: the inventory of the app's duties.
- `docs/install.md`, `docs/settings.md`, `docs/fixes.md`, `docs/under-the-hood.md`, `docs/valve-oil-ui.md`, and `CHANGELOG.md`.
