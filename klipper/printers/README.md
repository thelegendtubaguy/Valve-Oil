# Per-printer files

`../klippy/` is QIDI's Max 4 01.01.07.01 Klipper on upstream v0.14.0. QIDI's Plus 5 and Smart 5 01.01.02.01 Klipper differ from the Max 4's in the files below; each printer's directory holds those files, ported the same way, to lay over `../klippy/`.

- `plus5/`: steppers enable at the move (no early enable); the MAX6675 scale; QIDI's FLOW light colour; QIDI's Plus 5 print_stats_manager.
- `smart5/`: no `endstop_sync_reset` on the mainboard; QIDI's CUC trigger dispatch; 15 s / 2.5 s async command retry; "Unable to configure MCU" stops instead of restarting the MCU; homing G-code templates on the rails; 0.3 s disable stall; QIDI's Smart 5 heaters and print_stats_manager; Valve Oil's Smart 5 homing.
