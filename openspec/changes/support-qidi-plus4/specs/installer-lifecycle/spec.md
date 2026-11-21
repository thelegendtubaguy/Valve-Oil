## ADDED Requirements

### Requirement: QIDI Plus 4 support
The installer SHALL admit QIDI Plus 4 firmware `2.1.1` as the Plus 4, detected by `/dev_info.txt` and versioned by `/root/xindi/version` rather than a firmware manifest, with its own stock snapshot, guarded-patch variants, macro folder, user `mks`, and home `/home/mks`. It SHALL apply no board firmware, touchscreen, or system optimization to a Plus 4 that is not declared for the Plus 4.

#### Scenario: Plus 4 firmware selects the Plus 4 baseline
- **WHEN** installation starts on a printer whose `/dev_info.txt` reads `QIDI@x-plus_4` and whose `/root/xindi/version` reads `soc = V2.1.1`
- **THEN** the Plus 4 stock snapshot, the Plus 4 variant of every guarded patch, and `installer/klipper/printers/plus4/` are used
- **AND** every path the installer reads or writes is under `/home/mks` or a system path, never `/home/qidi`
- **AND** a guarded patch with no Plus 4 variant leaves the Plus 4's value unchanged

#### Scenario: Earlier Plus 4 firmware is refused
- **WHEN** installation starts on a Plus 4 whose `/root/xindi/version` reads an `soc` version other than `V2.1.1`
- **THEN** the firmware is treated as unsupported and the operator is told to update to a supported release first

#### Scenario: The Plus 4 runs the installer under its own interpreter
- **WHEN** a launcher starts on a Plus 4 whose `python3` is older than 3.9
- **THEN** it downloads the pinned standalone CPython archive, checks its SHA-256, and runs the installer with it
- **AND** if the download, the checksum, or the interpreter fails, it stops before the installer starts and nothing on the printer changes
- **AND** uninstall removes the interpreter

#### Scenario: Other printers don't change
- **WHEN** install, update, or uninstall runs on a Max 4, Plus 5, or Smart 5
- **THEN** no interpreter is downloaded and the installed files, patches, and system changes are the same as before this change

#### Scenario: Hardware and image fixes are not applied to the Plus 4
- **WHEN** install, update, or a firmware tool runs on a Plus 4
- **THEN** no Box, THR, mainboard, or closed-loop firmware is offered or flashed
- **AND** no zram module loads, apt sources aren't rewritten, and no service is disabled unless the Plus 4 declares it
- **AND** the Printer screen card and touchscreen app patches are not installed
