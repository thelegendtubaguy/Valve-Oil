// Console and notifications: Klipper's help for more commands, a gcode store with the mix a printer really holds
// (temperature reports, `//` messages, `!!` errors, commands), replies for a few informational commands, Moonraker
// announcements with dismiss and snooze, a Klipper config warning, and a small in-memory Moonraker database so the
// console's history, filters, and settings save (another plugin's database methods replace these if loaded later).

const HELP = {
  RESTART: 'Reload config file and restart host software',
  FIRMWARE_RESTART: 'Restart firmware, host, and reload config',
  STATUS: 'Report the printer status',
  HELP: 'Report the list of available extended G-Code commands',
  RESPOND: 'Echo the message prepended with a prefix',
  M105: '',
  M109: 'G-Code macro',
  M190: 'G-Code macro',
  M117: '',
  M220: '',
  M221: '',
  QUERY_ENDSTOPS: 'Report on the status of each endstop',
  GET_POSITION: 'Return information on the current location of the toolhead',
  SET_VELOCITY_LIMIT: 'Set printer velocity limits',
  SET_PRESSURE_ADVANCE: 'Set pressure advance parameters',
  SET_GCODE_OFFSET: 'Set a virtual offset to g-code positions',
  Z_TILT_ADJUST: 'Adjust the Z tilt',
  PROBE: 'Probe Z-height at current XY position',
  QUERY_PROBE: 'Return the status of the z-probe',
  PROBE_CALIBRATE: "Calibrate the probe's z_offset",
  SAVE_CONFIG: 'Overwrite config file (waits for a background save)',
  TURN_OFF_HEATERS: 'Turn off all heaters',
  SET_FAN_SPEED: 'Sets the speed of a fan',
  BED_MESH_CLEAR: 'Clear the Mesh so no z-adjustment is made',
  BED_MESH_PROFILE: 'Bed Mesh Persistent Storage management',
  SET_KINEMATIC_POSITION: 'Force a low-level kinematic position',
  DUMP_TMC: 'Read and display TMC stepper driver registers',
  SET_TMC_CURRENT: 'Set the current of a TMC driver',
  QUERY_FILAMENT_SENSOR: 'Query the status of the Filament Sensor',
  SET_INPUT_SHAPER: 'Set cartesian parameters for input shaper',
  EXCLUDE_OBJECT: 'Cancel moves inside a specified objects',
  SDCARD_PRINT_FILE: 'Loads a SD file and starts the print.  May include files in subdirectories.',
  CANCEL_PRINT: 'Cancels the current print.\nUsage: CANCEL_PRINT',
  PAUSE: 'Pauses the current print.\nUsage: PAUSE [X=<pos>] [Y=<pos>] [Z=<pos>] [E=<retract_length>] [B=<beeps>]',
  RESUME: 'G-Code macro',
  SET_HEATER_TEMPERATURE: 'Sets a heater temperature',
  TEMPERATURE_WAIT: 'Wait for a temperature on a sensor',
  SET_GCODE_VARIABLE: 'Set the value of a G-Code macro variable',
  ACCEPT: '',
  ABORT: '',
  TESTZ: '',
  _VO_BOX_STATE: 'G-Code macro',
  VO_KLIPPY_SHUTDOWN: 'Fake: shut Klipper down for SECONDS (30), as an MCU error does',
}

