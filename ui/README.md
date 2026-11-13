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
| `absent` | No status service (404), as on a printer without it |

Every scenario sends `update_now`, `check.next_at`, `job.run_id`, `run.firmware_update` and `run.can_continue`, `pending[].reinstall`, and `boards[].waiting_for_firmware_update` as the service does, and the printer warnings Klipper would report for it (with **Details** and **Report this board** links).

Switch at runtime from the console with `VO_UPDATES NAME=<scenario>`. **Update now** runs the same simulated update from any of them. `npm run dev` proxies `/tltg-status` to `VO_PRINTER` (default `http://localhost:7125`), so point both at the same place, for example `VO_PRINTER=http://192.168.1.166 npm run dev` with `?moonraker=192.168.1.166`.

`npm run build` writes `dist/`, which the fake serves at `http://localhost:7125/valve-oil/`, the same layout as the printer.

To try a build on a printer that already serves `/valve-oil/`, run `dev/sideload.sh qidi@<printer-ip>`. It builds the UI and swaps it into `~/tltg-valve-oil-ui` without restarting anything or changing nginx. Open tabs pick it up on refresh. The next real install replaces it.

## Check

```sh
npm run typecheck
npm test          # the Box, warning, and Valve Oil status logic ported from the Fluidd fork, with its tests
npm run build
```

## Layout

- `src/moonraker/client.ts`: websocket JSON-RPC client with reconnect.
- `src/state/`: printer status (subscribed objects, batched updates, temperature history, console), jobs, files and history, QIDI Box, warnings.
- `src/qidi/`: QIDI Box, printer-warning, and Valve Oil status logic ported from the Fluidd fork (`src/util/` there). Keep it in step with the fork.
- `src/state/valve-oil.ts`: polls Valve Oil's status service for the Updates screen, and while an update runs, so the app keeps saying so while Moonraker is down for it.
- `src/screens/`: Home, Files, QIDI Box, Updates. The Updates screen mirrors the Fluidd fork's Valve Oil settings section and uses its wording.
- `src/components/`: shared pieces such as the filament path, spools, temperature tiles, and the print dialog.

## Release

`scripts/build_installer_bundle.py` runs `npm ci` and `npm run build` with `VO_UI_VERSION` set, zips `dist/` reproducibly, and pins its sha256 in the bundled `installer/package.yaml`. The build writes `version.json`, which open tabs poll so they reload after an update, `.gz` copies of scripts and styles for nginx's `gzip_static`, and `licenses.md` for the bundled npm packages.
