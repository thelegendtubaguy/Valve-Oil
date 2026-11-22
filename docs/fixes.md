# Fixes for QIDI bugs

[← Back to the README](../README.md)

These are installed whether or not you choose the optional OS optimizations, and uninstall puts QIDI's original behavior back.

## The real error instead of `MCU shutdown`

When Klipper stopped on an error, such as a heater or fan fault or a lost Box connection, QIDI's print status module could crash Klipper during the shutdown, so the screen, Fluidd, and slicers showed only `MCU shutdown`. Now the original reason stays on screen. `FIRMWARE_RESTART` recovers as usual.

## QIDI Box disconnects

Boxes dropped with `Lost communication with MCU 'mcu_boxN'`, most often at print start or end with the Box heater on. Klipper was too busy to tell the Box heater it was still in control, and QIDI's Box firmware restarts the Box instead of reporting that. This project:

- Has each Box heater check in every second instead of every 3.75, so it rides out pauses of about three seconds.
- Saves the bed mesh at print start in the background, instead of freezing Klipper for over two seconds.
- Makes `SAVE_VARIABLE` skip saves that change nothing and stop re-reading the file it just wrote, so print end takes under 10 ms to save settings instead of about 0.4 s. Turning the printer off during a save can no longer leave the file empty or half written.
- Stops reading Box heater sensors after a shutdown, which crashed Klipper and hid the real error.

