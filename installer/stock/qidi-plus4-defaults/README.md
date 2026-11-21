# QIDI Plus 4 stock config snapshots

Not admitted yet: the Plus 4 is not in `installer/package.yaml` `printers:`. See `openspec/changes/support-qidi-plus4/`.

Bundled restore roots:

- `installer/stock/qidi-plus4-defaults/firmwares/plus4-2.1.1/config/`

Firmware `2.1.1` comes from QIDI's SOC payload `QD_Plus4_SOC` with SHA-256 `ef432bd700ef2b93a4f4f76a3975449f2e981d0200c1e3e58e21236d09fc1d47`, a Debian package (`QIDI-X-Plus4` `1.0.1`) whose `/root/xindi/version` reads `soc = V2.1.1`. The files are its `home/mks/printer_data/config/` entries, byte for byte; QIDI ships `moonraker.conf` with CRLF line endings and the rest with LF. Nothing needed redacting: the mainboard and toolhead are on fixed `/dev/ttyS2` and `/dev/ttyS0`.

Not in the payload, to be added from a stock printer: `plr.cfg`, `time_update.cfg`, `timelapse.cfg`, `KAMP_Settings.cfg`, and `KAMP/`.

Excluded runtime files: `config/box.cfg` (regenerated from `/home/mks/cfg_with_N`), `config/saved_variables.cfg.bak`, `config/fluidd.cfg`, and `config/saved_variables.cfg`.
