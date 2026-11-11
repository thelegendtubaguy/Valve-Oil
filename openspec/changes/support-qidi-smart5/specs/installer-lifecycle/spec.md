## MODIFIED Requirements

### Requirement: Firmware-gated ownership
The installer SHALL identify the printer from its detected supported firmware, select one complete baseline for that firmware, and change only recognized stock state or state proven to be installer-owned. Each supported firmware key SHALL belong to exactly one printer in `installer/package.yaml`. A firmware key SHALL be the SOC version, prefixed with the printer's firmware key prefix when the printer declares one, and every firmware a printer with a prefix lists SHALL carry it.

#### Scenario: Supported firmware selects one baseline
- **WHEN** installation starts on firmware declared for a printer in `installer/package.yaml`
- **THEN** exactly one complete firmware-scoped variant is selected for every required target
- **AND** configuration, managed-source expectations, and stock snapshots come from that printer's baseline
- **AND** the managed macro tree is the common optimized macros plus that printer's macro folder, which provides the same macro names on every printer and no file that the common macros also provide

#### Scenario: Firmware naming another printer is refused
- **WHEN** the firmware manifest's SOC file name does not carry the model prefix of a printer that lists the detected firmware under its key
- **THEN** the firmware is treated as unsupported

#### Scenario: Printers sharing a SOC version are told apart by the SOC file name
- **WHEN** two printers ship the same SOC version and the firmware manifest's SOC file name carries one printer's model prefix
- **THEN** the installer uses that printer's firmware key, baseline, and macro folder
- **AND** installed state, backups, and source-patch records carry that key

#### Scenario: A shared SOC version without a SOC file name is refused
- **WHEN** the firmware manifest has no SOC file name and its version is declared for more than one printer
- **THEN** the firmware is treated as unsupported

#### Scenario: Invalid admission fails before unsafe work
- **WHEN** firmware is unreadable or unsupported
- **THEN** the operation fails before reading installed state, creating a backup, or writing live files
- **AND** incomplete or ambiguous baseline data or an unclassifiable managed target fails before backup or mutation
- **AND** unowned live content is preserved

#### Scenario: Legacy installation is migrated conservatively
- **WHEN** legacy optimized markers exist without valid installer state
- **THEN** migration requires an idle printer and a validated stock snapshot for the detected firmware
- **AND** vendor-managed, user-specific, and sensitive configuration remains preserved

## ADDED Requirements

### Requirement: QIDI Smart 5 support
The installer SHALL admit QIDI Smart 5 firmware `01.01.02.01` as the Smart 5, under firmware key `smart5-01.01.02.01`, with its own stock snapshot, guarded-patch variants, source-patch expectations, and macro folder. It SHALL apply firmware and host fixes built for other printers to a Smart 5 only where that Smart 5's hardware, firmware image, bootloader, or kernel is recognized for them.

#### Scenario: Smart 5 firmware selects the Smart 5 baseline
- **WHEN** installation starts on firmware `01.01.02.01` whose firmware manifest SOC file starts with `S5_SOC_`
- **THEN** the Smart 5 stock snapshot, the Smart 5 variant of every guarded patch, and `installer/klipper/printers/smart5/` are used
- **AND** a guarded patch with no Smart 5 variant leaves the Smart 5's value unchanged

#### Scenario: Other printers' hardware fixes are not applied to the Smart 5
- **WHEN** install, update, or a firmware tool runs on a Smart 5
- **THEN** the THR FIFO fix and the mainboard closed-loop fix are not offered
- **AND** the Box firmware fix is offered only for bootloader builds already admitted for it
- **AND** zram modules load only on a kernel build declared for them

#### Scenario: Smart 5 Box motion keeps QIDI's values
- **WHEN** install, update, or idle automatic-update repair runs on a Smart 5
- **THEN** no Box motion release default is saved
