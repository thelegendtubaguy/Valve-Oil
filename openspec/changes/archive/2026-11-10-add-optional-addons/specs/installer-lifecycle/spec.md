## ADDED Requirements

### Requirement: Optional add-ons
The package SHALL let the operator install, update, and remove the add-ons pinned in `installer/package.yaml` (`addons:`) from Fluidd or `addons.sh`, changing nothing on the host but each add-on's directories under `~/tltg-addons/` and its systemd unit. Add-ons MUST NOT install system packages, change `printer_data/config`, or add Moonraker update-manager sections.

#### Scenario: An add-on installs offline into its own directories
- **WHEN** the operator installs an add-on while the printer is not printing or paused
- **THEN** its source and pinned wheels are verified against the manifest before anything is written, and it gets its own Python environment without pip under its directory
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

### Requirement: Add-ons in Fluidd
Normal install and update SHALL provide a package-owned add-ons service and a Fluidd nginx route that Fluidd's **Add-ons** settings use to list add-ons and run one install, update, or remove at a time, independently of optional OS optimization policy, with the same recognition, preservation, and restoration as the Printer screen service.

#### Scenario: Only authorized same-origin requests change add-ons
- **WHEN** a client calls the add-ons route
- **THEN** it is served only if Moonraker authorizes that client, by trusted address or by Fluidd's login, and the service listens only on the printer's loopback address
- **AND** a request that changes add-ons is refused unless it is a JSON request from the same origin carrying `X-Valve-Oil: addons`
- **AND** installs and updates are refused while a print is running or paused

#### Scenario: Changes finish and report on their own
- **WHEN** an add-on change starts
- **THEN** it runs `addons.sh` in its own session, so it finishes even if the page closes or the service restarts
- **AND** Fluidd shows its progress, log, and result, with the error as the last log line when it fails, and the add-on's state is current by the time the result is shown

#### Scenario: OctoEverywhere is linked from Fluidd
- **WHEN** OctoEverywhere is installed
- **THEN** its panel shows whether its service runs, whether it is connected to OctoEverywhere, and whether an account is linked
- **AND** an unlinked printer can be linked through its account-linking URL or a short link code fetched on request
- **AND** a printer linked while OctoEverywhere is running shows as linked without restarting OctoEverywhere
