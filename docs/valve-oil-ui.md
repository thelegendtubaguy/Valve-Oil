# Valve Oil UI

[← Back to the README](../README.md)

Valve Oil UI is a new web interface for the printer, built in this project. It is early and optional. It has a dashboard, the printer's files, the QIDI Box, and Valve Oil's updates, and more screens will follow. It sits alongside Fluidd at `http://<your printer's ip>/valve-oil/`. Fluidd stays the printer's main page and doesn't change, and both can be open at once.

## What's in it

- **Home** shows the print in progress, with its thumbnail filling in as it prints, time left and when it'll finish, pause, and cancel. Cancel needs a press and hold, so a stray tap doesn't end a print. Speed, flow, and Z offset can be adjusted from the same card. Between prints, Home shows recent files with how their last print went, the loaded filament, and print history.
- **Files** lists every G-code file with its thumbnail, print time, and filament, and takes uploads. Starting a print with a QIDI Box connected first matches each filament in the file to a loaded slot, as Fluidd's Box print dialog does.
- **QIDI Box** draws the filament path from each slot through the hub and toolhead to the nozzle, and has Load, Unload, Eject, Cut, the Box heaters, tool mapping, filament editing, Box settings, and load and unload speeds. While a load or unload runs it says what the Box is doing, and when a step fails it shows QIDI's error with **Retry** and **Stop**.
- **Updates** shows the Valve Oil release, the printer's QIDI firmware, and each board's firmware, what's waiting to install, and the **Automatic updates** switch, and installs updates with **Update now**. See [Updates](#updates).
- The temperature tiles set the nozzle, bed, and chamber targets and the exhaust fan. **Emergency stop** is always at the top right.
- On a phone the screens rearrange into one column with tabs along the bottom, and everything works the same.
- Printer warnings show at the top of every screen, as in Fluidd. A Valve Oil update warning has **Details**, which opens the Updates screen, and a board Valve Oil doesn't support yet has **Report this board**. **Open Fluidd** goes back to Fluidd for everything Valve Oil UI doesn't have yet, such as the console, configuration files, and bed mesh.

## Updates

The **Updates** screen is the same as the **Valve Oil** section of Fluidd's Settings page, so use whichever is open.

- **What's installed:** the Valve Oil release, the printer's QIDI firmware, and the firmware this Valve Oil release updates it to when the printer isn't on it yet. **Boards** lists the THR, the mainboard, and each USB Box, with its firmware, whose build it is, and whether it's up to date. On the Max 4 it also lists the closed-loop motor drivers once the printer is on this release's QIDI firmware.
- **What's waiting:** a new Valve Oil release, QIDI firmware, or board firmware, or Valve Oil itself when its files on the printer are missing or changed and it needs to be installed again. With automatic updates on, the screen says whether it installs at the next update check, and around what time that check is due, or what's stopping it, such as a print running or a heater on. With them off, it says to install it with **Update now** or by running the Valve Oil installer. A board whose new firmware needs the newer QIDI firmware says it's updated right after the QIDI firmware, in the same update.
- **Automatic updates** switches whether Valve Oil installs what's waiting by itself while the printer is idle. Valve Oil checks for updates either way. It's the same setting as the switch in Fluidd. If it can't be changed, the switch turns back and the screen says why.
- **Update now** asks first, then installs everything that's waiting. When QIDI firmware or board firmware is included, the printer can't be used until the update finishes, which can take a while, and the page loses contact with the printer for part of that time. Don't turn the printer off until it finishes. **Update now** is offered only when something is waiting or an update needs finishing. When the printer can't start it, such as while printing, with a heater on, while another update or update check runs, or while an earlier update needs recovering, it's unavailable and says why, naming what to run for a recovery, and if the printer turns it down after it's pressed, the screen says why.
- **While an update runs,** the screen shows the step it's on, and **Show log** shows its output. A banner at the top of every other screen says Valve Oil is updating the printer and not to turn it off, and stays there while the printer restarts its services and Valve Oil UI can't reach it. Klipper's restart buttons are hidden until the update finishes. The Updates screen says it's waiting for the printer and reconnects by itself, also after a reload.
- **When it's done,** the result stays on the screen until you close it, with the log open if it failed. An update that stopped part way says where it stopped, and **Update now** finishes it. A result closed here is closed in Fluidd too.
- **A QIDI firmware update that failed part way** says it stopped, at which step, and what the printer reported, in full, including any command it names to run first. **Continue the update** carries it on, as does running the Valve Oil installer. While a board flash needs recovering first, it says what to run and waits.
- **A board Valve Oil doesn't support yet,** such as a Box with a bootloader it doesn't recognize, says what that means for the printer, and **Report this board** opens a GitHub issue with the printer's details filled in. A Box put back on QIDI's firmware stays on it until the next Valve Oil release. When a QIDI firmware update left the Max 4's closed-loop motor drivers on their firmware, the screen says why and to update them from the touchscreen's firmware page, and **Report this board** opens an issue.

The screen checks the printer every 10 seconds while it's open, and every 2 seconds while an update runs. Valve Oil UI also checks once when it opens and whenever it loses the printer, so it notices an automatic update that has started. The printer warnings at the top also say when an update is waiting.

## What the installer does

- **Installs the build in `~/tltg-valve-oil-ui`** and has the printer's web server serve it at `/valve-oil/`, next to Fluidd. It reaches Klipper through the same Moonraker connection Fluidd uses, so there's nothing to set up.
- **Updates replace the whole build.** Open Valve Oil UI tabs switch to the new version by themselves.
- **It's served only together with this project's [Fluidd](fluidd.md).** If the installer doesn't recognize QIDI's web server configuration for Fluidd, or `~/tltg-valve-oil-ui` already holds files the installer didn't put there, it leaves them alone and `/valve-oil/` isn't served.

## Undoing it

Uninstalling removes the `/valve-oil/` route and `~/tltg-valve-oil-ui`. Fluidd isn't affected.
