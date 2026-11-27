## 1. Inventory

- [x] 1.1 Record every duty of QIDI's app besides drawing, for the Max 4, Plus 5, and Smart 5 apps, in `openspec/observations/qidi-platform.md`.
- [ ] 1.2 Settle the open questions in `design.md`.

## 2. Patch QIDI's app

- [x] 2.1 Add a patch site per duty and printer to `installer/runtime/qidiclient_firmware_popup.py`, for every reviewed build (Max 4 `01.01.07.01` and `01.01.07.02`, Plus 5 `01.01.02.01` and `01.01.02.02`, Smart 5 `01.01.02.01`), with the new reviewed patched hashes, and keep earlier patched forms upgradable.
- [ ] 2.2 Check on each printer that the patched app draws, shows LAN Only Mode on its account screen, offers a power-loss resume, and does none of the duties.

## 3. Take over the duties

- [x] 3.1 `vo_moonraker_fixes.py`: route `/api/qidiclient/*` to Valve Oil's agent, keep LAN-only mode on, and load upstream Moonraker's MQTT component in place of QIDI's.
- [x] 3.2 `vo-qidi-agent.service`: the agent and its methods, print state, resume after Klipper errors, `box_count`, the AI pause, and the error list.
- [x] 3.3 `vo-qidi-agent.service`: SSDP and `mkswifi`.
- [x] 3.4 The Box monitor: `MCU_ID.cfg`, and `printer-*.cfg` at boot.
- [x] 3.5 Install, update, and uninstall for all of the above; tests for each module's must and must-not.
- [x] 3.6 `vo-qidi-block.service`: QIDI's and third parties' names resolve nowhere and their addresses are refused, on every install.
- [x] 3.7 Valve Oil UI: drop the LAN-only switch.
- [x] 3.8 Fluidd fork: drop the LAN-only switch (fork PR #48, release `v1.37.7-vo-31ebc4d`, `fluidd.zip` sha256 `ba737260128444da2bdf1777ee7d10a5f1d99e95ec277477261cebec2db8dfc8`).
- [x] 3.9 Pin a fork release without the switch in `installer/package.yaml` (dev's `v1.37.7-vo-be0bd96` includes fork PR #48).

## 4. Validation

- [ ] 4.1 On each printer, with QIDI's app running and with it stopped: power cut and resume, resume after a Klipper error, Box connect and remove, USB drive in QIDI's app and Fluidd, mainboard name change, AI pause, QIDI Studio discovery, nothing reaching QIDI's servers, and Moonraker's MQTT with and without an `[mqtt]` section. (Smart 5, 2026-11-22: block, LAN-only pin, and MQTT both ways checked.)
- [ ] 4.2 Compare Valve Oil's print-state writes with the stock app's during the same print.

## 5. Release

- [x] 5.1 Docs: `docs/install.md`, `docs/settings.md`, `docs/fixes.md`, `docs/under-the-hood.md`, `docs/valve-oil-ui.md`.
- [x] 5.2 The `CHANGELOG.md` entries under the unreleased package version.
- [x] 5.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
