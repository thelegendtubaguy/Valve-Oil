## ADDED Requirements

### Requirement: Fault lines in klippy.log
Valve Oil's Klipper modules SHALL write one `vo_` line to `klippy.log` when a watched fault happens, naming what it affects and giving the numbers needed to judge it, and SHALL raise a printer warning only when the fault will fail the next operation and Valve Oil doesn't repair it first.

#### Scenario: A Box slot's step clock changes behind Klipper
- **WHEN** a Box's auto-feed leaves a slot's saved step clock different from the clock Klipper last left it with
- **THEN** `klippy.log` gets a line naming the slot and the offset in seconds, at the time it happens

#### Scenario: A fault Valve Oil repairs
- **WHEN** the watched fault is one Valve Oil repairs before it can fail an operation
- **THEN** it is logged and no printer warning is raised

### Requirement: Diagnostics check
Valve Oil UI SHALL offer a Diagnostics check that reads the printer's logs for every known fault signature within a bounded size and time, and lists each one found with what it means, what to do, and a docs link. It SHALL offer the result as a report to download. The same check SHALL run on a log file outside the printer.

#### Scenario: A signature is found
- **WHEN** an operator runs Diagnostics on a printer whose log shows a known fault
- **THEN** the screen names the fault in plain language with what to do and a link, and the report includes the log lines that showed it

#### Scenario: Nothing is found
- **WHEN** no known signature appears in the logs checked
- **THEN** the screen says so and says how far back it looked

#### Scenario: A large log
- **WHEN** the log is larger than the check's budget
- **THEN** the check reads the newest part within the budget and says how far back it reached, and printing is not slowed