export function install(fake) {
  fake.commands(HELP)
  const { state, now, pushConsole, broadcast } = fake

  // The gcode store as a printer that heated, printed, and stopped on an error holds it.
  const t0 = now() - 3600
  const seed = [
    ['command', 'G28'],
    ['response', '// Homing X Y Z'],
    ['command', 'M190 S60'],
    ...Array.from({ length: 6 }, (_, i) => ['response', `B:${(48 + i * 2.1).toFixed(1)} /60.0 T0:${(32 + i).toFixed(1)} /0.0`]),
    ['response', 'ok'],
    ['command', 'M109 S220'],
    ...Array.from({ length: 6 }, (_, i) => ['response', `B:60.0 /60.0 T0:${(170 + i * 9.4).toFixed(1)} /220.0`]),
    ['command', 'SET_PRESSURE_ADVANCE ADVANCE=0.032'],
    ['response', '// pressure_advance: 0.032000\n// pressure_advance_smooth_time: 0.030000'],
    ['command', 'BED_MESH_PROFILE LOAD=default'],
    ['response', '// Use BED_MESH_CALIBRATE to probe a new mesh, or SAVE_CONFIG to keep this one.'],
    ['command', 'G1 X420 F6000'],
    ['response', '!! Move out of range: 420.000 195.000 10.000 [0.000]'],
    ['command', 'QUERY_ENDSTOPS'],
    ['response', 'x:open y:open z:TRIGGERED'],
    ['command', 'SET_VELOCITY_LIMIT'],
    ['response', '// max_velocity: 600.000000\n// max_accel: 20000.000000\n// minimum_cruise_ratio: 0.500000\n// square_corner_velocity: 8.000000'],
    ['command', 'FOO_BAR'],
    ['response', '// Unknown command:"FOO_BAR"'],
    ['response', '// action:notification Filament runout on 1B'],
    ['response', '!! Code:QDE_004_002; Message:Filament not detected at the toolhead.'],
    ['response', '// Run VO_BOX_SLOT_JOG to back the filament out, then try again.'],
  ]
  state.console.unshift(...seed.map(([type, message], i) => ({ type, message, time: t0 + i * 20 })))

  fake.gcode((C, p) => {
    if (C === 'M105') return pushConsole(`ok B:${temp('heater_bed')} T0:${temp('extruder')}`), true
    if (C === 'STATUS') return pushConsole(state.klippy === 'ready' ? '// Klipper state: Ready' : '// Klipper state: Not ready'), true
    if (C === 'HELP') return pushConsole(['// Available extended commands:', ...Object.entries(HELP).filter(([, h]) => h).map(([c, h]) => `// ${c}: ${h.split('\n')[0]}`)].join('\n')), true
    if (C === 'RESPOND') return pushConsole(`${p.PREFIX ?? (p.TYPE === 'error' ? '!!' : '//')} ${p.MSG ?? ''}`), true
    if (C === 'GET_POSITION') {
      const [x, y, z] = state.status.toolhead.position
      return pushConsole(`// mcu: stepper_x:${Math.round(x * 80)} stepper_y:${Math.round(y * 80)} stepper_z:${Math.round(z * 400)}\n// toolhead: X:${x.toFixed(6)} Y:${y.toFixed(6)} Z:${z.toFixed(6)} E:0.000000\n// gcode: X:${x.toFixed(6)} Y:${y.toFixed(6)} Z:${z.toFixed(6)} E:0.000000`), true
    }
    if (C === 'QUERY_ENDSTOPS') return pushConsole('x:open y:open z:open'), true
    // VO_KLIPPY_SHUTDOWN [SECONDS=30]: Klipper shuts down on an MCU error and comes back on its own, to see the console
    // while Klipper is stopped.
    if (C === 'VO_KLIPPY_SHUTDOWN') {
      setTimeout(() => {
        state.klippy = 'shutdown'
        pushConsole("!! MCU 'mcu' shutdown: Timer too close")
        pushConsole('// This often indicates the host computer is overloaded. Check\n// for other processes consuming excessive CPU time, high swap\n// usage, disk errors, overheating, unstable voltage, or\n// similar system problems on the host computer.\n// Once the underlying issue is corrected, use the\n// "FIRMWARE_RESTART" command to reset the firmware, reload the\n// config, and restart the host software.\n// Printer is shutdown')
        broadcast('notify_klippy_shutdown', [])
      }, 200)
      setTimeout(() => {
        state.klippy = 'ready'
        pushConsole('// Klipper state: Ready')
        broadcast('notify_klippy_ready', [])
      }, Number(p.SECONDS ?? 30) * 1000)
      return true
    }
    return false
  })
  const temp = (h) => {
    const o = state.status[h] ?? {}
    return `${(o.temperature ?? 0).toFixed(1)} /${(o.target ?? 0).toFixed(1)}`
  }

  // Klipper's configuration warnings (configfile.warnings), as a deprecated option reports one.
  fake.status((st) => {
    st.configfile = {
      ...st.configfile,
      warnings: [{ type: 'deprecated_option', message: "Option 'max_accel_to_decel' in section 'printer' is deprecated.", section: 'printer', option: 'max_accel_to_decel' }],
    }
  })

  // ---- announcements ----
  const day = 86400
  const entries = [
    {
      entry_id: 'moonraker/2026-11-03-1',
      url: 'https://moonraker.readthedocs.io/en/latest/changelog/',
      title: 'Moonraker configuration changes',
      description: 'The [authorization] section now defaults to trusting only local clients. Check moonraker.conf before updating.',
      priority: 'high',
      date: now() - 3 * day,
      dismissed: false,
      date_dismissed: null,
      dismiss_wake: null,
      source: 'moonraker',
      feed: 'moonraker',
    },
    {
      entry_id: 'klipper/2026-10-20-1',
      url: 'https://www.klipper3d.org/Config_Changes.html',
      title: 'Klipper config changes',
      description: 'max_accel_to_decel is replaced by minimum_cruise_ratio. See the config changes page for details.',
      priority: 'normal',
      date: now() - 17 * day,
      dismissed: false,
      date_dismissed: null,
      dismiss_wake: null,
      source: 'moonraker',
      feed: 'klipper',
    },
  ]
  const open = () => ({ entries: entries.filter((e) => !e.dismissed) })
  fake.handle({
    'server.announcements.list': (p) => ({ entries: p.include_dismissed ? entries : open().entries, feeds: ['moonraker', 'klipper'] }),
    'server.announcements.update': () => ({ ...open(), modified: false }),
    'server.announcements.feeds': () => ({ feeds: ['moonraker', 'klipper'] }),
    'server.announcements.dismiss': (p) => {
      const e = entries.find((x) => x.entry_id === p.entry_id)
      if (!e) throw fake.rpcError(`No announcement entry exists for id: ${p.entry_id}`)
      Object.assign(e, { dismissed: true, date_dismissed: now(), dismiss_wake: p.wake_time ?? null })
      broadcast('notify_announcement_dismissed', [{ entry_id: e.entry_id }])
      // Snoozed: Moonraker wakes it after wake_time seconds. The fake wakes it after a tenth of that, at most 30 s.
      if (p.wake_time)
        setTimeout(() => {
          Object.assign(e, { dismissed: false, date_dismissed: null, dismiss_wake: null })
          broadcast('notify_announcement_wake', [{ entry_id: e.entry_id }])
        }, Math.min(30, p.wake_time / 10) * 1000)
      return { entry_id: e.entry_id }
    },
  })

  // ---- a small database, enough for settings shared with Fluidd ----
  const db = (state.database ??= {})
  const path = (key) => String(key ?? '').split('.').filter(Boolean)
  const getAt = (ns, key) => path(key).reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), db[ns])
  fake.handle({
    'server.database.list': () => ({ namespaces: Object.keys(db), backups: [] }),
    'server.database.get_item': (p) => {
      const value = p.key ? getAt(p.namespace, p.key) : db[p.namespace]
      if (value === undefined) {
        const e = fake.rpcError(`Key '${p.key}' in namespace '${p.namespace}' not found`)
        e.code = 404
        throw e
      }
      return { namespace: p.namespace, key: p.key ?? null, value }
    },
    'server.database.post_item': (p) => {
      const keys = path(p.key)
      let o = (db[p.namespace] ??= {})
      for (const k of keys.slice(0, -1)) o = o[k] && typeof o[k] === 'object' ? o[k] : (o[k] = {})
      o[keys[keys.length - 1]] = p.value
      return { namespace: p.namespace, key: p.key, value: p.value }
    },
    'server.database.delete_item': (p) => {
      const keys = path(p.key)
      const parent = keys.slice(0, -1).reduce((o, k) => o?.[k], db[p.namespace])
      const value = parent?.[keys[keys.length - 1]]
      if (parent) delete parent[keys[keys.length - 1]]
      return { namespace: p.namespace, key: p.key, value }
    },
  })
  db.fluidd ??= {}
  db.fluidd.console ??= { commandHistory: ['SET_PRESSURE_ADVANCE ADVANCE=0.032', 'QUERY_ENDSTOPS', 'G28'] }
}
