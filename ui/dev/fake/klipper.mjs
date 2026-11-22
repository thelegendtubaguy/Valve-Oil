// Valve Oil's Klipper switch: the status service's klipper block and POST api/klipper (installer/system/vo_status.py),
// the vo_klipper object and VO_KLIPPER (installer/klipper/extras/klipper_switch.py), and the printer warning when
// QIDI's Klipper runs although Valve Oil's is chosen.
//
// Pick the state with `--klipper <state>`, or switch at runtime with `VO_KLIPPER_STATE NAME=<state>`:
//   off            QIDI's Klipper chosen and running, Valve Oil's installed (the default)
//   on             Valve Oil's Klipper chosen and running
//   fallback       Valve Oil's chosen, QIDI's running because Valve Oil's didn't become ready; trying again fails again
//   not-installed  Valve Oil's Klipper isn't installed
//   absent         a status service without the klipper block
// A switch restarts Klipper for 4 seconds, and is refused during a print.

const VERSION = 'v0.14.0-vo1'
const FALLBACK_REASON = "Valve Oil's Klipper didn't become ready within 120 s, twice"
const NAMES = { valve_oil: "Valve Oil's Klipper", qidi: "QIDI's Klipper" }
const STATES = ['off', 'on', 'fallback', 'not-installed', 'absent']

