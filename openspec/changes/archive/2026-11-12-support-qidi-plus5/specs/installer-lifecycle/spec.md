## ADDED Requirements

### Requirement: QIDI Plus 5 support
The installer SHALL admit QIDI Plus 5 firmware `01.01.02.01` as the Plus 5, with its own stock snapshot, guarded-patch variants, source-patch expectations, and macro folder. It SHALL apply Max 4 firmware and host fixes to a Plus 5 only where that Plus 5's hardware, bootloader, or kernel is recognized for them.

#### Scenario: Plus 5 firmware selects the Plus 5 baseline
- **WHEN** installation starts on firmware `01.01.02.01` whose firmware manifest SOC file starts with `QD_PLUS5_SOC_`
- **THEN** the Plus 5 stock snapshot, the Plus 5 variant of every guarded patch, and `installer/klipper/printers/plus5/` are used
- **AND** a guarded patch with no Plus 5 variant leaves the Plus 5's value unchanged

#### Scenario: Earlier Plus 5 firmware is refused
- **WHEN** installation starts on Plus 5 firmware `01.01.01.06` or `01.01.01.07`
- **THEN** the firmware is treated as unsupported and the operator is told to update to a supported release first

#### Scenario: Max 4 hardware fixes are not applied to the Plus 5
- **WHEN** install, update, or a firmware tool runs on a Plus 5
- **THEN** the mainboard closed-loop fix is not offered
- **AND** the THR and Box firmware fixes are offered only for bootloader builds already admitted for them
- **AND** zram modules load only on a kernel build declared for them

#### Scenario: The rotated Plus 5 screen is shown upright
- **WHEN** the Printer screen card shows a Plus 5, whose landscape UI is drawn into a 480×800 framebuffer
- **THEN** the card shows the UI upright
- **AND** a tap on the card presses the touchscreen at the control shown under it

## MODIFIED Requirements

### Requirement: QIDI Box motion release defaults
The installer SHALL save the detected printer's release Box motion profile only when no Box motion setting exists in Klipper saved variables, and SHALL move a setting group that is still at an earlier release's defaults to the current release defaults.

#### Scenario: Printers without motion settings receive the release profile
- **WHEN** install, update, or idle automatic-update repair finds none of the Box motion settings
- **THEN** it saves the detected printer's release profile through Moonraker after Klipper is ready and verifies the live values
- **AND** a Max 4 gets a 950 mm fast push, QIDI's fast and unload speeds, a 25 mm/s approach, and a 55 mm extruder pull-back, and a Plus 5 gets an 850 mm fast push with QIDI's values for the rest

#### Scenario: Operator motion settings are preserved
- **WHEN** any Box motion setting exists, including one saved as `None` to use the vendor value
- **THEN** no motion setting is written, except an earlier default moved as below
- **AND** uninstall does not remove or reset the settings

#### Scenario: Earlier faster speed defaults are lowered
- **WHEN** install, update, or idle automatic-update repair on a Max 4 finds both fast load speeds at 120 and 100 mm/s, or both unload speeds at 115 and 120 mm/s
- **THEN** it saves that pair as the release defaults, 85 and 65 mm/s or 65 and 85 mm/s, and verifies the live values
- **AND** a pair with either speed changed is left unchanged

#### Scenario: Earlier Max 4 pull-back and approach defaults are raised
- **WHEN** install, update, or idle automatic-update repair on a Max 4 finds the extruder pull-back at 50 mm or the approach speed at 15 mm/s
- **THEN** it saves that setting as the release default, 55 mm or 25 mm/s, and verifies the live value
- **AND** every other motion setting is left unchanged
