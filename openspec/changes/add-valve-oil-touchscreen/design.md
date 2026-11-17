## Context

`qidiclient` runs as root, pinned to CPU 0, holds `/dev/fb0` and `/dev/input/event0`, and draws with LVGL. It talks to Moonraker at `127.0.0.1:7125` as agent `QD_MAX4`. It is more than a screen. Observations so far show it also:

- writes `[mcu mcu_boxN]` and `[box_config boxN]` into `box.cfg`, and rewrites `MCU_ID.cfg`, when Boxes are connected or removed, then restarts Klipper;
- serves as the Moonraker extension agent that decrypts the QIDI cloud account for QIDI's `mqtt` component;
- downloads and installs QIDI firmware, online and from USB, and flashes the boards;
- raises firmware notices and `QDC_…` error popups;
- sets the clock with `ntpdate`;
- holds the Spaghetti Detection and Foreign Object Detection toggles as its own state;
- turns heaters off before it restarts Klipper.

Its network setup, USB drive handling, log export, and cloud slicing pages are known only from strings.

The screen is 800×480 landscape on both printers; the Plus 5's framebuffer is 480×800 with the picture turned 90° counterclockwise. The host has four cores and 486 MB of memory, and Klipper's main thread already uses about a quarter of a core while printing. `qidiclient` holds 38 to 72 MB. A full framebuffer copy costs about 28 ms of CPU.

Valve Oil already draws to the framebuffer without third-party modules (`update_screen.py`, with images rendered at build time), and the USB installer change extends that into an interactive front end that reads the touch device.

## Goals / Non-Goals

**Goals:** an operator can run a print, manage Box filament, and recover from an error without QIDI's app; the app shows what Valve Oil adds; uninstalling returns the screen to QIDI's app; it never costs a print CPU or memory it needs.

**Non-Goals:** a switch between the two apps; deleting or modifying `qidiclient`; cloud slicing; AI detection; replacing Fluidd or Valve Oil UI on the network.

## Decisions

### Replace, with uninstall as the way back

Where the Valve Oil touchscreen is installed, it owns the screen and QIDI's app does not draw. Operators get no control that swaps them. QIDI's app stays on disk, unmodified, and uninstall puts it back on the screen. Whether an operator who can't reach the web interface or SSH can uninstall from the touchscreen itself is open; the USB installer doesn't uninstall.

A QIDI firmware update that replaces `/home/qidi/QIDI_Client` or re-enables its service must not leave two apps fighting for the screen, and a Valve Oil app that can't start must not leave a dead screen. What the printer shows in that second case is open (below).

### Decide per duty: take over, leave to a screenless QIDI app, or drop

With no way to switch back, each thing `qidiclient` does besides drawing must be taken over, kept by `qidiclient` running without the screen, or dropped and documented. Likely split, to be confirmed by the inventory:

- **Take over:** the clock, heaters off before a Klipper restart, and what the app does for the QIDI Box:
  - When a Box connects or disconnects, it writes `box.cfg` and restarts Klipper. Valve Oil's Box monitor already does this.
  - It saves `box_count`. The patched app still does this after each Moonraker subscription and Klippy state change, so nothing would do it once the app is gone, and the stock macros read it.
  - It publishes each Box's serial and firmware version. Valve Oil already reads Boxes and flashes them itself.
  - It never writes `enable_box` when a Box connects. Its filament-matching page writes `enable_box` and the `value_t` tool-to-slot mappings when a print starts from the touchscreen: 1 for Box slots, 0 when a filament is mapped to the external spool, and a warning and no print when the external spool has no filament (`openspec/observations/qidi-box/state-and-commands.md`). The Valve Oil app's print-start screen does the same.
  - It updates `MCU_ID.cfg` when the mainboard reconnects.
- **Needs an answer:** QIDI firmware updates and the QIDI cloud account and app pairing. Without a switch, these either work under the Valve Oil touchscreen or need an uninstall first.
- **Drop:** cloud slicing, AI detection toggles.

Whether `qidiclient` can keep running without the screen, so it keeps its duties while the Valve Oil app draws, is the first thing to test: it would shrink the take-over list to nothing and answer the firmware and cloud questions.