export function install(fake) {
  const k = { state: 'off', selected: 'qidi', running: 'qidi', reason: null, available: true, fails: false, restarting: false, absent: false }

  function apply(name) {
    if (!STATES.includes(name)) name = 'off'
    Object.assign(k, { state: name, selected: 'qidi', running: 'qidi', reason: null, available: true, fails: false, restarting: false, absent: name === 'absent' })
    if (name === 'on') Object.assign(k, { selected: 'valve_oil', running: 'valve_oil' })
    if (name === 'fallback') Object.assign(k, { selected: 'valve_oil', reason: FALLBACK_REASON, fails: true })
    if (name === 'not-installed') k.available = false
    publish()
  }

  const printing = () => ['printing', 'paused'].includes(fake.state.status.print_stats?.state)

  function blockers(use) {
    const out = []
    if (printing()) out.push({ code: 'printing', message: 'a print is running or paused' })
    if (use === 'valve_oil' && !k.available) out.push({ code: 'valve_oil_unavailable', message: "Valve Oil's Klipper isn't available: not installed" })
    return out
  }

  function block() {
    const to = k.selected === 'valve_oil' ? 'qidi' : 'valve_oil'
    const blocked = blockers(to)
    return {
      available: k.available,
      unavailable_reason: k.available ? null : 'not installed',
      selected: k.selected,
      running: k.restarting ? null : k.running,
      fallback_reason: k.selected === 'valve_oil' && k.running === 'qidi' ? k.reason : null,
      version: k.available ? VERSION : null,
      restart_error: null,
      switch: { to, allowed: blocked.length === 0, blocked_by: blocked },
    }
  }

  function klipperObject() {
    return { selected: k.selected, running: k.running, reason: k.selected !== k.running ? k.reason : null, available: k.available, version: k.running === 'valve_oil' ? VERSION : null }
  }

  function warnings(list) {
    const kept = list.filter((w) => w.id !== 'vo_klipper_fallback')
    if (k.selected !== 'valve_oil' || k.running !== 'qidi' || k.restarting) return kept
    return [
      ...kept,
      {
        id: 'vo_klipper_fallback',
        level: 'warning',
        title: "QIDI's Klipper is running instead of Valve Oil's.",
        message: `${k.reason}. Try Valve Oil's Klipper again, or go back to QIDI's, in Valve Oil's settings or with VO_KLIPPER USE=VALVE_OIL or VO_KLIPPER USE=QIDI.`,
      },
    ]
  }

  function publish() {
    fake.set('vo_klipper', klipperObject())
    fake.set('tltg_printer_warnings', { warnings: warnings(fake.state.status.tltg_printer_warnings?.warnings ?? []) })
  }

  /** The switch, as POST api/klipper makes it: the choice, then Klipper restarted a moment later. */
  function change(use) {
    k.selected = use
    k.restarting = true
    publish()
    setTimeout(() => {
      fake.restartKlipper(4)
      setTimeout(() => {
        k.restarting = false
        k.running = use === 'valve_oil' && k.fails ? 'qidi' : use
        k.reason = k.running !== use ? FALLBACK_REASON : null
        publish()
      }, 4000)
    }, 1000)
  }

  const send = (res, code, body) => {
    res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
    res.end(JSON.stringify(body))
    return true
  }

  fake.voStatus((body) => {
    if (!k.absent) body.klipper = block()
  })

  fake.http(async (req, res, path) => {
    if (path !== '/tltg-status/api/klipper' || k.absent) return false
    if (req.method !== 'POST') return send(res, 405, { error: 'Use POST.' })
    if (req.headers['x-valve-oil'] !== 'status') return send(res, 403, { error: 'Missing X-Valve-Oil header.' })
    let raw = ''
    for await (const chunk of req) raw += chunk
    let body
    try {
      body = raw ? JSON.parse(raw) : {}
    } catch {
      return send(res, 400, { error: 'Expected a JSON request.' })
    }
    if (!['valve_oil', 'qidi'].includes(body.use) || Object.keys(body).some((key) => !['use', 'source'].includes(key)))
      return send(res, 400, { error: 'Expected {"use": "valve_oil"} or {"use": "qidi"}, with an optional "source" of "ui" or "console".' })
    await new Promise((r) => setTimeout(r, 400))
    const blocked = blockers(body.use)
    if (blocked.length) return send(res, 409, { error: `Klipper can't be switched now: ${blocked.map((b) => b.message).join('; ')}.`, blocked_by: blocked })
    change(body.use)
    return send(res, 202, { klipper: block() })
  })

  fake.gcode((C, p) => {
    if (C === 'VO_KLIPPER_STATE') {
      apply((p.NAME ?? 'off').toLowerCase())
      fake.pushConsole(`// Klipper switch: ${k.state}`)
      return true
    }
    if (C !== 'VO_KLIPPER') return false
    if (p.USE == null) {
      const o = klipperObject()
      if (o.selected === o.running) fake.pushConsole(o.running === 'valve_oil' ? `Valve Oil's Klipper ${VERSION} is running. Go back to QIDI's Klipper with VO_KLIPPER USE=QIDI.` : "QIDI's Klipper is running. Valve Oil's Klipper is off; switch to it with VO_KLIPPER USE=VALVE_OIL.")
      else fake.pushConsole(`Valve Oil's Klipper is chosen, but QIDI's Klipper is running: ${o.reason}. Try again with VO_KLIPPER USE=VALVE_OIL, or stay on QIDI's with VO_KLIPPER USE=QIDI.`)
      return true
    }
    const use = { VALVE_OIL: 'valve_oil', QIDI: 'qidi' }[p.USE.toUpperCase()]
    if (!use) throw fake.rpcError('VO_KLIPPER USE= takes VALVE_OIL or QIDI')
    if (printing()) throw fake.rpcError("Klipper can't be switched during a print. Switch once the print ends or is cancelled.")
    fake.pushConsole(`Switching to ${NAMES[use]}...`)
    const blocked = blockers(use)
    if (blocked.length) fake.pushConsole(`!! Klipper wasn't switched: Klipper can't be switched now: ${blocked.map((b) => b.message).join('; ')}.`)
    else {
      fake.pushConsole(`${NAMES[use]} chosen. Klipper restarts now.`)
      change(use)
    }
    return true
  })

  // Each scenario writes the status afresh, its own warnings with it; the fallback warning is added back.
  fake.status((st) => {
    st.vo_klipper = klipperObject()
    st.tltg_printer_warnings = { warnings: warnings(st.tltg_printer_warnings?.warnings ?? []) }
  })

  fake.commands({
    VO_KLIPPER: 'Say which Klipper runs, or switch with USE=VALVE_OIL or USE=QIDI',
    VO_KLIPPER_STATE: `Fake: set the Klipper switch's state (NAME=${STATES.join('|')})`,
  })

  apply(fake.args.klipper ?? 'off')
}
