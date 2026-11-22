# QIDI Box

[← Back to the README](../README.md)

The **QIDI Box** card in [this project's Fluidd](fluidd.md) shows every Box's slots, the filament path, and tool mapping, and controls loading, the Box heater, and Box settings. See [thelegendtubaguy/fluidd](https://github.com/thelegendtubaguy/fluidd) for how to use it.

This page covers what Valve Oil changes for the Box.

## Tool mapping

A normal print end restores the default tool mapping, T0 → 1A, T1 → 1B, and so on. `VO_RESET_TOOL_MAPPINGS` restores it while the printer is idle.

### Printing from the external spool by tool number

A print that starts on a tool with no slot on your connected USB Boxes prints from the external spool. With one Box that's T4 and up; a tool mapped to the external spool counts too. Print start turns **Use QIDI Box** off for that print, as QIDI's touchscreen does when you choose the external spool, and the console says `T4 has no QIDI Box slot on this printer; printing from the external spool with the QIDI Box off`. A normal print end turns the Box back on. After a cancelled or failed print, the next print that starts on a Box slot turns it back on, unless you, the touchscreen, Fluidd, or Valve Oil UI set **Use QIDI Box** in between. A tool you've mapped to a Box slot still loads that slot. This is about the tool a print starts on; a tool change during a print to a tool with no Box slot still fails and pauses the print.

## Tool changes outside a print

`T0` through `T15`, which are also the tool buttons in Fluidd's Tool card, are QIDI's print-time tool change: they cut the loaded filament, unload it, and load the tool's slot without heating the nozzle. Outside a print they ran anyway, with the nozzle cold or cooling, and could leave the Box busy and the printer paused with no print. They now run only while a print is running or paused. Otherwise nothing moves and the console says:

```text
T4 not run: QIDI's tool change runs only during a print. To load a slot, use Load on the QIDI Box card or LOAD_FILAMENT_CONTROL SLOT=<n>.
```

**Load** on the QIDI Box card heats the nozzle for the slot's filament and swaps out whatever is loaded.

## Box heater

Box heater targets can be set from Fluidd, up to 65 °C, the Box's advertised maximum.

Each Box's processor temperature shows in Fluidd's temperature card as **Box1 MCU** through **Box4 MCU**. These sensors have no limits, so they never stop a print.

### Box heater switching speed

To hold its target, the Box heater switches on and off 10 times a second, Klipper's default. If that makes lights on the same circuit flicker, it can switch faster, as `pwm_cycle_time` does for the bed and chamber heaters. QIDI's Box code takes no cycle time from `box.cfg`. In seconds, for every USB Box:

```text
SAVE_VARIABLE VARIABLE=vo_box_heater_pwm_cycle_time VALUE=0.02
FIRMWARE_RESTART
```

It takes effect the next time Klipper restarts, a plain `RESTART` included. Values from `0.01` to `0.1` work; anything else, or no setting, keeps `0.1`. If you've shortened `pwm_cycle_time` for the bed or chamber, the same value is a good start. Switching faster doesn't change how much the heater heats. To go back, save `0.1` and restart Klipper. `klippy.log` shows the cycle each Box heater uses, for example `vo_box_heater_refresh: heater_box1 switches every 0.02 s`. RS-485 Boxes aren't changed.

### Turning spools while the Box heats

With this turned on, while a Box's heater has a target, during a print or not, each of its empty slots turns its spool every 5 minutes, so the spool sits in the heat on a different side. The slot's motor, which also drives the roller under the spool, turns back 25 mm at 20 mm/s, one slot at a time. That's half as far as QIDI's drying turns the spools, and slower. A print doesn't pause or slow for it.

- Only slots that are empty turn: QIDI's saved state for the slot is empty, and the slot's inlet sensor sees no filament. Put the spool on the rollers without feeding its filament. A slot with filament anywhere, parked at the hub or loaded to the toolhead, never turns.
- The first turn comes 5 minutes after the heater comes on.
- Nothing turns while the print is paused, while the Box loads, unloads, or changes tools, or while QIDI's own drying runs on that Box, which turns the spools itself.
- It's off until you turn on **Turn spools while heating** in the QIDI Box card's settings in Fluidd, or run `SAVE_VARIABLE VARIABLE=vo_box_spool_roll VALUE=1`. `VALUE=0` turns it off again. The change applies at the next turn.

`klippy.log` notes each turn, for example `vo_box_spool_roll: turning 1A's spool 25 mm`.

## Load and unload speed

A load pushes filament from the spool to the Box hub, then fast toward the toolhead, then slowly until the toolhead filament sensor trips. An unload has the extruder pull the filament back, the Box pull it back to the hub, and then back to its parked position.

The installer saves this project's defaults, unless you've already saved any of these settings, in which case all of yours are kept. Change them in Fluidd's QIDI Box card, or with `VO_BOX_MOTION` in the console. Run `VO_BOX_MOTION` with no parameters to see the current settings and the motion they give.

**Max 4.** The fast push runs in two stages, and the unload pulls back to the hub in two stages. The fast and unload speeds match QIDI's; the longer fast push, the faster approach, and the shorter extruder pull-back save time. Earlier versions saved faster speeds, 120/100 mm/s for the fast push and 115/120 mm/s for the unload, a 50 mm extruder pull-back, and a 15 mm/s approach. Updating lowers either speed pair to QIDI's speeds if both of its speeds are still at those values, raises a pull-back still at 50 mm to 55 mm and an approach still at 15 mm/s to 25 mm/s, and keeps anything you've changed.

| Parameter | What it sets | This project | QIDI | Allowed |
|---|---|---|---|---|
| `HUB_SPEED` | Speed from the spool to the Box hub, in mm/s. All slots only. | QIDI's | 85 | 10–150 |
| `FAST_LENGTH` | How far the fast push goes, in mm | 950 | 930 | 100–2000 |
| `FAST_SPEED_1` | Speed of the first fast stage, in mm/s | 85 | 85 | 10–150 |
| `FAST_SPEED_2` | Speed of the second fast stage, in mm/s | 65 | 65 | 10–150 |
| `APPROACH_SPEED` | Speed for the rest of the way to the toolhead sensor, in mm/s | 25 | 10 | 2–40, below both fast speeds |
| `LOAD_ACCEL` | Load acceleration | QIDI's | 100 | 20–400 |
| `UNLOAD_EXTRUDER_LENGTH` | How far the extruder pulls filament back before the Box takes over, in mm. All slots only. | 55 | 120 | 20–300 |
| `UNLOAD_SPEED_1`, `UNLOAD_SPEED_2` | Speeds of the two stages back to the hub, in mm/s | 65, 85 | 65, 85 | 10–150, must differ |
| `UNLOAD_ACCEL` | Unload acceleration | QIDI's | 100 | 20–400 |
| `PARK_SPEED` | Speed from the hub back to the parked position, in mm/s. All slots only. | QIDI's | 80 | 10–150 |

**Plus 5.** The fast push and the pull back to the hub are each one move, and every setting applies to all slots. QIDI's 720 mm fast push stopped about 125 mm short of the toolhead sensor on a Plus 5, leaving the rest to QIDI's 25 mm/s approach, so this project pushes 850 mm.

| Parameter | What it sets | This project | QIDI | Allowed |
|---|---|---|---|---|
| `HUB_SPEED` | Speed from the spool to the Box hub, in mm/s | QIDI's | 85 | 10–150 |
| `FAST_LENGTH` | How far the fast push goes before the approach, in mm | 850 | 720 | 100–2000 |
| `FAST_SPEED` | Speed of the fast push, in mm/s | QIDI's | 100 | 10–150 |
| `APPROACH_SPEED` | Speed for the rest of the way to the toolhead sensor, in mm/s | QIDI's | 25 | 2–40 |
| `LOAD_ACCEL` | Acceleration of the fast push | QIDI's | 100 | 20–400 |
| `UNLOAD_EXTRUDER_LENGTH` | How far the extruder pulls filament back before the Box takes over, in mm | QIDI's | 100 | 20–300 |
| `UNLOAD_SPEED` | Speed back to the hub, in mm/s | QIDI's | 100 | 10–150 |
| `UNLOAD_ACCEL` | Acceleration back to the hub | QIDI's | 50 | 20–400 |
| `PARK_SPEED` | Speed from the hub back to the parked position, in mm/s | QIDI's | 80 | 10–150 |

**Smart 5.** The settings are off on the Smart 5, whose Box loads and unloads at QIDI's values, until a Smart 5 Box load has been measured.

```gcode
VO_BOX_MOTION HUB_SPEED=120 PARK_SPEED=120
```

Settings take effect on the next load or unload, without a restart. The command refuses a combination the Box can't run, or a setting this printer's Box code doesn't use, and says why.

- **One setting back to QIDI's:** `PARAM=vendor`, for example `HUB_SPEED=vendor`.
- **Everything back to QIDI's,** including slot settings: `VO_BOX_MOTION RESET=1`. Updates won't reapply this project's defaults after that.

### Per-slot settings

On the Max 4, the fast push, approach, and unload settings can also be set for one slot, which overrides the all-slots setting. For example, to load slot 2 more gently:

```gcode
VO_BOX_MOTION SLOT=2 FAST_SPEED_1=60 FAST_SPEED_2=60 APPROACH_SPEED=8
```

Slots are numbered from 0, the same as `LOAD_FILAMENT_CONTROL SLOT=`, so the card's 1A is slot 0 and 2A is slot 4. `SLOT=2 FAST_SPEED_1=global` makes slot 2 follow the all-slots setting again, and `SLOT=2 RESET=1` clears all of slot 2's settings.

### Choosing values

- **`FAST_LENGTH`** is used exactly as set. On a Max 4, filament reached the toolhead sensor 1001–1016 mm into the push, so QIDI's 930 mm leaves about 70–85 mm for the slow approach. On a Plus 5, it reached the sensor about 845 mm into the push. The fast push stops at the toolhead sensor, so set it past the sensor and filament reaches the toolhead at full speed.
- **`UNLOAD_EXTRUDER_LENGTH`:** an unload starts with the extruder pulling the cut filament back 30 mm, then this much more at 10 mm/s, before the Box takes over. The sensor sits above the extruder gears, so once it clears, the filament is out of the gears. How far that takes varies between printers. On one Max 4 the filament left the sensor 34 mm into the unload, about 4 mm into this second pull; on another, it once left only as a 50 mm pull ended. The 55 mm default keeps some margin over that and saves about 6.5 seconds per unload over QIDI's 120 mm. If the sensor hasn't cleared by then, the extruder pulls up to 20 mm more before the unload [stops](#filament-caught-at-the-toolhead). If unloads stop that way and the filament comes out freely by hand, raise this setting, or set it back to QIDI's with `VO_BOX_MOTION UNLOAD_EXTRUDER_LENGTH=vendor`.

If a setting saved with `SAVE_VARIABLE` is invalid, the affected load or unload uses QIDI's motion and the console says why. If a QIDI firmware update changes how the Box moves filament in a way these settings don't recognize, the Box keeps QIDI's motion and the console says so.

## Eject

QIDI's eject parks the toolhead over the waste chute before and after the Box pulls the filament back, so it homes X and Y on a printer that isn't homed. When the eject only needs the Box to pull filament back, because nothing is fed past the Box hub or another slot's filament is loaded, the toolhead now stays where it is. An eject that first has to heat and unload filament from the toolhead still parks the way QIDI does. Either way the eject turns the hotend off and disables the extruder motor afterward.

## Cutting the filament

`VO_CUT_FILAMENT`, and **Cut** on the QIDI Box card, cuts the filament at the toolhead without heating the nozzle, for example to free Box filament caught in the hub from Fluidd. It homes X and Y first if either isn't homed, then runs the same cutter moves as a filament change. On the Max 4, the cutter cuts cold filament and the extruder sits above the cutter, so the extruder's 4 mm pull-back after the cut moves only filament that never enters the hotend. The piece below the cut stays in the hotend until the next load pushes it out, as after any cut.

- The nozzle heater isn't changed. Klipper normally refuses every extruder move, pull-backs included, with the nozzle below `min_extrude_temp` (135 °C on the Max 4). During the cut it allows pull-backs and still refuses pushing filament forward.
- The acceleration and positioning mode are put back afterwards, even if the cut stops with an error.
- It's refused while a print is running or paused, or while the Box is busy, including while a failed Box step waits for `FLOW_RETRY` or `FLOW_STOP`.

Every other cut works the same way at the cutter: `OPTIMIZED_CUT_FILAMENT` and `CUT_FILAMENT_1` run by hand, a cut while a print is paused, the cut in a filament change, and the cuts in QIDI's Box flows. Each allows its pull-back with the nozzle cold, and each moves the toolhead off the cutter even if the cut stops with an error, so a failed cut doesn't leave the toolhead pressed against the cutter. If one already is, see [The toolhead is stuck in the filament cutter](troubleshooting.md#the-toolhead-is-stuck-in-the-filament-cutter).

### Unloading and ejecting cold

QIDI's Box unload and eject first heat the nozzle to the top of the filament's printing range, 280 °C for some ASA, then cut and pull the filament back. With `COLD=1` they run the same steps without heating:

```gcode
UNLOAD_FILAMENT_CONTROL SLOT=1 COLD=1
EJECT_FILAMENT_CONTROL SLOT=1 COLD=1
```

**Unload** and **Eject** on the QIDI Box card send `COLD=1` unless a print is paused. The toolhead parks over the waste chute, homing X and Y first if needed, then the filament is cut and pulled back as usual. The nozzle heater stays off, and with no heat there's no ooze, so QIDI's 5 second wait and nozzle wipe at the end are skipped. Only pull-backs are allowed with the nozzle cold, as for the cut.

- `COLD=1` is refused while a print is running or paused.
- If a step fails, **Retry** (`FLOW_RETRY`) runs it cold again.
- An unload that would also unload the external spool still heats, and the console says so.
- If a cold unload keeps failing, for example because the cut didn't go all the way through, **Stop** it, then unload from the console without `COLD=1` to heat the nozzle as QIDI does.

## Moving a slot by hand

`VO_BOX_SLOT_JOG` moves a slot's filament toward the toolhead or back toward the spool, for example to back filament off the Box hub or work a tangle loose:

```gcode
VO_BOX_SLOT_JOG SLOT=1 DISTANCE=-10
```

The QIDI Box card in Fluidd has **In** and **Out** buttons that jog the selected slot 10 mm.

`DISTANCE` is in mm, positive toward the toolhead, up to 1500 either way. `SPEED` is optional: 20 mm/s by default, up to 100. Slots are numbered from 0, so 1B is slot 1.

- The slot moves whatever its sensors read and whatever the printer has recorded as loaded. QIDI's saved Box state, such as which slot is loaded, isn't changed.
- The extruder doesn't turn during a jog, so jogging the filament that's in the extruder pushes or pulls against its gears.
- A jog is refused while a print is printing or the Box is moving filament. It works while a print is paused and while a failed Box step waits for `FLOW_RETRY` or `FLOW_STOP`, for example to work filament free after [an unload stops](#filament-caught-at-the-toolhead).
- Jogging out until the slot's inlet sensor no longer sees filament takes the filament out of the slot's feeder, and jogging in won't grab it again. Push it back into the inlet by hand, and the Box loads it as it does any newly inserted filament. With long tubes to the hub, that can take well over a meter of jogging.
- A jog that brings filament to the Box hub, or clears it, updates the printer's record of which slot is at the hub, as for [stuck Box loads](#stuck-box-loads).

## Editing QIDI's filament list

On Max 4 firmware `01.01.07.01`, the Vendor and Filament lists on the touchscreen and in Fluidd come from QIDI's filament files. Fluidd's **Filaments** page in the sidebar edits the list for the 0.4 mm nozzle, `filament-0.4.json`, the one Klipper and the touchscreen read:

- **Add** or **Duplicate** a filament, then set its vendor, name, type, nozzle temperatures, Box heating limit, chamber temperature, and whether it's a support material. A new vendor name adds that vendor to the pickers. The new filament gets the next free ID and takes its drying and other settings from the filament you duplicated, or else from Generic's filament of the same type.
- **Edit** changes the same fields of any filament. Its ID stays as it is, because Box slots refer to filaments by ID. **Remove** names any Box slot set to the filament before deleting it.
- Each change is saved to the printer straight away. Klipper, the Box, and the touchscreen use it once Klipper and `qidi-client` restart: choose **Restart Klipper and touchscreen** on the page, or reboot the printer.
- **Export** downloads the list, and **Import** puts filaments from an exported file back. Import selects the filaments missing from the list, and leaves unselected the ones that differ from it, as the difference may be QIDI's.

The installer makes this possible by adding a `qidi_filament_config` folder to Fluidd's configuration files, linked to QIDI's filament files. The other nozzle sizes' files are there too, and any of them can be edited by hand: right-click a file and choose **Edit**. Keep it valid JSON, as a missing comma or bracket can break the touchscreen's filament list.

> [!WARNING]
> These are QIDI's own files, not copies. Firmware updates replace them and remove your changes, so **Export** the list before updating and **Import** it afterwards. They aren't part of the installer's backups. The [spool brands' filaments](#anycubic-elegoo-bambu-lab-and-creality-filaments) come back by themselves.

If `qidi_filament_config` already exists, the installer leaves it alone. Uninstall removes the link but never your edits.

## Spool tags

With the [Valve Oil Box firmware](firmware.md#qidi-box-firmware), which the installer puts on every USB Box, the Box reads **Anycubic**, **Elegoo**, **Bambu Lab**, **Creality**, **TigerTag**, and **OpenTag3D** spool tags as well as QIDI's. Reading works as it does for QIDI spools: when you insert filament, or with **Read RFID** on the touchscreen, the Box turns the spool past its reader and sets the slot's filament and color from the tag. The touchscreen and Fluidd show it like any other slot. Boxes on QIDI's own firmware read only QIDI tags, as before.

- A tag sets the slot to that brand's filament in QIDI's list, matched by name, such as **Anycubic PLA High Speed**, **Elegoo Rapid PLA+**, **Bambu PLA Basic**, or **Creality Hyper PLA**. Bambu Lab tags are locked with keys made from each tag's serial number. Klipper works the keys out to read the tag, which it never writes. Copies of Bambu Lab tags written to blank tags read the same way. A Bambu Lab tag carries Bambu's filament ID, which picks the filament, and a two-color spool such as PLA Silk Multi-Color sets both colors. If the list has no such filament, the slot gets the brand's plain filament of that type, then Generic's, then QIDI's. Creality tags are encrypted with keys made from each tag's serial number, which Klipper works out as for Bambu Lab's. TigerTag and OpenTag3D tags name their brand, so a TigerTag spool from Polymaker sets the Polymaker filament of that type if the list has one. Creality, TigerTag, and OpenTag3D support follows their published formats and hasn't been tried on real spools yet; if one doesn't read, [send a report](#spools-that-dont-read). A filament you added yourself with the same name, such as your own **Elegoo PLA**, is used instead of this project's.
- Tags the Box can't read leave the slot's filament as it was. OpenPrintTag tags use a kind of NFC the Box's reader can't talk to. QIDI's firmware could give such a spool the filament of the last tag that reader read, which might be the spool in the slot next to it; that no longer happens.
- Each reader serves two slots, 1A with 1B and 1C with 1D. The tag has to pass close over the reader, so a spool may need a full turn before it reads.

### Anycubic, Elegoo, Bambu Lab, and Creality filaments

Klipper adds 30 Anycubic, 32 Elegoo, 49 Bambu Lab, and 34 Creality filaments to [QIDI's filament list](#editing-qidis-filament-list) when it starts, if they're missing, so you can pick them on the touchscreen and in Fluidd with or without tags. Their names and nozzle temperatures come from the vendors' slicer profiles (Bambu Studio's for Bambu Lab, OrcaSlicer's for Creality), and their Box heating, chamber, and drying settings from QIDI's filament of the same type. QIDI's own filaments and yours are never changed, and a filament isn't added if its name or ID is already taken. After a firmware update replaces QIDI's list, they're added again once the optimized configuration is reinstalled. The touchscreen lists them after the printer's next restart.

Bambu Lab's are listed under QIDI's **Bambu** vendor, next to QIDI's own Bambu PLA, PETG, and ABS. A Bambu Lab ABS tag sets QIDI's **Bambu ABS**, and Bambu's 2.85 mm TPU 75A is left out.

Their IDs start with `QFX` (Anycubic), `QFY` (Elegoo), `QFZ` (Bambu Lab), and `QFW` (Creality), letters QIDI doesn't use. Uninstall leaves them in the list, since Box slots may use them.

### Writing spool tags

With the Valve Oil Box firmware, you can write a filament to a spool's RFID tag, so the Box sets the slot from it whenever that spool is loaded, like a QIDI spool. Use it for spools with no tag, or for blank tags you stick on yourself: MIFARE Classic 1K, NTAG213, NTAG215, or NTAG216.

On the Box card on Home in [Valve Oil UI](valve-oil-ui.md), or the QIDI Box card in Fluidd, select the slot and use the **Write spool tag** button beside the pencil. Pick the vendor, filament, and up to four colors; they start as the slot's.

- **Spool on an empty slot's rollers, filament not fed:** the Box turns the spool backward until its tag comes past the reader, at most about one and a half turns, and stops once it's written.
- **Filament fed from the slot:** the Box can't turn the spool without pulling the filament, so turn it slowly backward by hand for up to a minute.

The tag has to be on the side of the spool facing the reader; a tag on the far side is never seen, so turn the spool over. Each write is read back from the tag before it counts. Any filament in the printer's filament list can be written, whatever its vendor, including ones you added. The tag also carries the filament's name, so a printer whose list has that name under a different ID uses its own.

Only blank tags and tags Valve Oil wrote before are written. Tags holding Anycubic, Elegoo, Creality, TigerTag, or OpenTag3D data, Bambu Lab tags, and locked tags are left alone, and the dialog says why. Each reader serves two slots, but a write goes only to the tag of the spool being turned; a parked spool beside it is never written.

The commands behind it, for macros:

```gcode
VO_BOX_RFID_WRITE SLOT=2
VO_BOX_RFID_WRITE SLOT=2 FILAMENT=QFY08 COLORS=E5484D
```

Without `FILAMENT=` and `COLORS=` it writes the slot's saved filament and color. `NAME=0` leaves the name off, and `SECONDS=` (5 to 300, default 60) sets how long it waits when you turn the spool by hand.

`VO_BOX_RFID_TEST SLOT= ACTION=SCAN` lists every tag a slot's reader sees, with its type and contents, while you turn spools by hand or while the Box turns that slot's spool (`TURN=-700`). It's a diagnostic tool; `ACTION=WRITE` writes with every option exposed.

### Spools that don't read

When the Box reads a tag it doesn't recognize, Klipper saves what it read as `vo_rfid_<reason>_<reader>_<time>.json` in its log folder (Fluidd: **Logs**). Reports from releases before 26.11.16.1 are named `tltg_rfid_…`. To read a tag on purpose, put the spool in a slot without feeding its filament and run:

```gcode
VO_BOX_RFID_REPORT SLOT=7
```

Slots are numbered from 0, so 2D is slot 7. For up to 60 seconds (`SECONDS=` up to 300) it waits for a tag; turn the spool slowly by hand until it reports. It saves `vo_rfid_report_<slot>_<time>.json` with everything the tag lets it read. Attach the file to a [spool tag issue](https://github.com/thelegendtubaguy/valve-oil/issues/new?template=spool-tag.yml) to get that brand supported. Reports never include the tag's serial number.

## Filaments missing from QIDI's list

If a Box slot or the external spool has a saved filament that isn't in QIDI's list, such as `None`, QIDI's load fails with `Internal error on command:"LOAD_FILAMENT_CONTROL"` and shuts Klipper down. The load now uses QIDI PLA Basic temperatures instead, the way QIDI already does for some older saved values, and the console names the saved filament. Choose the slot's filament again to clear it.

Setting the external spool to a filament that isn't in QIDI's list is refused with `External spool filament not changed`, as QIDI already does for Box slots.

## Stuck Box loads

If a slot's filament sits at the Box hub but the printer has no slot recorded as loaded, a Box load has QIDI unload the hub first, and QIDI's unload stops with `无法识别到已进料耗材` ("can't identify the loaded filament") and `Step BOX_UNLOAD failed. Pausing flow.` It does the same on every retry. This can happen when a Box disconnects while it feeds newly inserted filament up to the hub.

The printer now remembers which slot fed filament to the hub, in the saved variable `vo_box_hub_slot`. When that slot, or the only slot holding filament, still has filament at its inlet, the load unloads it and carries on, and the console names the slot.

Otherwise the console says so and lists the slots with filament, and the nozzle is turned off unless a print is running or paused. Follow the filament from the hub back to its slot, then save that slot and continue the load:

```gcode
SAVE_VARIABLE VARIABLE=last_load_slot VALUE='"slot1"'
FLOW_RETRY
```

Slots are numbered from 0: `slot0` is 1A, `slot1` is 1B, and `slot4` is 2A. `FLOW_RETRY` heats the nozzle again and repeats the unload. Each time, `klippy.log` gets a `vo_box_loaded_slot: Box unload has no loaded slot` line with the Box's state; include it when [reporting a problem](troubleshooting.md#reporting-a-problem).

## Failed Box loads and unloads

When a step of a Box load, unload, or eject fails, QIDI pauses it with a line such as `Step BOX_LOAD failed. Pausing flow.`, usually after an error such as `Code:QDE_004_006; Message:Extruder loading failure.` The nozzle stays at the load temperature. The touchscreen offers no way out, but the QIDI Box card in Fluidd does, and so does the console:

- **`FLOW_RETRY`** heats the nozzle again and repeats the failed step.
- **`FLOW_STOP`** ends the paused flow. With no print running or paused, it also turns the nozzle off. During a print, the nozzle stays hot so the print can resume.

With QIDI's own code, `FLOW_STOP` on a paused flow never finished: every later command waited behind it, and only restarting Klipper, followed by `FIRMWARE_RESTART`, got the printer back. It now finishes straight away.

A step can also end with an error instead of failing, such as `Error during homing e_endstop: Communication timeout during homing` from a Box motor move. QIDI then left the flow running with the nozzle hot, and neither the card nor `FLOW_RETRY` could do anything with it. The flow is now paused on that step, with `Step BOX_LOAD failed. Pausing flow.` before the error, so Retry and Stop work as for any failed step.

QIDI's Box moves, such as the park over the waste chute, assume absolute positioning but don't set it. If relative positioning was left on, for example by a command that stopped with an error before switching back, every heat step failed with `Move out of range` until Klipper restarted. A load, unload, or eject started or retried with no print running or paused now switches to absolute positioning first, as `G90` does. During a print, positioning is left as the print set it.

### Failed tool changes during a print

When a step of a filament change during a print fails, QIDI pauses the print, and **Resume** finishes the change from wherever the filament stopped, then carries on printing. A step that ended with an error instead, such as `Error during homing b_endstop: Communication timeout during homing` while unloading, cancelled the print. It now pauses the print the same way, and the console says which step failed and why, for example `T1 failed at BOX_UNLOAD: Error during homing b_endstop: Communication timeout during homing. The print is paused; RESUME tries the tool change again.` Only a Klipper shutdown, such as a lost Box connection, still ends the print.

### Filament caught at the toolhead

An unload pulls the filament back with the extruder, with the slot's feeder turning along with it, until the toolhead filament sensor is clear, and only then does the Box pull it back. While the sensor still detects filament, QIDI pulls 10 mm more at a time, up to seven times, then moves or shakes the toolhead and starts over, three times in all, before `Code:QDE_004_008; Message:Extruder unloading failure.` That's more than 400 mm and about a minute and a half of pulling on filament that isn't moving, which grinds it.

The unload now stops once the extruder has pulled 20 mm past the pull-back without the sensor clearing, 105 mm in all with the Max 4's default pull-back, and the console says what's wrong, for example `Box unload of 1D stopped: the toolhead filament sensor still detects filament after the extruder pulled it back 105 mm, ...`. Something is holding the filament: at the toolhead, such as a cutter blade that hasn't sprung back or a swollen cut end, or in the Box, such as a slot feeder that won't turn. Free it, then:

- During a print, **Resume** tries the tool change again.
- Otherwise, **Retry** (`FLOW_RETRY`) repeats the unload, or **Stop** (`FLOW_STOP`) ends it.

Before 26.11.22.2, this also happened with nothing caught, on the first unload after a long run on one slot, with the Box clicking. While printing, the Box feeds filament on its own whenever the buffer asks for it. When print start turned that feeding back on in the second a feed was running, the Box lost the slot motor's timing for the rest of the print, and the unload's pull-back, which runs the slot motor with the extruder, ran it up to 25 seconds late or in bursts. The unload now lines the slot motor's timing up before it pulls back, as a load does, after stopping the feeding on that slot's Box even when QIDI's code has set it up on the other Box.

The slot can be [jogged](#moving-a-slot-by-hand) while the print or the failed unload is paused, for example `VO_BOX_SLOT_JOG SLOT=2 DISTANCE=-20` to pull 1C back, or the **Out** button on the Box card in Valve Oil UI.

When Klipper didn't move the extruder for those pulls at all, the message says so instead, for example `Box unload of 1D stopped: Klipper didn't move the extruder for the 105 mm of pull-back QIDI's unload asked for, ...`, and names the [cancelled part](#filament-changes-inside-a-cancelled-part) the print is inside when that's why.

`klippy.log` gets a line for every unload from the toolhead, starting `vo_box_flow_recovery: 1D unload from the toolhead:`. It has both sensors before and after, how far the extruder was asked to pull and how far it moved, how far Klipper moved the slot's motor during those pulls, where the toolhead sensor cleared, and how many times the Box's buffer switch made the Box feed filament forward since the last unload and during this one. Attach it when you report an unload that stopped.

### Filament changes inside a cancelled part

Klipper drops every move while a print is inside a part you've cancelled (skipped), extruder moves included. OrcaSlicer marks where a part ends only when the next part starts, so a filament change right after a part's last line runs inside that part. When that part was cancelled, the change ran with the Box motors but without the toolhead or extruder: no cut, no pull-back, no purge. The unload then stopped as if filament were [caught at the toolhead](#filament-caught-at-the-toolhead), and the paused print stayed inside the cancelled part, so retracting, unloading, and loading by hand did nothing either.

Moves now go through during a filament change, from QIDI's `TOOL_CHANGE_START` to its `TOOL_CHANGE_END`, and while the print is paused. A change that starts inside a cancelled part first raises the nozzle straight up to the height the change expects, since the slicer's lift before it was dropped, then cuts, unloads, loads, and purges as usual. The cancelled part's own lines are still skipped, before and after the change. This works with any slicer and with files already sliced.

### A slot that runs out during a tool change

When the loaded slot runs out of filament and the print then changes tool, QIDI purges what's left at the waste chute and is meant to switch the print to another slot with the same filament. A bug in QIDI's Box code stops it there with `'BoxExtruderStepper' object has no attribute 'loaded_stepper'`. The print is paused, and the console now says which slot ran out, for example `T3 paused the print: 1C ran out of filament. ...`. **Resume** loads the new tool's slot and carries on. Put filament in the slot that ran out before the print needs it again, or the print pauses there.

### Filament stopped between the hub and the toolhead

A load that gets the filament to the Box hub but not to the toolhead, for example because the tube to the toolhead is longer than `FAST_LENGTH` allows for, leaves the filament in the tube between them. QIDI already counts that slot as loaded. Its Unload then does nothing but park the toolhead and report success, and its Eject heats the nozzle first.

With no print running or paused, **Unload** and **Eject** of that slot now pull its filament back out of the hub, a paused flow is stopped first, and the nozzle isn't heated:

- **Unload** pulls the filament back until the hub sensor clears, then about 220–260 mm further, which parks it in the Box as QIDI's unload does. The slot is then ready to load again.
- **Eject** pulls it back until the hub sensor clears, then runs QIDI's eject, which pulls the filament out of the slot's feeder.

The slot is the one that last fed filament to the hub, or the one QIDI last loaded, as long as its inlet sensor still detects filament. When neither tells, the only slot with filament is used. Otherwise, or when you ask for a different slot, nothing moves and the console says why. If the slot's filament leaves its inlet sensor while the hub sensor still detects filament, the filament at the hub isn't that slot's, or it broke. The pull stops there, and the console says so.

Unload of a slot whose filament isn't past the hub doesn't run. The console says so, instead of QIDI parking the toolhead and reporting success.

## Resuming a paused print

Pausing turns the nozzle off. With the Box, **Resume** first moves the toolhead to the waste chute and heats the nozzle back to the temperature it had when the print paused, then runs QIDI's resume, which reloads the filament and carries on printing. QIDI's resume cuts before it heats, so after a pause long enough for the nozzle to cool below 135 °C it stopped with `Extrude below minimum temp`, and the print stayed paused until the nozzle was heated by hand.

## Runouts during Box loads and unloads

When filament leaves the toolhead sensor during a print, Klipper waits for the command that's running to finish before it handles the runout. A Box unload inside a resume or the print-start load runs within one command, so its runout used to be handled minutes later, once the new filament was already loaded: the print paused and QIDI's auto-reload ran, or, if the print had been cancelled meanwhile, the idle printer paused, homed, and reported `Auto reload failed`. The runout is now handled only if the toolhead sensor still reports no filament and the print is still running unpaused; otherwise the console says `VO_FILAMENT_SENSOR: ignoring an earlier toolhead sensor trip` and nothing else happens.

## Connecting, disconnecting, and moving Boxes

QIDI's touchscreen app writes the USB Boxes into `box.cfg` and restarts Klipper whenever the Boxes it finds change. It counted a Box board resetting, which drops the Boxes off USB for a few seconds after a Klipper start or a Box firmware flash, as a change, so Klipper could restart several times in a row for nothing. The restart at the app's start didn't check for a print, and its restart turned the heaters off. It numbered the Boxes by USB port, so a Box moved to another port took another Box's number and slots.

With Valve Oil, its Box monitor does this instead:

- **A Box keeps its number,** and so its slots and tool mapping, on any USB port. The first install records each Box's number from `box.cfg` as it is, so nothing is renumbered.
- **`box.cfg` changes only when the Boxes do.** About 5 seconds after a Box connects or disconnects and the Boxes stop changing, it writes `box.cfg` in QIDI's layout, and only when the file would be different. Boxes dropping off USB and coming back leave it as it is.
- **A new Box** gets the lowest number no connected Box has. While a Box is disconnected, the Boxes after it move up, since `box.cfg` can't skip a number, and they get their numbers and slots back when it's connected again. Connecting a new Box instead takes the disconnected Box's number.
- **Klipper restarts only while the printer is idle,** with no print running or paused and no heater on, and the heaters are never turned off for it. Otherwise a printer warning says `A USB Box was connected or disconnected.`, and Klipper restarts once the printer is idle.
- **Installs and board firmware go first.** While the installer runs or a board's firmware is written or waits for its rescue, the monitor changes nothing, and catches up when that ends.

The touchscreen still shows the Boxes, their firmware, and its Box notices. This applies to the touchscreen apps of Max 4 firmware `01.01.07.01` and Plus 5 and Smart 5 firmware `01.01.02.01`; with any other app, the app keeps managing `box.cfg` as before. A `box.cfg` you've edited, so it's no longer in QIDI's layout, is left as it is. Uninstall puts QIDI's app back, which writes `box.cfg` again when it next starts. RS-485 Boxes aren't in `box.cfg`, and aren't affected.

## Box disconnects

See [Fixes](fixes.md#qidi-box-disconnects) for what this project changes, and [Troubleshooting](troubleshooting.md#box-link-monitor) to record why a Box drops.

## RS-485 Boxes

QIDI's Max 4 firmware `01.01.07.01` and Plus 5 and Smart 5 firmware `01.01.02.01` include support for Boxes, buffers, and filament racks that connect over RS-485 through a USB adapter, alongside the USB Boxes that run Klipper. This project hasn't been tested with RS-485 hardware yet, so it watches for it:

- **If you have RS-485 hardware,** the console asks you to open a [GitHub issue](https://github.com/thelegendtubaguy/valve-oil/issues) once per Klipper start. Attach `vo_box485_report.json` and `vo_box485.log` from the `logs` folder in Fluidd. The log records what QIDI's code sends to and receives from each device, and the report summarizes the setup. Chip ids and USB serial numbers are replaced with codes that can't be traced back to your hardware. Nothing about how the hardware works is changed.
- **To write the report yourself,** run `VO_RS_BOX_REPORT`. It says whether RS-485 hardware was found, and if it was, starts the log right away.
- **Without RS-485 hardware,** nothing is written. QIDI's software looks for an RS-485 adapter and logs `Can not find 485 device` each time it's missing. That's normal for USB Boxes. Until RS-485 hardware has been seen, it now looks every 30 seconds instead of every 2, so a newly connected adapter can take up to 30 seconds to be found, and `klippy.log` gets that line once an hour, with a count.

> [!WARNING]
> Don't put a USB Box's id from `box.cfg` into `[box485] serial_port` in `printer.cfg`. It stops the `Can not find 485 device` lines, but QIDI's RS-485 code then reads that Box's connection and takes Klipper's messages, so the Box drops. The optimized configuration keeps the RS-485 code from opening a Klipper port and says so in the console. Set `serial_port` back to `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`.
