# Valve Oil UI

The project's own web interface for the printer, served at `http://<printer>/valve-oil/` beside Fluidd. Operator docs are in [`docs/valve-oil-ui.md`](../docs/valve-oil-ui.md).

Vite, React 19, TypeScript, and Tailwind CSS 4. It talks to Moonraker over its JSON-RPC websocket on the same origin, so it needs no configuration on the printer.

## Develop

```sh
cd ui
npm ci
npm run dev:fake                       # fake Moonraker on :7125, serving dist/ at /valve-oil/
npm run dev                            # http://localhost:5173/?moonraker=localhost:7125
```

`?moonraker=<host:port>` points the dev server at any Moonraker, the fake or a printer, and is remembered in development builds. Production builds always use the page's own origin.

The fake Moonraker (`dev/fake-moonraker.mjs`) models a Max 4 on firmware 01.01.07.01 with two Boxes. It simulates printing, temperatures, Box loads and unloads, and the commands the UI sends. Pick a starting state with `--scenario`:

| Scenario | State |
|---|---|
| `printing` | A two-color print at 62% |
| `idle` | Ready, with print history and a printer warning |
| `box-busy` | A Box load running |
| `box-failed` | A Box unload paused on `QDE_004_008` |
| `stranded` | Filament stuck between the hub and the toolhead |
| `no-box` | No QIDI Box |

`VO_BOX_RFID_WRITE` and `VO_BOX_RFID_TEST` write and scan made-up spool tags: 1A holds a QIDI tag, 1D (an empty slot, so the Box turns its spool) and 2C blank tags, 2A Elegoo data, and 2D a locked tag; 1B and 1C have none.

It also stands in for Valve Oil's status service at `/tltg-status/api`, which feeds the Updates screen. Pick its state with `--updates` (with only `--updates` given, the printer starts `idle`):

| Updates scenario | State |
|---|---|
| `none` | Nothing waiting, automatic updates on, every board up to date |
| `release-off` | A Valve Oil release waiting, automatic updates off |
| `firmware-printing` | QIDI firmware, THR firmware, and a release waiting, held back by a print (switches the printer to `printing`) |
| `boards` | A Box with a bootloader Valve Oil doesn't support (with **Report this board**), a Box kept on QIDI's firmware, and a mainboard not read |
| `running` | Update now installing QIDI firmware, the THR, and a release; Moonraker and the status service go away for 15 seconds, then Klipper restarts |
| `failed` | The last update failed with its log, and the last check failed; the next Update now fails once more |
| `interrupted` | An automatic update stopped part way, with firmware still waiting |
| `refused` | A release waiting; the first Update now is refused (409) because an update check holds the installer lock with a heater on, and stays unavailable for 20 seconds |
| `firmware-failed` | A QIDI firmware update kept after it failed while installing QIDI's package; **Continue the update** carries it on |
| `firmware-rescue` | A QIDI firmware update kept after a mainboard write couldn't be verified; Update now waits for `./mainboard-firmware.sh rescue` |
| `closed-loop` | After a firmware update that left the Max 4's closed-loop drivers on their firmware |
| `thr-waiting` | QIDI firmware waiting, with the THR and mainboard updated after it in the same update |
| `reinstall` | Valve Oil's installed files are missing or changed, so the installed release waits to be installed again |
| `dev` | A dev prerelease whose update check failed, which the screen leaves out |
| `absent` | No status service (404), as on a printer without it |

Every scenario sends `update_now`, `check.next_at`, `job.run_id`, `run.firmware_update` and `run.can_continue`, `pending[].reinstall`, and `boards[].waiting_for_firmware_update`, `release.channel` and `release.commit`, and `support.issue_url` as the service does, answers `GET /tltg-status/api/support-bundle` with an empty zip, and the printer warnings Klipper would report for it (with **Details** and **Report this board** links).

Switch at runtime from the console with `VO_UPDATES NAME=<scenario>`. **Update now** runs the same simulated update from any of them. `npm run dev` proxies `/tltg-status`, `/tltg-addons`, and `/tltg-screen` to `VO_PRINTER` (default `http://localhost:7125`), so point both at the same place, for example `VO_PRINTER=http://192.168.1.166 npm run dev` with `?moonraker=192.168.1.166`.

