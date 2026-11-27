## ADDED Requirements

### Requirement: QIDI's app only draws and answers its operator
On a printer whose touchscreen app is a reviewed build, Valve Oil SHALL patch the app so that, besides drawing the screen and acting on what an operator does on it, it does none of the duties this capability gives Valve Oil, none of its QIDI cloud work, and does not register as a Moonraker extension agent. The app's power-loss resume prompt SHALL keep working.

#### Scenario: Patched app running
- **WHEN** the patched app runs, a print runs, a Box or USB drive is connected, and the network comes up
- **THEN** the app writes no `print_state`, `MCU_ID.cfg`, or `box_count`, deletes no `printer-*.cfg`, mounts nothing, sends QIDI's cloud nothing an operator didn't start, announces nothing on the LAN, starts no FRP or P2P, and is not among Moonraker's agents

#### Scenario: Account screen of QIDI's app
- **WHEN** an operator opens the patched app's account screen
- **THEN** it shows LAN Only Mode instead of a cloud login QR code

#### Scenario: Unreviewed app
- **WHEN** the touchscreen app is not a reviewed build
- **THEN** the app is left as it is and keeps its duties, and Valve Oil does none of them, while the printer is still kept off QIDI's servers

### Requirement: Power-loss resume works whichever app is on the screen
While a print runs, Valve Oil SHALL record the print state QIDI's Moonraker resumes from, in the keys and form QIDI's app writes, whether or not any touchscreen app runs.

#### Scenario: Power cut during a print
- **WHEN** the printer loses power during a print and starts again
- **THEN** the touchscreen app offers to resume, and resuming continues the print from where it stopped as with the stock app

#### Scenario: Touchscreen app stopped
- **WHEN** a print runs while no touchscreen app runs
- **THEN** the print state is still recorded

#### Scenario: Klipper loses the mainboard during a print
- **WHEN** Klipper stops with `Lost communication with MCU` during a print past its first layer
- **THEN** Klipper's firmware is restarted and the print resumes as with the stock app

### Requirement: The printer stays off QIDI's servers
On every install, whichever touchscreen app is installed, Valve Oil SHALL keep every program on the printer from reaching QIDI's servers and the third-party services QIDI's software uses, from before QIDI's app and Moonraker start, and SHALL NOT block the printer's network, other internet hosts, or the services of add-ons the operator installs. Valve Oil SHALL keep QIDI's LAN-only mode on.

#### Scenario: A program connects to QIDI
- **WHEN** a program on the printer connects to QIDI's cloud, QIDI's MQTT broker or P2P server, Peergine, an address lookup service, or the time server QIDI's app names
- **THEN** the connection fails without leaving the printer, and the printer's network, Valve Oil's updates, and installed add-ons such as ZeroTier and OctoEverywhere stay reachable

#### Scenario: The printer's DNS servers
- **WHEN** the printer's DNS servers are the public ones QIDI's DHCP hook adds, such as `1.1.1.1` and `8.8.8.8`, alone or after the network's own
- **THEN** the printer resolves names through them, and QIDI's names still resolve nowhere

#### Scenario: LAN-only mode turned off
- **WHEN** an operator turns QIDI's LAN-only mode off on the touchscreen or in Fluidd, or a batch config write sets it off
- **THEN** the setting reads on again at once, and nothing connects to QIDI

#### Scenario: Uninstall
- **WHEN** Valve Oil is uninstalled
- **THEN** nothing Valve Oil blocked stays blocked, and LAN-only mode stays as it was

### Requirement: Moonraker's MQTT reaches the operator's broker
Valve Oil SHALL make QIDI's Moonraker load upstream Moonraker's MQTT component in place of QIDI's, so an `[mqtt]` section in `moonraker.conf` connects Moonraker to that broker as upstream Moonraker documents, and without one Moonraker connects to no broker and reports no failed component.

#### Scenario: Operator adds an [mqtt] section
- **WHEN** `moonraker.conf` has an `[mqtt]` section naming a broker on the operator's network and Moonraker restarts
- **THEN** Moonraker connects to it, publishes its status there, and answers API requests sent over it

#### Scenario: No [mqtt] section
- **WHEN** `moonraker.conf` has no `[mqtt]` section
- **THEN** Moonraker connects to no MQTT broker and shows no MQTT warning or failed component

### Requirement: The printer is found on the LAN
Valve Oil SHALL send the SSDP announcements and answer the `mkswifi` discovery requests QIDI's app does, so QIDI Studio finds the printer on the LAN.

#### Scenario: QIDI Studio looks for printers
- **WHEN** QIDI Studio on the same network searches for printers
- **THEN** it finds the printer by name as with the stock app

### Requirement: USB drives are mounted for every app
With QIDI's app's mounter patched out, a USB drive's partition SHALL be mounted at `printer_data/gcodes/USB` when it is connected or present at boot, with the options QIDI's app uses, and unmounted when it is removed, whichever app is on the screen.

#### Scenario: Drive connected
- **WHEN** an operator connects a USB drive
- **THEN** its files appear under `USB` in Fluidd and in the touchscreen app's file list

#### Scenario: Drive removed
- **WHEN** the drive is removed
- **THEN** it is unmounted and `USB` is empty

### Requirement: Klipper and its configuration are kept as QIDI's app keeps them
Valve Oil SHALL save `box_count` when Klipper's Box count differs from the saved one, delete `printer-*.cfg` in the config folder at boot, and pause a running print when QIDI's AI service reports spaghetti, as QIDI's app does, whichever app is on the screen.

#### Scenario: AI service reports spaghetti
- **WHEN** QIDI's AI service reports spaghetti during a print with no touchscreen app running
- **THEN** the print pauses

#### Scenario: Box count changes
- **WHEN** Klipper starts with a different number of Boxes than the saved `box_count`
- **THEN** `box_count` is saved with the new number

### Requirement: Uninstall gives the duties back to QIDI's app
Uninstall SHALL restore QIDI's app bytes and remove Valve Oil's services, rules, the block of QIDI's servers, and the Moonraker changes, so QIDI's MQTT component loads again.

#### Scenario: Uninstalled
- **WHEN** Valve Oil is uninstalled and the printer restarts
- **THEN** QIDI's app is QIDI's file, registers as Moonraker's agent, and does its duties, and no Valve Oil duty service remains
