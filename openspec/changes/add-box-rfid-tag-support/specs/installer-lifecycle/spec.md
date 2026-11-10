## MODIFIED Requirements

### Requirement: QIDI filament database in Fluidd
Normal install and update SHALL expose QIDI's touchscreen filament database directory as `config/qidi_filament_config` in Fluidd's configuration files, independently of optional OS optimization policy, without copying or backing up the database files. Their contents SHALL change only by the optimized configuration adding missing catalog filaments.

#### Scenario: Link is created where the database exists
- **WHEN** the QIDI filament database directory exists and `config/qidi_filament_config` is absent
- **THEN** the installer creates a symlink to the directory and records ownership for restoration
- **AND** reconciliation recreates an owned link that is absent and leaves a current link unchanged

#### Scenario: Missing databases and existing entries are preserved
- **WHEN** the database directory is missing or `config/qidi_filament_config` already exists as anything other than the installer's link
- **THEN** no link is created, the existing entry is left unchanged, and the outcome is recorded without failing other host operations

#### Scenario: Config backups tolerate the link
- **WHEN** an install, update, uninstall, or restore backs up or restores `config/`
- **THEN** the link is neither followed nor archived, and a restore keeps an existing link

#### Scenario: Restoration removes only the installer's link
- **WHEN** uninstall or transaction recovery runs
- **THEN** the link is removed only while it still points at the database directory; an operator-replaced entry is preserved