### One Moonraker client, no private state

The app reads and controls the printer only through Moonraker and the package's own G-code commands, the same ones Fluidd uses. It keeps no state that Fluidd can't see, so the web and the touchscreen never disagree the way QIDI's AI toggles do.

### Screens

Five destinations on a left rail: Home, Files, Box, Control, Settings. Printing replaces Home while a print runs. The print start shows the stages of the start-print contract by name. A Klipper stop takes over the screen with Klipper's own message. No status dots: the screen's title and content say the printer's state.

From the owner's review of the concept screens:

- **Before a print:** the bed mesh is adaptive, full bed, or any mesh saved in Klipper, picked by name; timelapse and the polar cooler, on printers that have one, are set per print and carry over to the next.
- **Tune:** heater targets come first (nozzle, bed, and chamber on printers that have one), then speed, flow, and Z offset, then the part, aux, and exhaust fans.
- **Runout:** the paused screen suggests the slot with the same filament and lets the operator continue from any other slot, or load a new spool into the empty one.
- **Move:** X, Y, and Z home on their own as well as together; Home Z homes X and Y first when they aren't homed. On the screen, Z up means the bed moves toward the nozzle and Z down away from it, the opposite of Klipper's Z coordinate on these printers. Two buttons send Z all the way up and all the way down, 10 mm short of max Z.
- **Slots:** materials are base materials (PLA, PETG, ABS, ASA, TPU, PA, PC, More), colors at least three rows; the material and brand lists are edited in Valve Oil UI, not on the touchscreen.
- **Asleep:** the logo bounces around the screen, changing color at each bounce, while the clock and one status line stay put.

### Drawing

Start from the USB installer's front end, redrawing only the regions that changed. The sleep screen's moving logo redraws only the rectangles it leaves and enters, a few times a second. Measure it against a print before committing: if it can't hold a responsive screen within its CPU limit, the alternative is a native LVGL program, which is what `qidiclient` and the comparable community screens use.

### Resource limits

The service runs off Klipper's CPUs with a CPU quota and a memory ceiling, like the Printer screen service, and at lower priority than the print-critical services.

## Risks / Trade-offs

- [A `qidiclient` duty is missed and something stops working days later, such as a new Box not appearing] → complete the inventory for every supported firmware first, and test each duty with the Valve Oil app on the screen before release.
- [Two programs holding the framebuffer and touch device] → only the Valve Oil app draws; `qidiclient` runs only if it is proven to work without the screen.
- [The app takes CPU or memory from a print] → hard limits on the service, and a measured print with it running before release.
- [QIDI firmware updates change what `qidiclient` does] → the inventory is redone for each supported firmware, as source patches are today.
- [Owners lose QIDI's cloud app and QIDI firmware updates, and the only way back is uninstalling] → say so before install, in the docs and the installer's summary.
- [Scope: this is a second UI to maintain beside Fluidd's fork and Valve Oil UI] → share state and wording with `ui/` where it exists, and keep the first release to the print path.

## Open Questions

- Can `qidiclient` run without the screen and keep its duties?
- Whether the touchscreen comes with every Valve Oil install or is a choice during install, and whether uninstalling it means uninstalling Valve Oil or only the touchscreen.
- How QIDI firmware updates and QIDI's cloud app work with the Valve Oil touchscreen installed, if at all.
- What the screen shows when the Valve Oil app can't start: QIDI's app as a safety net, or an error with instructions.
- Whether uninstall is offered on the touchscreen itself.
- The USB installer's front end or LVGL, after measuring.
- Which screens make the first release; the print path (Home, Files, Printing, Box, error) is the proposed minimum.
- How "continue from another slot" after a runout works with QIDI's Box macros, and whether the finished screen's bed-release time is something the package can provide.
- What "Z all the way up" stops at (the mockup says Z 10 mm), and which printers have a polar cooler. QIDI's Max 4 app saves its preview page's polar cooler checkbox as `enable_polar_cooler`; the Valve Oil app uses the same variable, which carries over between prints.
- The wording of every screen. The font is Montserrat, as in the USB installer.
- Whether the Plus 5 ships with the first release or after it.
