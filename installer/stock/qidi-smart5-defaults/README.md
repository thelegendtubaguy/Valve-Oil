# QIDI Smart 5 stock config snapshots

Source repository: https://github.com/thelegendtubaguy/Qidi-Smart5-Defaults

Runtime restore path: `installer/runtime/legacy_manual_install.py`.

Bundled restore roots:

- `installer/stock/qidi-smart5-defaults/firmwares/01.01.02.01/config/`

Firmware `01.01.02.01` uses defaults commit `c8e9da1609d29fb3b4ed567ea4b0c216873b7c33`, release archive `S5_01.01.02.01_20261016_Release.zip` with SHA-256 `5677f126716aaa0c084c02a787145bc911cd346badd87cca350cecaeebbc041e`, firmware-manifest SHA-256 `854c31f880ef7d783ea70364456a38912673c7ef90b07d9c0b1ca24fde90d21b`, and SOC payload SHA-256 `6630997ca6bf99bdb8519fdddab3f8b522d91eeb58cf5344b5fcde47a066add7`. QIDI ships these files with CRLF line endings, and the snapshot keeps them; only copyright e-mail addresses are redacted.

The Smart 5's SOC versions repeat the Plus 5's, so the installer keys Smart 5 firmware as `smart5-<version>`; the snapshot directory keeps QIDI's version.

Legacy manual-install reset selects the restore root by detected firmware.

Excluded runtime files: `config/MCU_ID.cfg`, `config/box.cfg`, `config/fluidd.cfg`, and `config/saved_variables.cfg`.
