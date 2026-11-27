## Context

QIDI's touchscreen app, `qidiclient`, runs as root with `Restart=always`, draws with LVGL, and connects to Moonraker at `127.0.0.1:7125` as an extension agent named for the model (`QD_MAX4`, `QD_PLUS5`, `S5`). Static analysis of the Max 4 `01.01.07.01` and Plus 5 `01.01.02.01` apps (one source tree) and the Smart 5 `01.01.02.01` app (its own tree) finds these duties besides drawing, recorded in `openspec/observations/qidi-platform.md`:

- **Print state.** While printing, it writes `print_state.*` (file and G-code position, layer, plate, excluded objects, targets, fans, speed and extrusion factors) to Moonraker's database 10–40 times a minute. QIDI's Moonraker turns those keys into the power-loss resume (`printer.print.power_off_print`). Nothing else writes them. After three Klipper errors during a print it also restarts Klipper's firmware and resumes the print by itself.
- **Startup.** It rewrites `MCU_ID.cfg` from `/dev/serial/by-id`, deletes `printer-*.cfg` in the config folder, and turns the heaters off and restarts Klipper when its device check asks for it. Valve Oil's existing patch already stops its `box.cfg` writes.
- **Klipper.** It saves `box_count` after each Moonraker subscription and Klippy state change, and sends `PAUSE` when the `qdc_ai_error_code` saved variable reports spaghetti.
- **Moonraker agent.** QIDI's `mqtt` component asks the first registered agent to decrypt the saved cloud account (`qidiclient.crypto.aes_decrypt_hex`, AES-128-ECB with a key the component passes). QIDI's `extensions` component serves `/api/qidiclient/files/list`, `files/stats`, and `config/offical_filament_list` from the agent, and the agent answers `get_all_error_list` and `notify_export_log` and pushes its error list to QIDI's cloud as `ui_assistant_message_update`.
- **QIDI cloud.** It reports the device to `/community/v1/device/status/report` at startup and on settings changes, fetches the account profile into `user.*` and unlinks when the cloud answers `40004`, checks `/backend/v1/fireware/upgrade-info` hourly, and runs P2P remote access from an SDK compiled into the app. Its FRP writer is never called; QIDI's Moonraker runs FRP.
- **LAN.** It sends SSDP NOTIFY announcements when the network comes up and answers `mkswifi` discovery requests.
- **Clock.** It runs `ntpdate` when Wi-Fi connects. `ntpdate` is not installed on any of the three printers.
- **USB drives.** It mounts and unmounts partitions at `printer_data/gcodes/USB` on udev events and at startup, racing QIDI's system `makerbase-automount`, which does the same.
- **Operator-started.** Wi-Fi setup, the cloud login QR code and logout (shown as LAN Only Mode while LAN-only mode is on), the power-loss resume prompt, self-check, log export to a drive, offline update, factory reset, and screen sleep, which writes `general.screen_state`.

Valve Oil already patches the reviewed app builds with guarded instruction changes (`installer/runtime/qidiclient_firmware_popup.py`), already writes `box.cfg` itself (`qidi-box-config`), already keeps the app's P2P code out of Peergine's relay mesh (`installer-lifecycle`), and already loads code into QIDI's Moonraker (`vo_moonraker_fixes.py`), which today holds the first cloud connection until the app's agent connects.

QIDI's Moonraker loads its compiled `mqtt` component whether or not `moonraker.conf` has an `[mqtt]` section (`server.py` adds it to the sections it loads), and that component connects only to QIDI's cloud broker with the account QIDI's cloud gave the printer and ignores the section's options, so an operator can't use Moonraker's MQTT with their own broker. QIDI's `frp_manager` acts only when that component calls it.

## Goals / Non-Goals

**Goals:** each duty above on the printer and its network that isn't operator-started is Valve Oil's on every supported printer whichever app is on the screen; QIDI's app no longer does any of them; behavior seen by QIDI's Moonraker components and QIDI Studio is the same as with the stock app; stopping QIDI's app loses nothing but its screen. Nothing on the printer reaches QIDI's servers, and Moonraker's MQTT works with the operator's own broker.

**Non-Goals:** the Valve Oil touchscreen (change `add-valve-oil-touchscreen`), which shows no QIDI cloud login; keeping QIDI's cloud, QIDI's phone app, account linking, FRP, or P2P working, which the owner chose to give up; changing what QIDI's app shows or does when an operator uses it, beyond its account screen showing LAN Only Mode; QIDI firmware updates, which Valve Oil already delivers and already blocks in the app; QIDI's AI service itself.

## Decisions

### Patch every duty out of QIDI's app

Each duty gets a size-preserving patch site per reviewed build, in the existing patch module and with its guards: a build is patched only when its SHA-256 is a reviewed form, every site's bytes are checked before writing, and the patched hashes are recorded. Sites prefer returning at the entry of a function whose only job is the duty, with every caller checked. The app keeps its screen, its own file list, and every operator-started action.

