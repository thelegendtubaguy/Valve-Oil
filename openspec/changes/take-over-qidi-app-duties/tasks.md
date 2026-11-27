## 1. Inventory

- [x] 1.1 Record every duty of QIDI's app besides drawing, for the Max 4, Plus 5, and Smart 5 apps, in `openspec/observations/qidi-platform.md`.
- [ ] 1.2 Settle the open questions in `design.md`.

## 2. Patch QIDI's app

- [x] 2.1 Add a patch site per duty and printer to `installer/runtime/qidiclient_firmware_popup.py`, for every reviewed build (Max 4 `01.01.07.01` and `01.01.07.02`, Plus 5 `01.01.02.01` and `01.01.02.02`, Smart 5 `01.01.02.01`), with the new reviewed patched hashes, and keep earlier patched forms upgradable.
- [ ] 2.2 Check on each printer that the patched app draws, shows LAN Only Mode on its account screen, offers a power-loss resume, and does none of the duties.

## 3. Take over the duties

- [x] 3.1 `vo_moonraker_fixes.py`: route `/api/qidiclient/*` to Valve Oil's agent, keep LAN-only mode on, and load upstream Moonraker's MQTT component in place of QIDI's.
- [x] 3.2 `vo-qidi-agent.service`: the agent and its methods, print state, resume after Klipper errors, `box_count`, and the error list.
- [x] 3.3 `vo-qidi-agent.service`: SSDP and `mkswifi`.
- [x] 3.4 The Box monitor: `MCU_ID.cfg`, and `printer-*.cfg` at boot.
- [x] 3.5 Install, update, and uninstall for all of the above; tests for each module's must and must-not.
- [x] 3.6 `vo-qidi-block.service`: QIDI's and third parties' names resolve nowhere and their addresses are refused, on every install.
- [x] 3.7 Valve Oil UI: drop the LAN-only switch.
- [x] 3.8 Fluidd fork: drop the LAN-only switch (fork PR #48, release `v1.37.7-vo-31ebc4d`, `fluidd.zip` sha256 `ba737260128444da2bdf1777ee7d10a5f1d99e95ec277477261cebec2db8dfc8`).
- [x] 3.9 Pin a fork release without the switch in `installer/package.yaml` (dev's `v1.37.7-vo-be0bd96` includes fork PR #48).
- [x] 3.10 QIDI's AI detection off on every install: `algo_app.service` disabled by every install and update without the installer's question (its screen and `--keep-ai-detection` removed), and reconciled after a QIDI firmware update.
- [x] 3.11 `vo_moonraker_fixes.py`: keep the touchscreen's `ai.*` detection settings at `"0"` from the same table of pinned keys as `general.lan_mode`.
- [x] 3.12 Remove the agent's AI pause and the error list's AI entries, with their test, now that nothing reports to them.
- [x] 3.13 Fluidd fork: no AI detection controls to remove (branch `qidi` at `be0bd96`; `serverDatabaseConfigSelect`/`Update` in `src/api/socketActions.ts` have no callers since fork PR #48).

## 4. Validation

- [ ] 4.1 On each printer, with QIDI's app running and with it stopped: power cut and resume, resume after a Klipper error, Box connect and remove, USB drive in QIDI's app and Fluidd, mainboard name change, QIDI Studio discovery, nothing reaching QIDI's servers, and Moonraker's MQTT with and without an `[mqtt]` section. (Smart 5, 2026-11-22: block, LAN-only pin, and MQTT both ways checked. Plus 5 `01.01.02.02`, 2026-11-27: install over 26.11.25.1, home screen, one agent, discovery started, block, LAN-only mode; power cut at layer 32 with QIDI's app running: Valve Oil's record survived, the app offered the resume, and the print resumed 4 KiB before the recorded position; a USB drive mounted once at `gcodes/USB` (the repeated add event left alone), was listed by Moonraker and opened in QIDI's app, and was unmounted on removal; a stale mainboard name in `MCU_ID.cfg` was rewritten 2 s after the mainboard's add event, byte-identical to the original, with a `printer-*.cfg` backup removed and Klipper left running; unplugging the USB Box had Valve Oil rewrite `box.cfg` for no Box and restart Klipper 6 s later and save `box_count` 0, and plugging it back in put back the identical `box.cfg`, restarted Klipper once, and saved `box_count` 1, with QIDI's app doing neither; QIDI Studio connected to the printer added by IP, and a unicast `mkswifi` request got the app's reply (discovery across the test network's VLANs couldn't be tried).)
- [ ] 4.2 Compare Valve Oil's print-state writes with the stock app's during the same print.
- [ ] 4.3 QIDI's AI detection off: on the Max 4, `algo_app` stopped and disabled after an install from an install that kept it on, after a QIDI firmware update and the install it runs, and back as found after an uninstall that restores the OS optimizations; on each printer, each AI detection switch in the touchscreen's settings turned on reads off again, is logged in `moonraker.log`, and `ai.*` reads `"0"` in QIDI's config table; whether turning the spaghetti switch on still saves Klipper's `enable_noodle_detection`.

## 5. Release

- [x] 5.1 Docs: `docs/install.md`, `docs/settings.md`, `docs/fixes.md`, `docs/under-the-hood.md`, `docs/valve-oil-ui.md`.
- [x] 5.2 The `CHANGELOG.md` entries under the unreleased package version.
- [x] 5.3 Run `openspec validate --all --strict`, the installer core tests, and the bundle smoke test.
