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

`npm run build` writes `dist/`, which the fake serves at `http://localhost:7125/valve-oil/`, the same layout as the printer.

To try a build on a printer that already serves `/valve-oil/`, run `dev/sideload.sh qidi@<printer-ip>`. It builds the UI and swaps it into `~/tltg-valve-oil-ui` without restarting anything or changing nginx. Open tabs pick it up on refresh. The next real install replaces it.

## Check

```sh
npm run typecheck
npm test          # the Box logic ported from the Fluidd fork, with its tests
npm run build
```

## Layout

- `src/moonraker/client.ts`: websocket JSON-RPC client with reconnect.
- `src/state/`: printer status (subscribed objects, batched updates, temperature history, console), jobs, files and history, QIDI Box, warnings.
- `src/qidi/`: QIDI Box and printer-warning logic ported from the Fluidd fork (`src/util/` there). Keep it in step with the fork.
- `src/screens/`: Home, Files, QIDI Box.
- `src/components/`: shared pieces such as the filament path, spools, temperature tiles, and the print dialog.

## Release

`scripts/build_installer_bundle.py` runs `npm ci` and `npm run build` with `VO_UI_VERSION` set, zips `dist/` reproducibly, and pins its sha256 in the bundled `installer/package.yaml`. The build writes `version.json`, which open tabs poll so they reload after an update, `.gz` copies of scripts and styles for nginx's `gzip_static`, and `licenses.md` for the bundled npm packages.