The app's QIDI cloud work, the device report, the error list push, the online update check, and P2P, is patched out the same way and done by nothing. The patch keeps the earlier P2P sites (`installer-lifecycle`) as a second guard.

The app stops registering as a Moonraker agent: it identifies as a plain client, or skips identifying if its own Moonraker use doesn't need it. That removes the race for QIDI's components' agent calls for good, as the owner asked.

A touchscreen app that is not a reviewed build is left as it is, keeps every duty, and Valve Oil's services take none, the rule `qidi-box-config` already follows. The Valve Oil touchscreen does not stop such an app.

### Valve Oil's agent takes the app's place

QIDI's `extensions` component serves `/api/qidiclient/*` from the only agent connected, failing when a slicer has registered as an agent too. `vo-qidi-agent.service` connects to Moonraker's Unix socket and identifies as an agent under the app's own name (`QD_MAX4`, `QD_PLUS5`, `S5`), QIDI's firmware version, and URL `local`, as the app did. It answers the app's methods that don't reach QIDI's cloud, in the app's result shapes: the file list and statistics, the official filament list, and the error list. The decryption QIDI's `mqtt` component asked for and the log export QIDI's cloud asked for go with the cloud.

`vo_moonraker_fixes.py` sends unqualified `/api/qidiclient/*` calls to Valve Oil's agent when others are connected. Its fixes for QIDI's `mqtt` component (the TLS handshake off the event loop, the first connection held for the touchscreen app, stopping retries in LAN-only mode) go with that component.

### The agent service does the app's Moonraker-side work

Through the same connection and one status subscription, the agent service:

- records `print_state.*` exactly as the app does: the same 19 keys and string forms, written when the file position has moved more than 1024 bytes, only the fields that changed, with the app's per-print reset, so QIDI's `power_off_print` and the resume prompts behave as with the stock app;
- after `Lost communication with MCU`, `Heater extruder not heating at expected rate`, or `Move queue overflow` during a print past layer 1, restarts Klipper's firmware, waits for it as the app does, and resumes with `power_off_print`;
- saves `box_count` after it connects, when Klipper becomes ready, and when a Box connects or disconnects, from the USB Boxes connected, as the app does;
- sends `PAUSE W=1` when `qdc_ai_error_code` reports `noodle` with `status=1` during a print, once per print;
- keeps the app's error list from Klipper's state and warnings with QIDI's codes and texts (`QIDI_Client/resource/qidi_client_error`), answers `get_all_error_list`, and sends each addition and removal as `ui_assistant_message_update` to Moonraker's clients. The app's cloud MQTT and P2P entries are left out: with LAN-only mode on, the app reported neither.

### The agent service does the app's LAN work

It sends the SSDP announcements every 5 seconds and answers `mkswifi` discovery on UDP 8989 once Wi-Fi is up, as the app does. The announcement says the printer isn't linked to a QIDI account or connected to QIDI's cloud, as the app said for a printer never linked, with LAN-only mode on. It is stdlib-only Python, runs at low priority off Klipper's CPUs within a memory ceiling, and restarts on failure.

### The clock

`ntpdate` is not installed on any of the three printers, so the app's call never ran, and `systemd-timesyncd` keeps the clock. Valve Oil patches the call out and adds nothing.

### Startup duties with the Box monitor

The Box monitor already runs at boot and on Box hotplug. It also writes `MCU_ID.cfg` exactly as the app does (`[mcu]`, the first `/dev/serial/by-id` name for the mainboard, `restart_method: command`) at boot and when the mainboard's serial device appears, only when the file would change, and at boot deletes `printer-*.cfg` directly in the config folder when a mainboard was found. Like the app, it restarts nothing for `MCU_ID.cfg`.

### USB drives

The patched app no longer mounts or unmounts: it takes a drive already mounted at `printer_data/gcodes/USB` as ready when udev reports the partition added, rescans it, and looks for its offline update there. Valve Oil's udev rule starts `vo-usb-mount@add-<partition>.service`, which mounts a USB partition there with the app's options for its filesystem, unless a drive is already mounted or the device is in `/etc/fstab`, and then repeats udev's add event so the app sees the drive even when it handled the first event before the mount. The repeated event finds the drive mounted and does nothing. `vo-usb-mount@remove-<device>.service` unmounts it when it is removed. Mounting through a service keeps the mount out of udev's private namespace.

QIDI's `makerbase-automount` mounts on every add, even over a drive already mounted, so the repeated event would stack mounts. Valve Oil's `60-usbmount.rules` in `/etc/udev/rules.d` replaces QIDI's rule of that name while Valve Oil is installed, and uninstall removes it.

### QIDI's servers are blocked

