<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/images/valve-oil-logo-dark.png">
    <img alt="Valve Oil" src=".github/images/valve-oil-logo.png" width="520">
  </picture>
</h1>

Faster, fixed, and more capable QIDI printers: optimized Klipper macros, slicer start G-code, and a current Fluidd, installed and removed with one command.

<p>
  <a href="https://youtube.com/@TubaMakes"><img alt="Subscribe to Tuba Makes on YouTube" src="https://img.shields.io/badge/YouTube-Tuba%20Makes-FF0000?style=for-the-badge&logo=youtube&logoColor=white"></a>
  <a href="https://tubag.uy/patreon"><img alt="Support Tuba Makes on Patreon" src="https://img.shields.io/badge/Patreon-Support%20me-F96854?style=for-the-badge&logo=patreon&logoColor=white"></a>
  <a href="https://l.tubaguy.com/tmdiscord"><img alt="Join the Tuba Makes Discord" src="https://img.shields.io/badge/Discord-Tuba%20Makes-5865F2?style=for-the-badge&logo=discord&logoColor=white"></a>
</p>

If you want to help support content like this, consider subscribing on YouTube or joining on Patreon!

## Supported printers

| Printer | QIDI firmware |
|---|---|
| QIDI Max 4 | `01.01.06.03`, `01.01.06.04`, `01.01.06.05`, `01.01.07.01` |
| QIDI Plus 5 | `01.01.02.01` |


## Install

1. SSH into your printer `qidi@<your printer's ip>`.
2. Run this on your printer:
   ```bash
   /bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/valve-oil/releases/latest/download/install-latest.sh)"
   ```
3. Follow the prompts.
4. In OrcaSlicer 2.4.0 or later, subscribe to the [OrcaCloud bundle](https://cloud.orcaslicer.com/b/49f26b9c2101). For QIDI Studio or older Orca, [copy the start G-code by hand](docs/install.md#slicer-start-g-code).
5. Slice using your printer's Valve Oil profile, such as `Qidi X-Max 4 0.4 nozzle - Valve Oil`. Optionally, make a copy and customize it.

Before changing anything, the installer reads the printer's boards, which stops Klipper and the touchscreen for a few minutes, and refuses a new install on a board it doesn't support yet. At the end, without asking, it flashes [Valve Oil's board firmware](docs/firmware.md) to the toolhead board, the Max 4's mainboard, and each USB QIDI Box. **After the toolhead board is flashed, run input shaper calibration before bed leveling or printing.** On an older QIDI firmware, it offers to [update the QIDI firmware](docs/install.md#qidi-firmware-updates) first. While Valve Oil is installed, the touchscreen no longer installs QIDI's firmware updates; Valve Oil delivers them.

[Dry run, QIDI firmware updates, automatic updates, and uninstalling →](docs/install.md)

## What you get

### Updates
- Valve Oil delivers QIDI's firmware updates, once a release supports them, with its own board firmware in the same run.
- An hourly check shows what's waiting in Fluidd's Settings and Valve Oil UI, and with automatic updates on, installs it while the printer is idle.

[QIDI firmware updates and automatic updates →](docs/install.md#updating)

### Faster prints
- Shorter print starts and filament changes: quicker homing and probing, and less fixed waiting around cuts, wipes, and saves.
- Box filament stays loaded between prints, so the next print skips the unload and reload.
- Options to reuse a saved bed mesh, warm up the bed, chamber, and nozzle in turn, and start once the chamber reaches a minimum temperature.

[Print start and end →](docs/print-start.md)

### QIDI Box
- Faster, tunable loading and unloading.
- Cut, unload, and eject filament without heating the nozzle.
- Box heater control from Fluidd, and Box processor temperatures.
- Edit QIDI's filament list from Fluidd, including brands such as Bambu, Overture, and Polymaker.
- Read Anycubic, Elegoo, and Bambu Lab spool tags, with every one of their filaments in QIDI's list.

[QIDI Box →](docs/qidi-box.md)

### Spoolman
- Each QIDI Box slot can have its own Spoolman spool, and filament use is charged to the loaded slot's spool.

[Spoolman →](docs/spoolman.md)

### Current Fluidd
- [This project's build of current Fluidd](https://github.com/thelegendtubaguy/fluidd), replacing the old Fluidd QIDI ships (1.30.5 on the Max 4), which QIDI no longer updates. Your settings carry over.
- A redesigned QIDI Box card, a live view of the touchscreen, and a restart button for a frozen touchscreen.

[Installing and undoing it →](docs/fluidd.md)

### Add-ons
- Install optional programs from Fluidd's Settings with one click, such as OctoEverywhere or Obico for remote access, or Tailscale to reach the printer over your own private network, without the system changes their own installers make.

[Add-ons →](docs/addons.md)

### Valve Oil UI
- An early second web interface at `http://<your printer's ip>/valve-oil/`, alongside Fluidd, with the dashboard, files, QIDI Box, and updates screens.

[Valve Oil UI →](docs/valve-oil-ui.md)

### Fixes for QIDI bugs
- The real error instead of a bare `MCU shutdown`.
- Fewer Boxes dropping with `Lost communication with MCU 'mcu_boxN'`.
- Multi-plate 3MF files print the right plate from Fluidd, reprints, and the job queue.
- On the Max 4, a top speed that keeps the X/Y motors from skipping on fast diagonal moves.
- A probe point whose samples never agree fails the mesh instead of hanging print start.

[All fixes →](docs/fixes.md)

### Tools
- A guided nozzle load-cell check, probe accuracy, and bed screw helpers.
- A switch to stop the external spool's runout sensor from pausing prints.
- On the Max 4, an optional monitor that pauses the print when an X or Y motor slips, or corrects the position and keeps printing.
- Input shaper calibration with newer Klipper's sweeping resonance test, with a switch back to QIDI's.
- A bed soak test that shows how long the bed and chamber take to reach temperature and the bed to stop moving.

[Tools →](docs/tools.md)

## More

- [Every setting and command in one table](docs/settings.md)
- [If something goes wrong](docs/troubleshooting.md): restoring backups and reporting problems
- [Board firmware](docs/firmware.md): fixes for the toolhead board, the QIDI Box, and the Max 4 mainboard, which the installer flashes
- [What the installer changes on your printer](docs/under-the-hood.md)
- [Changelog](CHANGELOG.md)
- [Help test releases](TESTING.md) and [run the dev branch](DEVELOPMENT.md)

## License

Repository-authored content is licensed under [GPLv3](LICENSE). Third-party components and license texts are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