For a Box that still drops, the [Valve Oil Box firmware](firmware.md#qidi-box-firmware) keeps it connected so Klipper shows why, and the [Box link monitor](troubleshooting.md#box-link-monitor) records what happened.

## Klipper slowing down during long Box prints

Every QIDI Box load left three of QIDI's timers behind in Klipper, from the load-cell check that confirms the filament reached the nozzle. Klipper checks every timer many times a second, so it got busier with each filament change. After about 400 changes it was busy most of the time even while idle, and a Box motor move could fail with `Communication timeout during homing`, which cancelled the print. Those timers are now removed once QIDI is done with them, and a filament change that fails that way [pauses the print](qidi-box.md#failed-tool-changes-during-a-print) instead.

Klipper also watches how busy it is. If its timers keep piling up, or it stays more than 45% busy for five minutes, the console says so, at most once an hour. If a homing move times out, the console says which board's sync timed out, how far apart each board sent its status reports and Klipper received them, and how busy Klipper was just before. That includes the nozzle load-cell probe's `WEIGHT sensor error`, which is how QIDI reports a timed-out probe move, not a fault in the load cell. A move that involves more than one board stays armed only while each board reports every 15 ms. Reports sent far apart point at the board; reports sent on time but received far apart point at the connection or the printer's CPU. Each board's timeout is renewed from the other board's reports, so the message also says how much time each board had left when the report that renews it reached Klipper, below 0 when it came after the deadline, how long reports took to reach Klipper, and any retransmits or serial-port errors during the move. `klippy.log` also gets each board's last reports, how often Klipper's threads were interrupted, and which CPUs handled the serial and USB connections. Moves that pass get one `klippy.log` line for each run of homing or probing, with the least time any board had left, so a printer that comes close shows it before a move fails. `klippy.log` gets a `vo_klipper_health` line every minute with the timer count and how busy Klipper was.

In Fluidd, the **System Utilization** card on the System page charts both: **Klipper Main Thread** under Klipper Load, and **Klipper Timers**. Klipper Load counts all of Klipper's threads; the main thread is the one that has to keep up. The timer count stays around 60.

Klipper also does less work on its main loop, so Box motor moves keep more headroom:

- QIDI's Box modules save their own settings the slow way that `SAVE_VARIABLE` used to: a full rewrite of `saved_variables.cfg` and a re-read, even when nothing changed. Box print start saved about 20 settings, holding Klipper for about 1.3 seconds, and every filament change saved about 8 more. They now skip unchanged settings and write the file safely, as `SAVE_VARIABLE` does.
- Valve Oil's filament-change, temperature-wait, and homing macros no longer copy QIDI's large Box and configuration status each time they run, which held Klipper for 10–50 ms each.
- QIDI's Box status, which Fluidd and the touchscreen read every second, takes about half as long to build, and every macro, QIDI's included, reads printer settings and status faster. QIDI's start-up macro, which held Klipper for about 0.8 seconds after every start, is faster too.
- With USB Boxes only, QIDI's software looks for an RS-485 adapter every 30 seconds instead of every 2.
- Spoolman's slot tracking reads the loaded slot directly instead of building QIDI's Box status twice a second.
- Klipper checks its timers, about 70 of them, many times a second; that now takes about a third less time.
- QIDI's once-a-second status update for Fluidd and the touchscreen skips macros whose values haven't changed and compares QIDI's Box status faster. Fluidd and the touchscreen receive the same updates.
- Klipper encodes the status it sends Moonraker with `msgspec` instead of Python's `json`, in about a third of the time. The installer adds `msgspec` to QIDI's Klipper. Anything `msgspec` can't encode goes out with `json` as before.
- While Klipper streams a print's G-code, moves let Klipper's other timers run every 20 ms. A run of moves (planning, bed mesh splitting, step generation) used to hold them for 50–150 ms a few thousand times an hour. Homing and probing moves keep QIDI's timing.
- QIDI's Box status reports each Box's temperature again only once it moves 0.1 °C. Its wobble of a few hundredths of a degree resent the whole 8.5 KB Box list about once a second, three quarters of all the status Klipper sends. Fluidd's Box temperatures come from each Box's own sensor, which is unchanged.
- Moves longer than half a second that don't extrude, such as the end G-code lowering the bed 170 mm, are queued as half-second pieces along the same line at the same speed. Klipper used to generate all the steps of the bed drop at once (12,800 a millimetre for each Max 4 Z motor), holding its main loop for about 1.5 seconds. As with any run of moves, the G-code after such a move runs once less than 3 seconds of it is left.

The timer and status changes come from the same module as the [fixes from newer Klipper](#fixes-from-newer-klipper), with the same check: each is skipped, and listed as skipped in `klippy.log`, if QIDI's code isn't exactly the version it was written for.

## Klipper sharing the CPU with the touchscreen

QIDI runs Klipper, Moonraker, and the touchscreen app at the same CPU priority, so a busy touchscreen app or Moonraker could delay Klipper. Klipper now gets the CPU first, then Moonraker, then the touchscreen app.

QIDI's `qidi-tuning` service keeps Klipper on CPUs 1 and 2, keeps the web server and camera on CPU 0, and pins the toolhead's and mainboard's connections to CPUs 2 and 3. It did this by running a script again every 35 seconds, more than 8,500 times in 3.5 days on the test Max 4, and QIDI's `irqbalance` moved the connections away in between. Within 25 seconds of a Klipper restart, it had put the mainboard's on Klipper's CPUs. Now systemd starts each service on its CPUs at its priority, the script runs once at boot, and `irqbalance` leaves the toolhead's and mainboard's connections where the script puts them. The toolhead's connection gets its CPU once Klipper first opens it, a few seconds after boot.

Moonraker also gets CPU 3, which has little else to do when AI detection is off. On the test Max 4 with the camera streaming and Fluidd connected, other programs interrupted Moonraker about 18 times a second instead of 45, and status updates reached Fluidd and the touchscreen in a median 8.7 ms instead of 12.5 ms. With AI detection running, the difference is small.

## Moonraker freezing while it connects to QIDI's cloud

On a printer linked to a QIDI account, QIDI's Moonraker keeps a connection open to QIDI's cloud server. Each time it connects, it waits for the server to answer the start of the encrypted connection, and QIDI's code does that waiting in the one loop that runs everything else in Moonraker, with a 60 second limit. If the server accepts the connection and then doesn't answer, Moonraker stops for those 60 seconds. Fluidd and the touchscreen get no answers, Klipper's state isn't read, and `moonraker.log` says `EVENT LOOP BLOCKED: 62 seconds` after `The handshake operation timed out`. QIDI's code retries 2 seconds later, so a server that keeps doing it freezes Moonraker again and again.

At power-on this can show on the touchscreen as **Printer service failed to start** (`QDC_400_005_015_001`) while Klipper is running normally.

Moonraker now waits for the server's answer in the background, for at most 10 seconds, and keeps answering Fluidd, the touchscreen, and Klipper while it waits. When the answer doesn't come, QIDI's code gets the same error and retries as before. On the Max 4 test printer, with a test server that accepts connections and never answers, each Moonraker request took about 59 seconds before the change and about 15 ms after it.

This doesn't change whether QIDI's cloud accepts your printer. For the `MQTT Connection Failed: Not authorized` warning, see the next section.

## `Not authorized` from QIDI's cloud after the printer starts

QIDI's Moonraker reads the printer's QIDI account through the touchscreen app. At power-on it connected to QIDI's cloud before the touchscreen app had started, so it connected without the account to QIDI's fallback server, which refused it. Fluidd then showed **Moonraker has failed plugins** with `mqtt`, and `MQTT Connection Failed: Not authorized`, even when the printer connected with its account seconds later. QIDI's code also reads the account through whichever program registered with Moonraker first as an agent, so when OrcaSlicer connected to the printer before the touchscreen app did, the account couldn't be read at all.

Now, on a printer linked to a QIDI account, Moonraker waits for the touchscreen app before it first connects to QIDI's cloud, and always reads the account through the touchscreen app. If the touchscreen app hasn't connected after two minutes, Moonraker connects as QIDI's code did before.

The warning still appears when QIDI's server refuses the printer's account, or answers too late. QIDI's code waits 2 seconds for the server to accept the account, then starts again, and keeps trying. Moonraker drops the warning as soon as the printer connects. Fluidd used to keep showing it until the page was reloaded; now, while Fluidd shows a Moonraker warning or failed plugin, it checks Moonraker again every 10 seconds, so the warning goes away within 10 seconds of the printer connecting. When `moonraker.log` has `MQTT connection successful` after the last `Not authorized`, the printer is connected to QIDI's cloud.

## MQTT errors after turning LAN only mode on

QIDI's LAN only mode, on the touchscreen or in [Valve Oil UI](settings.md#qidis-cloud), disconnects the printer from QIDI's cloud. QIDI's code only closed the connection when it was connected, though. On a printer with no QIDI account, turning LAN only mode off makes it try to connect, QIDI's cloud refuses it every few seconds with `Bad user name or password`, and turning LAN only mode back on left those tries running. The touchscreen kept showing an MQTT connection error, and Fluidd showed **Moonraker has failed plugins** with `mqtt` and `MQTT Connection Failed: Bad user name or password`, until Moonraker restarted.

Now, turning LAN only mode on also stops QIDI's tries to connect and clears that warning. A printer that is connected to QIDI's cloud is disconnected by QIDI's own code, as before.

## Touchscreen offering firmware you already have

With this project's [toolhead board](firmware.md#toolhead-accelerometer-fix) or [mainboard](firmware.md#mainboard-closed-loop-reply-fix) firmware, QIDI's touchscreen app showed **New MCU or THR firmware has been detected. Please go to the firmware page to update.** The firmware page then had nothing to update. The pop-up treats any board version that isn't exactly QIDI's as out of date, and this project's firmware reports QIDI's version followed by `-valve-oil.` and a revision number. The firmware page compares versions another way and sees them as current.

The pop-up now accepts a board version that starts with QIDI's. It still appears when QIDI ships a newer version than the board has.

The installer changes QIDI's touchscreen app for this, on Max 4 firmware `01.01.07.01` and Plus 5 and Smart 5 firmware `01.01.02.01`. The same change [hides QIDI's online firmware updates](install.md#qidis-own-firmware-updates) on the touchscreen. It keeps QIDI's original, and uninstall puts it back. A touchscreen app the installer doesn't recognize, such as one from another QIDI firmware, is left as it is.

## QIDI app connections disabled for security

On a printer linked to a QIDI account, QIDI's touchscreen app runs the peer-to-peer (P2P) remote access that QIDI's phone app uses for the camera and files. Some of its connections are disabled due to security concerns: the printer no longer relays other people's sessions or sends its own through other people's devices, and it ignores requests and settings from QIDI's P2P server about them.

QIDI's phone app still reaches the printer on the same network. Away from it, for example over a phone's cellular connection, the session goes through QIDI's own relay server, and on the Smart 5 test printer the camera showed a black picture there.

The installer makes this change in the same touchscreen app builds as [the firmware notice fix](#touchscreen-offering-firmware-you-already-have), keeps QIDI's original, and uninstall puts it back.

## Fixes from newer Klipper

QIDI's Klipper is upstream Klipper from August 2024 with QIDI's changes. These later upstream fixes are applied to it when Klipper starts:

- Homing moves, including the Box's, set up their endstop sync for the start of the move. Before, the setup could wait behind the move's own step messages.
- A Box or toolhead board that restarts by itself now stops Klipper with `MCU '…' spontaneous restart`. Before, Klipper carried on and failed later with a misleading error.
- Once Klipper loses contact with a Box or board, commands waiting on it fail with `No communication with MCU` instead of waiting forever and holding up every later command.
- A stepper disable that arrives while its driver is still being enabled waits for the enable to finish.
- Heaters turn off if Klipper's main loop stops for 5 seconds while temperature readings keep arriving. A heater's temperature reads 0 only 7 seconds after its last reading, instead of 5, so a lost board is reported as lost rather than as a heater fault.
- A Klipper timer can't be started again while an earlier run of it is still waiting.
- Python's garbage collector skips everything Klipper set up at startup, so its periodic full collections have less to check.

`klippy.log` lists the fixes applied at each start. A fix is skipped, and listed as skipped, if the Klipper code it replaces isn't exactly the version it was written for.

## Fixes in Klipper's C code

Part of Klipper is compiled C code, `klippy/chelper/c_helper.so`. Valve Oil replaces QIDI's copy with the same Klipper code built with these later upstream fixes:

- Every `SET_PRESSURE_ADVANCE` that changed the value kept about 50 bytes of memory that Klipper never freed. OrcaSlicer's adaptive pressure advance changes it many times a layer, so Klipper's memory grew through each print, and through the next, until Klipper restarted. The Max 4, Plus 5, and Smart 5 have about 480 MB of memory for everything they run.
- During homing and probing that involve more than one board, Klipper could send a board a homing timeout earlier than the one it had already given it, so the board could stop the move with `Communication timeout during homing` sooner than intended.
- A step far in the future could be written past the end of Klipper's step queue, corrupting Klipper's memory.
- Each time Klipper closed a board's connection, at `RESTART`, `FIRMWARE_RESTART`, or a failed attempt to connect, it left two files open.

Valve Oil recognizes the library QIDI ships with Max 4 firmware `01.01.06.04` and later and Plus 5 and Smart 5 firmware `01.01.02.01`. Any other library, such as one Klipper rebuilt after its source files were edited, is left as it is. QIDI's source files aren't changed. Klipper rebuilds the library at startup only when a source file is newer than it, which Valve Oil's never is, so Klipper uses it as it is. Install restarts Klipper once to load it, and uninstall puts QIDI's back. `sha256sum ~/klipper/klippy/chelper/c_helper.so` prints `f8b7b7bb28dddff1f28a0775d0ace4696cd7ac95094a8e2dbbef16b1a49ac593` for Valve Oil's library and `214d1ab79a78b2aa28ba8cd7ba0d7383afcaaf9d36561112aa7bf73cf6591714` for QIDI's.

## Probe points retrying forever

When a point's two probe samples disagree by more than `samples_tolerance` (0.02 mm), Klipper probes the point again, up to `samples_tolerance_retries` times (10). QIDI's Klipper restarted that count on every retry, so a point whose samples never agreed was probed forever, and print start hung on it. The point now gets at most that many retries, then the bed mesh, `Z_TILT_ADJUST`, or `PROBE` fails with `Probe samples exceed samples_tolerance`, as in upstream Klipper. During print start that error stops the print.

QIDI also re-zeroed the nozzle load cell for the retry while the nozzle was still pressed on the bed. On a Max 4, the samples after it read 45–57 µm low, and they went into the mesh. The load cell is now re-zeroed once the nozzle has lifted off the bed, as it is before every point. The Max 4, the Plus 5, and the Smart 5 have the same code. This fix comes from the same module as the [fixes from newer Klipper](#fixes-from-newer-klipper), with the same check.

## Motors turning on late

On the Max 4, QIDI's Klipper turns a motor on 0.3 seconds before it moves, so the motor holds its position first. After homing turns X and Y off, the first homing move starts only about 0.16 seconds ahead, so the turn-on was scheduled in the past. The mainboard ran it as soon as it arrived and logged `Timer too close` in `klippy.log`, nearly always between the `ENABLE delay: stepper=stepper_x` and `stepper_y` lines. The turn-on now goes as early as the mainboard can still take it on time, about 0.14 seconds before the move during homing, and never after the move. When 0.3 seconds early is still possible, nothing changes. QIDI's `ENABLE delay` line in `klippy.log` shows the time used. It comes from the same module as the [fixes from newer Klipper](#fixes-from-newer-klipper), with the same check. The Plus 5's Klipper turns motors on at the move, so it needs no change.

A few `Timer too close` lines during prints have no known cause yet. When Klipper sends a board a command, such as a pin or fan change, whose time has already passed or is under 5 ms away, `klippy.log` gets a `late MCU command` warning naming the board, the command, how late it was, and the Klipper code that sent it, at most once every 10 seconds for each command. A command sent so far ahead that the board's clock can't reach it, as in [Fans and lights switching at the wrong time](#fans-and-lights-switching-at-the-wrong-time), gets an `MCU command too far ahead` warning the same way. Please include those lines when reporting a problem.

## Fans and lights switching at the wrong time

`M106`, `M107`, `SET_FAN_SPEED`, and `SET_PIN` take effect when the move before them ends. QIDI's Klipper sent the board its command as soon as it planned that move, however far ahead that was. A board compares times in 32 bits, so it can tell a time ahead from one already past only within about 12.8 seconds on the Max 4 and Plus 5 mainboards, 3.6 seconds on the Smart 5's, and 30 seconds on the toolhead board. On the Plus 5, a part fan change during a pause went to the toolhead board about 30 seconds ahead, which the board reads as already past. On the Smart 5, QIDI's start G-code sent the part fan's `M106` about 12 seconds ahead, which its mainboard read as 2.4 seconds past. Standard Klipper board firmware stops the printer with `Timer too close` for such a command.

These commands now wait, as in newer upstream Klipper, and go to the board at most 3 seconds before they take effect. Klipper keeps no more than that queued during a print, so in a print's normal flow they go out as before. A request that a later one replaces before it's due is dropped. QIDI's own fan code still sets the fan, with its kick start and fan speed check, and QIDI's smart case light goes through the same queue. This comes from the same module as the [fixes from newer Klipper](#fixes-from-newer-klipper), with the same check.

## X/Y motors skipping at top speed

QIDI sets the Max 4's top speed to 800 mm/s, but that limit applies to the toolhead, not to each motor. The Max 4 is a CoreXY: a diagonal move turns one X/Y motor up to 1.41 times as fast as the toolhead moves, so at 800 mm/s one motor's belt runs at about 1130 mm/s. On the test printer, back-and-forth diagonals at 800 mm/s and 30000 mm/s² made the X motor skip about 60 mm of belt, loudly, and the printer didn't notice. At 500 mm/s, where the motor's belt runs at about 710 mm/s, nothing skipped.

The top speed is now 565 mm/s, which keeps every motor at or below 800 mm/s of belt in any direction. Moves between bed mesh and leveling points, and this project's fastest travel moves, run at 565 mm/s instead of 600 and 750. A slicer or command that sets a higher speed with `SET_VELOCITY_LIMIT VELOCITY=` or `M203` gets 565 mm/s and a console note, as QIDI's Klipper already does for acceleration. The slicer's own speeds, up to 500 mm/s travel in QIDI's Max 4 profile, are unchanged. Install and update lower any `max_velocity`, bed mesh `speed`, or Z-tilt `speed` in `printer.cfg` above 565 to 565, including ones you set yourself; lower values are kept, and uninstall puts back the values you had. The Plus 5's X/Y motors have no closed loop, and at 565 mm/s Z-tilt travel they skipped steps, so its top speed is 400 mm/s instead of QIDI's 600, and its bed mesh and Z-tilt `speed` are raised from 150 to 400. Install and update lower each of those above 400 to 400 the same way, and every faster move on the Plus 5, chute travel included, runs at 400 mm/s. On the Max 4, the [X/Y step-loss monitor](tools.md#xy-step-loss-monitor) can tell you if a motor skips anyway.

## X/Y homing grinding into the frame

On the Max 4, X and Y home against the frame, and each motor's closed-loop driver decides when the toolhead has reached it. QIDI's driver firmware only watches the motor's speed. It waits until the motor has turned about 3.8 mm and passed about 48 mm/s, then signals the end of the axis when the motor slows below about 29 mm/s. It never checks how far the motor has fallen behind. When it misses, Klipper keeps driving the motor into the frame for the rest of the homing move, up to 590 mm on X, grinding loudly, and then stops with `No trigger on x after full movement`. A home that starts with the toolhead against the frame always misses, so after one miss the next home usually misses too.

The homing guard watches each X and Y homing move:

- The other motor's driver is asked how far behind its motor is, up to 50 times a second: the Y driver while X homes, the X driver while Y homes. The Max 4 is CoreXY, so both motors turn for either axis and both stop when the toolhead does. The homing motor's own driver can't be asked, because answering during a touch makes it report the end of the axis early, in the middle of the bed. When the motor falls about 6 mm of belt behind, something is holding the toolhead, so the move stops at once. The toolhead backs off 20 mm and homes that axis again, and the console shows `Stopped homing X because the motors fell 6.4 mm behind before the X driver reported the end of the axis. Backing off and homing X again.`
- If Y's second try is stopped too, the toolhead has usually run into the filament cutter at the front left, where it ends up when a print is cancelled or the power goes off during a filament cut. Y backs off 20 mm, X homes, which moves the toolhead to the right, away from the cutter, and Y homes again. The console says `Backing off and homing X, which moves the toolhead to the right, then homing Y again.`
- If X's second try is stopped, or a Y try after X homed, homing fails with an error saying it happened twice or again after homing X. Check that nothing blocks the toolhead and that the belts are tight before homing again.
- The guard can only watch while the driver answers. If it gets no answer to two questions in a row during a homing move, about half a second, it stops the move and homing fails with `Stopped homing X because the X and Y motor drivers stopped answering`. Turn the printer off and on, then home again. Homing doesn't try again here, because the toolhead could be anywhere.
- Each X/Y homing move drives no further than the axis is long plus 6 mm, 400 mm on X and 421 mm on Y, instead of QIDI's 1.5 times the axis, 591 and 623 mm. A home the driver misses still grinds into the frame for the rest of that move when the guard is off. Y needs more than 400 mm because QIDI parks the toolhead at the chute at Y403, 404 mm from where Y homes. The touches after the first are just as long: the driver sometimes ends a touch by itself in the middle of the bed, and QIDI's homing notices only because the next touch carries on to the frame and comes out a different length.
- When an X or Y homing move fails, for any reason, both X/Y drivers are set back to working mode before the error shows. QIDI's homing skipped that, leaving both motors on their lower homing current and the homing driver ready to report the end of the axis until the next home.
- While a closed-loop motor's firmware is being updated (`UPGRADE_MOTOR`), the guard asks the drivers nothing, because a question sent during the transfer can make the update fail. X and Y homing moves during the update home as they do with the guard off, and `klippy.log` says the guard isn't watching them.
- `klippy.log` gets one `vo_homing_guard:` line for every X and Y homing move, with what the driver reported. Please include those lines when reporting a homing problem.

The guard is on by default. To turn it off, run `SAVE_VARIABLE VARIABLE=vo_homing_guard VALUE=0`; the travel limit stays. The Plus 5 and Smart 5 have no closed-loop X/Y drivers, and their homing is unchanged.

## Unknown Box filaments shutting Klipper down

Loading a slot whose saved filament isn't in QIDI's list no longer shuts Klipper down. See [QIDI Box](qidi-box.md#filaments-missing-from-qidis-list).

## Klipper shutting down at startup on `INIT_RFID_READ`

QIDI's startup macro `INIT_GLOBALS` runs 5 seconds into Klipper's startup and ends with `INIT_RFID_READ`, which has the QIDI Box read its spool tags. QIDI's Box code only finishes starting near the end of Klipper's startup, and `INIT_RFID_READ` doesn't check for that. On a start where something earlier took more than those 5 seconds, the command failed with `Internal error on command:"INIT_RFID_READ"` (`'NoneType' object has no attribute 'allVariables'` in `klippy.log`) and Klipper shut down until a firmware restart. This happens with or without a Box, since the macro runs the command either way.

`INIT_RFID_READ` now waits until QIDI's Box code has started, then runs as before. If that takes more than a minute, the startup tag read is left out and `klippy.log` says so. A start where QIDI's Box code is ready in time is unchanged.

## Box loads stuck at `无法识别到已进料耗材`

A Box load no longer stalls on every try when a slot's filament is at the Box hub but no slot is recorded as loaded. See [QIDI Box](qidi-box.md#stuck-box-loads).

## `FLOW_STOP` hanging Klipper

Stopping a failed Box load with `FLOW_STOP` no longer leaves every later command waiting and the nozzle hot until Klipper is restarted. See [QIDI Box](qidi-box.md#failed-box-loads-and-unloads).

## Box filament stranded between the hub and the toolhead

Unload and Eject now pull back a slot's filament that a failed load left between the Box hub and the toolhead. See [QIDI Box](qidi-box.md#filament-stopped-between-the-hub-and-the-toolhead).

## External spool loads ending in `Step EXT_WIPE failed`

Loading the external spool from the touchscreen ended with `Step EXT_WIPE failed. Pausing flow.` after the purge and nozzle wipe had run, so QIDI stopped the load instead of finishing it and turned the nozzle heater off. QIDI's wipe step reports a failure every time it runs. Seen on the Smart 5; the Max 4 and Plus 5 have the same QIDI code. The load now finishes. A wipe that hits an error, such as a move out of range, still pauses the load as before.

## Klipper shutting down when a print starts on a tool with no Box slot

A print that started on a tool with no slot on the connected USB Boxes, such as T4 with one Box, or with a tool mapped to the external spool, shut Klipper down with `Internal error on command:"BOX_PRINT_START"` and `Unknown config object 'box_stepper slot4'` after heating, probing, and meshing. QIDI's Box code looks for a slot that doesn't exist. Such a print now uses the external spool with the Box off; see [Printing from the external spool by tool number](qidi-box.md#printing-from-the-external-spool-by-tool-number).

## Multi-plate 3MF files

A 3MF file started without a plate number, for example from Fluidd, a reprint, or the job queue, prints the plate it was sliced on instead of plate 1. Before, files exported from a plate other than plate 1 failed with `3MF processing error: [Errno 2] No such file or directory`.

## Filaments a file uses

Fluidd's Box print dialog lists the filaments Moonraker says a file uses. Moonraker now reads them from what the slicer wrote for that file: for a 3MF, the filaments its plate uses; for an OrcaSlicer G-code file, the filaments it gives a length. Before, OrcaSlicer G-code files named none, and when OrcaSlicer exported several plates into one 3MF, only the plate selected in OrcaSlicer named any, and it named every plate's filaments.

While a Box is connected, Fluidd opens the Box print dialog for every print. For a file that still names no filaments, it lists every filament in the file's slicer project and needs a slot for at least one.

## Faster Moonraker startup

Unchanged 3MF files reuse their metadata instead of being re-extracted every time Moonraker starts. The first restart after installing rebuilds it once, and after that only new or changed files are read. Manual metadata rescans keep QIDI's thumbnails, and a failed rescan keeps the previous metadata.

## Z offset reset to 0

Cancelling a print during its start no longer wipes your saved Z offset to `0`. See [Print start](print-start.md#z-offset).

## Box eject homing the toolhead

An eject that only needs the Box to pull filament back no longer homes and parks the toolhead. See [QIDI Box](qidi-box.md#eject).

## Cancelled parts printing again after a power loss

When a print resumes after a power loss, parts you cancelled (skipped) before the power went off stay skipped. QIDI's resume cancels them again, but it restarts the file a little before where the power went off, partway through the part being printed, and Klipper didn't know which part that was. When it was a cancelled part, the resumed print went straight back to printing it, in the air, until it reached the next part. The resume now starts that part again once the toolhead is back in place, so a cancelled part is skipped from the first line, and a resume in the middle of a filament change inside one finishes the change as [usual](qidi-box.md#filament-changes-inside-a-cancelled-part).

QIDI's resume keeps no cancelled parts for a print with more than 64 parts, so those print again.
