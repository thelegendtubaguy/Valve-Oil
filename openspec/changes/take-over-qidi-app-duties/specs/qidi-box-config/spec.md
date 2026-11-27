## MODIFIED Requirements

### Requirement: Valve Oil writes box.cfg for USB Boxes
On a printer whose touchscreen app is a reviewed build, Valve Oil SHALL stop the app from writing `box.cfg` or `MCU_ID.cfg` or restarting Klipper for the Boxes, and SHALL write `box.cfg` itself in QIDI's layout for the USB Boxes connected and `MCU_ID.cfg` in the app's form for the mainboard connected.

#### Scenario: The touchscreen app starts or sees a Box
- **WHEN** the patched app starts, or a USB Box connects or disconnects
- **THEN** the app writes no `box.cfg` or `MCU_ID.cfg`, saves no `box_count`, and restarts no Klipper for the Boxes
- **AND** it still shows the Boxes' firmware versions and notices

#### Scenario: Mainboard name changes
- **WHEN** the mainboard's `/dev/serial/by-id` name differs from `MCU_ID.cfg`, as after a mainboard flash
- **THEN** Valve Oil rewrites `MCU_ID.cfg` in the app's form and restarts Klipper only when the printer is idle

#### Scenario: The set of Boxes changes
- **WHEN** a USB Box connects or disconnects and the Box list then stays the same for 5 seconds
- **THEN** Valve Oil writes `box.cfg` atomically in QIDI's layout, with `# No Box Config` for no Box, only when the file would change
- **AND** a list that returns to what the file already holds, as after a Box board reset, writes nothing and restarts nothing

#### Scenario: box.cfg in another layout
- **WHEN** `box.cfg` is neither in the layout the patched app writes nor lists no Box, such as a file edited by hand
- **THEN** Valve Oil leaves it as it is and restarts nothing

#### Scenario: Unreviewed touchscreen app
- **WHEN** the touchscreen app is not a reviewed build
- **THEN** the app keeps managing `box.cfg` and `MCU_ID.cfg` and Valve Oil's monitor writes neither

