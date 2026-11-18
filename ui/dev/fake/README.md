# Fake Moonraker plugins

Each `*.mjs` here exports `install(fake)` and adds one feature's fake printer behavior: JSON-RPC methods, G-code,
HTTP routes, Klipper objects, and config settings. `../fake-moonraker.mjs` loads them all at startup, in name order.
See the `fake` object in `fake-moonraker.mjs` for what it offers.

Model a QIDI printer (Max 4 unless the plugin says otherwise). Use made-up serials and IDs; never copy hardware
identifiers from a real printer.