The fake is split by feature: `dev/fake-moonraker.mjs` is the core (printer state, the Box, Valve Oil's status service, scenarios) and each `dev/fake/*.mjs` adds one screen's methods, G-code, routes, and objects (see `dev/fake/README.md`). Their options:

| Option or command | What it does |
|---|---|
| `--scenario bed-soak` | Starts with a bed soak test running, 20 times faster than on a printer |
| `--gcode-kbps <n>` | Slows G-code downloads so the preview's progress shows |
| `--addons <state>`, `VO_ADDONS NAME=<state>` | The add-ons service: `none`, `installed`, `linking`, `busy`, `foreign`, `failed`, `absent` |
| `--lan-mode 0\|1\|none` | QIDI's LAN only mode off, on, or missing |
| `--spoolman` | Loads Moonraker's `spoolman` component with made-up spools |
| `VO_KLIPPY_SHUTDOWN [SECONDS=30]` | Shuts Klipper down for a while, for the stop screen |

The G-code files it serves are generated: OrcaSlicer-style `.gcode` and QIDI Studio-style `.gcode.3mf`, with objects, feature types, retractions, fan changes, and a tool change, and the printing scenario moves through the file. It also has a `config` folder, logs, history, a job queue, timelapse videos, cameras, services, the touchscreen stream at `/tltg-screen/ws`, bed meshes, and the add-ons service.

`npm run build` writes `dist/`, which the fake serves at `http://localhost:7125/valve-oil/`, the same layout as the printer.

To try a build on a printer that already serves `/valve-oil/`, run `dev/sideload.sh qidi@<printer-ip>`. It builds the UI and swaps it into `~/tltg-valve-oil-ui` without restarting anything or changing nginx. Open tabs pick it up on refresh. The next real install replaces it.

## Check

```sh
npm run typecheck
npm test          # the logic ported from the Fluidd fork, with its tests, and the UI's own parsing and calculations
npm run build
```

## Layout

- `src/moonraker/client.ts`: websocket JSON-RPC client with reconnect.
- `src/App.tsx`: the shell: the left rail (a bottom bar with **More** on phones), the top bar (state, temperatures, Save config, notifications, theme, emergency stop), warnings, and Klipper's stop screen. Screens other than Home, the Box, and Updates are loaded when first opened.
- `src/lib/router.ts`: hash routes, `#/<screen>/<sub>/<path>`; tabs are sub-paths, such as `#/control/macros` and `#/files/config/printer.cfg`.
- `src/styles.css`: Valve Oil's brand tokens (from the USB installer and touchscreen screens) and the shared classes: `card`, `btn`, `choice`, `seg`, `pm`, `pill`, `tile`, `link-row`, `table`, `menu`. Dark is the default.
- `src/components/ui.tsx`: shared controls: dialogs, `confirm()` and `prompt()`, segmented tabs, pill choices, − value + steppers, menus, hold-to-confirm buttons.
- `src/state/`: printer status (subscribed objects, batched updates, temperature history, console, all of `configfile.settings`), jobs, files and history, QIDI Box, warnings, add-ons, and `database.ts` for Moonraker's database. Anything Fluidd also stores goes in its `fluidd` namespace under Fluidd's own key and shape, so the two UIs agree; what only Valve Oil UI keeps goes in `valve_oil_ui`.
- `src/qidi/`: QIDI Box, printer-warning, Valve Oil status and add-ons, LAN only mode, bed soak, probe report, and Klipper health logic ported from the Fluidd fork (`src/util/` there), with its tests. Keep it in step with the fork.
- `src/workers/`: the G-code preview's parser, ported from the fork with its tests, run in a Web Worker.
- `src/state/valve-oil.ts`: polls Valve Oil's status service for the Updates screen, and while an update runs, so the app keeps saying so while Moonraker is down for it.
- `src/screens/`: Home, Files (with the G-code preview and the config editor), Box, Control, Console, Tune, History, System, Settings, Updates. The Updates screen mirrors the Fluidd fork's Valve Oil settings section and uses its wording.
- `src/components/<screen>/`: each screen's pieces; shared ones such as the filament path, spools, temperature tiles, and the print dialog are directly in `src/components/`. The config editor is CodeMirror 6, loaded only when it opens.

## Release

`scripts/build_installer_bundle.py` runs `npm ci` and `npm run build` with `VO_UI_VERSION` set, zips `dist/` reproducibly, and pins its sha256 in the bundled `installer/package.yaml`. The build writes `version.json`, which open tabs poll so they reload after an update, `.gz` copies of scripts and styles for nginx's `gzip_static`, and `licenses.md` for the bundled npm packages.