QIDI's kernels have no packet filter: no netfilter, BPF, seccomp filters, network namespaces, or policy routing, and netfilter's hooks can't be added as modules. So the block is by destination, which also covers every program on the printer, whichever app is on the screen. `vo-qidi-block.service`, a root service separate from the agent, runs on every install, whatever the touchscreen app:

- the names QIDI's app builds, QIDI's Moonraker, and Moonraker's own components reach, QIDI's MQTT and P2P hosts, and the hosts QIDI's cloud gave the printer for MQTT, P2P, and FRP (read from QIDI's config table every 5 seconds when the database has changed), resolve to `0.0.0.0` and `::` from a marked block of `/etc/hosts`;
- the addresses among them, QIDI's fallback MQTT broker, and the app's time server get `prohibit` routes, marked with their own route protocol, so a connection fails at once with "Permission denied", also for a program that has the address cached or built in.

It also keeps QIDI's `frpc.service` stopped. Only public addresses are blocked, never the printer's network or a DNS server: QIDI's DHCP hook writes `1.1.1.1` and `8.8.8.8` to `/etc/resolv.conf` after the network's own servers, and they are the printer's only DNS servers on a network whose DHCP names none (the Plus 5 test network). The app pings them, and `223.5.5.5` and `119.29.29.29`, only to check its connection. So the printer resolves names and reaches other internet hosts, such as Valve Oil's own updates and the add-ons the operator installs (ZeroTier, OctoEverywhere). The service starts before Moonraker and QIDI's app at boot, before their first connection, blocks from its first check even when the database can't be read, and removes the block and its routes when it stops, so uninstall, which stops it, leaves nothing blocked. A name QIDI's cloud invents later isn't in the list until it reaches the config table or a reviewed build.

### LAN-only mode stays on

QIDI's LAN-only mode (`general.lan_mode` `"1"`) makes QIDI's app filter its own HTTP requests before they connect and show LAN Only Mode on its account screen instead of a cloud login that can't work. `vo_moonraker_fixes.py` keeps it on inside QIDI's database component: it writes `"1"` when Moonraker starts, a write of `general.lan_mode` stores and answers `"1"` without running QIDI's handler (which would close the operator's MQTT connection or start QIDI's), and a batch write or a delete that changed it is followed by `"1"` again. The check after other writes reads only the component's in-memory copy, as the print state writes many times a minute. QIDI's app reads the value only through Moonraker. Uninstall leaves it on; the operator turns it off at the bottom of the touchscreen's Wi-Fi page.

### Moonraker's own MQTT

`vo_moonraker_fixes.py` loads upstream Moonraker `v0.9.3`'s `mqtt.py`, the version QIDI's Moonraker is built on, as `moonraker.components.mqtt` in place of QIDI's compiled component. It is installed beside the fixes module as `vo_moonraker_mqtt.py`, unchanged apart from its ending: with an `[mqtt]` section, upstream's client connects to that broker as Moonraker documents; without one, a stand-in loads that connects nowhere, reports itself disconnected, and refuses publishes and subscriptions, so the component QIDI's `server.py` always loads never shows as failed. Upstream components that use MQTT (history, power, sensors) see upstream's API. When the file is missing, QIDI's component loads as before.

### Uninstall

Uninstall restores QIDI's app bytes, removes Valve Oil's services, the block, the udev rule, and the Moonraker changes, which brings QIDI's MQTT component back, and leaves `print_state.*`, `MCU_ID.cfg`, the mount, and LAN-only mode as they are; QIDI's app takes its duties back at its next start.

## Risks / Trade-offs

- [A duty is reproduced with a small difference, such as a print-state value form, and a power-loss resume fails only in the field] → derive each format from QIDI's consumer code, compare Valve Oil's database writes with the app's on hardware before release, and test a power cut during a print on each printer.
- [A patch site disables more than its duty] → every caller is checked, the screen's own features are exercised on each printer, and the patch module's existing guards refuse unknown builds.
- [More work on the host during prints] → print-state writes move from the app to the agent service at the same rate, and the service is limited and off Klipper's CPUs.
- [The app's screen relies on state its patched-out duties kept, such as its USB status or error list] → each screen feature is exercised on each printer with the patched app.
- [A host QIDI's software reaches isn't in the list, because a later build or QIDI's cloud names a new one] → each new reviewed build's strings and each printer's logs are checked for hosts, and the hosts the cloud writes into the config table are blocked as they appear.
- [A QIDI firmware update replaces QIDI's Moonraker with a build whose `mqtt` component or database code differs] → the database fix checks the methods it wraps and logs when it leaves them alone; the MQTT component is upstream's and depends only on upstream Moonraker's API.
- [Operators relied on QIDI's phone app or cloud] → the owner's decision; the changelog and docs say what stops working.

## Open Questions

- Who calls `/api/qidiclient/*` and `get_all_error_list` on the LAN in practice; both are answered either way.
- When the app zeroes `box_count` for a Box that needs firmware.
- Whether `algo_app` pauses a print by itself on spaghetti.
