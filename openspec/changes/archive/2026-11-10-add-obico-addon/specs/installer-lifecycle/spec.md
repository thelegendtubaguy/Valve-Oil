## MODIFIED Requirements

### Requirement: Optional add-ons
The package SHALL let the operator install, update, and remove the add-ons pinned in `installer/package.yaml` (`addons:`) from Fluidd or `addons.sh`, changing nothing on the host but each add-on's directories under `~/tltg-addons/` and its systemd unit. Add-ons MUST NOT install system packages, change `printer_data/config`, or add Moonraker update-manager sections.

#### Scenario: An add-on installs offline into its own directories
- **WHEN** the operator installs an add-on while the printer is not printing or paused
- **THEN** its source, pinned wheels, and pinned source-only packages are verified against the manifest before anything is written, and it gets its own Python environment without pip under its directory
- **AND** pinned source-only packages are pure Python and are built offline after the wheels, with the setuptools among them
- **AND** its settings and identity are kept in a separate data directory that updates keep
- **AND** its unit is installed, enabled, and started, and the install succeeds only when the service stays running
- **AND** a new install that does not stay running is removed again except for its data

#### Scenario: Releases bring installed add-ons to their pinned builds
- **WHEN** install or update runs with an add-on installed whose build differs from the release's
- **THEN** the new build is staged completely before it replaces the installed one, and the previous build is put back if the new one does not stay running
- **AND** an add-on that fails to update is reported without failing the install
- **AND** an installed add-on already at the release's build is not rebuilt

#### Scenario: Installs made outside the package are left alone
- **WHEN** an add-on's unit exists without the package marker, or the add-on's own installer left its directories
- **THEN** the add-on is listed as installed outside Valve Oil, and install and remove refuse without changing anything

#### Scenario: Uninstall removes add-ons
- **WHEN** uninstall succeeds
- **THEN** every package-owned add-on is stopped and disabled, and its unit, directories, and data are removed

#### Scenario: Only one remote-access add-on is installed
- **WHEN** the operator installs OctoEverywhere while Obico is installed, by the package or otherwise, or Obico while OctoEverywhere is
- **THEN** the install is refused before anything is written, and the list names the add-on to remove first
- **AND** an add-on already installed still updates

#### Scenario: Obico leaves the printer configuration alone
- **WHEN** Obico is installed and running
- **THEN** it does not add includes to `printer.cfg` or files to `printer_data/config`, and it streams video with the Raspberry Pi Debian 11 Janus build in its source
- **AND** without an H.264 encoder it sends camera frames to Janus only while Obico's server reports someone viewing the printer, and until that has been off for a minute
- **AND** a plugin release without the entry points the package's launcher replaces fails to start, so its install or update is rolled back

#### Scenario: Obico keeps its server and link across updates
- **WHEN** an update rebuilds Obico
- **THEN** its server address and account link are kept, while its Moonraker address, log path, and remote-access target are the package's
- **AND** `addons.sh relink obico [--server URL]` forgets the account link, sets the server when one is given, and restarts Obico so it can be linked again, and refuses any server that is not an http or https address before changing anything

### Requirement: Add-ons in Fluidd
Normal install and update SHALL provide a package-owned add-ons service and a Fluidd nginx route that Fluidd's **Add-ons** settings use to list add-ons and run one install, update, remove, or relink at a time, independently of optional OS optimization policy, with the same recognition, preservation, and restoration as the Printer screen service.

#### Scenario: Only authorized same-origin requests change add-ons
- **WHEN** a client calls the add-ons route
- **THEN** it is served only if Moonraker authorizes that client, by trusted address or by Fluidd's login, and the service listens only on the printer's loopback address
- **AND** a request that changes add-ons is refused unless it is a JSON request from the same origin carrying `X-Valve-Oil: addons`
- **AND** installs, updates, and relinks are refused while a print is running or paused

#### Scenario: Changes finish and report on their own
- **WHEN** an add-on change starts
- **THEN** it runs `addons.sh` in its own session, so it finishes even if the page closes or the service restarts
- **AND** Fluidd shows its progress, log, and result, with the error as the last log line when it fails, and the add-on's state is current by the time the result is shown

#### Scenario: OctoEverywhere is linked from Fluidd
- **WHEN** OctoEverywhere is installed
- **THEN** its panel shows whether its service runs, whether it is connected to OctoEverywhere, and whether an account is linked
- **AND** an unlinked printer can be linked through its account-linking URL or a short link code fetched on request
- **AND** a printer linked while OctoEverywhere is running shows as linked without restarting OctoEverywhere

#### Scenario: Obico is linked from Fluidd
- **WHEN** Obico is installed
- **THEN** its panel shows whether its service runs, which server it uses, and whether an account is linked
- **AND** while Obico waits to be linked, the panel shows its current link code and how to enter it in Obico's manual linking
- **AND** the operator can unlink the printer, or move it to Obico's cloud or another Obico server, which unlinks it
- **AND** a blocked install names the add-on to remove first
