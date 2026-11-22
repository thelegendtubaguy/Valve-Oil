// System → Diagnostics' fault check: Valve Oil's status service at /tltg-status/api/diagnostics
// (installer/system/vo_status.py Diagnostics, running installer/runtime/diagnostics.py). Pick what the check finds with
// `--diagnostics <scenario>`, or switch at runtime with `VO_DIAGNOSTICS NAME=<scenario>`:
//
//   found     the last check found a step clock the release installed now fixes, and a QIDI error code (the default)
//   clean     the last check found nothing
//   partial   the last check found the same, but read only the newest part of a large log
//   failed    the last check failed, and the next one fails too
//   idle      no check has run yet; the next one finds what `found` does
//   absent    an older Valve Oil without the check (404)
//
// A check started with POST runs for about 3 seconds.

const SCENARIOS = ['found', 'clean', 'partial', 'failed', 'idle', 'absent']
const RUN_MS = 3000
const DOCS = 'https://github.com/thelegendtubaguy/valve-oil/blob/main/docs/troubleshooting.md'
const INSTALLED = '26.11.22.2'

export function install(fake) {
  let scenario = 'found'
  let check = null
  let generation = 0

  const iso = (date = new Date()) => date.toISOString().replace(/\.\d+Z$/, 'Z')
  // klippy.log's times: the printer's local time, to the second, before the check that's being made up started.
  let checkedAt = Date.now()
  const logTime = (secondsAgo) => {
    const d = new Date(checkedAt - secondsAgo * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }

  const findings = () => {
    const clock = [logTime(3 * 3600 + 120), logTime(3 * 3600 + 118), logTime(40 * 60)]
    const error = logTime(38 * 60)
    return [
      {
        id: 'box_step_clock_offset',
        title: "A Box slot motor's step clock is off Klipper's",
        severity: 'fixed',
        meaning: "The Box kept a slot motor's step clock seconds away from the one Klipper uses, after its auto-feed was re-armed during a feed. An unload then runs that slot's motor late, or in bursts, and the toolhead sensor never clears.",
        action: "Nothing, on Valve Oil 26.11.22.2 or later, which resets the slot's step clock before each unload. On an earlier release, update Valve Oil; a Klipper restart also clears it until the next print start.",
        fixed_in: '26.11.22.2',
        docs: `${DOCS}#box-slot-step-clock`,
        count: 2,
        first: clock[0],
        last: clock[2],
        details: ["Box 1 slot stepper oid 11: 23.7 s behind Klipper's", "Box 2 slot stepper oid 9: 51.2 s behind Klipper's"],
        evidence: [
          `${clock[0]} - mcu 'mcu_box1': got {'oid': 11, 'clk': 4035537278, '#name': 'last_step_clk', '#sent_time': 18423.511, '#receive_time': 18423.514}`,
          `${clock[1]} - mcu 'mcu_box1': got {'oid': 11, 'next_s': 3328437278, 'now': 4035601944, '#name': 'STEP-set_last_step', '#sent_time': 18425.102, '#receive_time': 18425.106}`,
          `${clock[2]} - mcu 'mcu_box2': got {'oid': 9, 'clk': 1192745503, '#name': 'last_step_clk', '#sent_time': 28870.044, '#receive_time': 28870.047}`,
        ],
      },
      {
        id: 'qidi_error',
        title: 'QIDI error codes',
        severity: 'info',
        meaning: "QIDI's code reported these errors. The lines before each show what led up to it.",
        action: "Look up the code in the table of QIDI's error codes; report it with the support bundle if it recurs.",
        fixed_in: null,
        docs: `${DOCS}#qidi-error-codes`,
        count: 1,
        first: error,
        last: error,
        details: ['QDE_004_008: Extruder unloading failure.'],
        evidence: [
          `${logTime(38 * 60 + 25)} - Status changed: printing -> paused`,
          `${logTime(38 * 60 + 2)} - box_flow_recovery: Box unload of slot2 stopped: the toolhead filament sensor still detects filament`,
          `${error} - Code:QDE_004_008; Message:Extruder unloading failure.`,
        ],
      },
    ]
  }

  const result = (since, kind) => {
    const partial = kind === 'partial'
    const span = { start: 5 * 3600, day: 86400, all: 9 * 86400 }[since] ?? 9 * 86400
    const reached = partial ? Math.min(span, 2 * 86400) : span
    const size = partial ? 96 * 1024 * 1024 : 31 * 1024 * 1024
    const read = partial ? 64 * 1024 * 1024 : size
    return {
      schema: 1,
      checked: {
        since,
        files: [{ name: 'klippy.log', size, read_from: size - read, bytes_read: read }],
        from: logTime(reached),
        to: logTime(5),
        complete: !partial,
        stopped: partial && since === 'all' ? 'time' : null,
        elapsed: partial ? 60.02 : 8.41,
      },
      installed: INSTALLED,
      findings: kind === 'clean' ? [] : findings(),
    }
  }

  // installer/runtime/diagnostics.py report(), with vo_status.py's printer details first.
  const report = (r) => {
    const out = ['Printer details:', `Valve Oil: ${INSTALLED}`, 'mcu: 01.01.07.01-tltg.2 (mcu.bin)', 'USB Boxes in box.cfg: 2', 'Kernel: 5.10.160', 'Auto-updates: on', '', 'Valve Oil diagnostics', '']
    const c = r.checked
    out.push(`Checked: ${c.files.map((f) => `${f.name} (${Math.floor(f.bytes_read / 1024)} KB of ${Math.floor(f.size / 1024)} KB)`).join(', ') || 'no logs'}`)
    out.push(`From ${c.from ?? 'unknown'} to ${c.to ?? 'unknown'}${c.complete ? '' : ', not the whole period'}${c.stopped === 'time' ? ' (stopped at the time limit)' : ''}`)
    out.push('')
    if (!r.findings.length) out.push('No known fault signatures found.')
    for (const f of r.findings) {
      out.push(`== ${f.title} [${f.severity}] x${f.count}, ${f.first ?? '?'} to ${f.last ?? '?'}`, f.meaning, `What to do: ${f.action}`, `Docs: ${f.docs}`)
      for (const d of f.details) out.push(`  - ${d}`)
      if (f.evidence.length) out.push('  Log lines:', ...f.evidence.map((l) => `    ${l}`))
      out.push('')
    }
    return `${out.join('\n').trimEnd()}\n`
  }

  const finish = (since, kind, started) => {
    const base = { since, started_at: iso(started), finished_at: iso(new Date(started.getTime() + RUN_MS)) }
    checkedAt = started.getTime()
    if (kind === 'failed') return { ...base, state: 'failed', error: 'The check could not be run: klippy.log: Permission denied', result: null, report: null }
    const r = result(since, kind)
    return { ...base, state: 'done', error: null, result: r, report: report(r) }
  }

  const idle = { state: 'idle', since: null, started_at: null, finished_at: null, error: null, result: null, report: null }

  const apply = (name) => {
    if (!SCENARIOS.includes(name)) throw fake.rpcError(`Unknown diagnostics scenario ${name}: use ${SCENARIOS.join(', ')}`)
    generation++
    scenario = name
    check = ['idle', 'absent'].includes(name) ? null : finish('all', name, new Date(Date.now() - 2 * 3600 * 1000))
  }

  const send = (res, code, body) => res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(body))

  fake.http(async (req, res, path) => {
    if (path !== '/tltg-status/api/diagnostics') return false
    // The core answers for the status service while it's away.
    if (fake.statusServiceDown) return false
    if (scenario === 'absent') return send(res, 404, { error: 'Not found.' })
    if (req.method === 'GET') return send(res, 200, check ?? idle)
    if (req.method !== 'POST') return send(res, 405, { error: 'Use GET or POST.' })
    if (req.headers['x-valve-oil'] !== 'status') return send(res, 403, { error: 'Missing X-Valve-Oil header.' })
    let raw = ''
    for await (const chunk of req) raw += chunk
    let body
    try {
      body = raw ? JSON.parse(raw) : {}
    } catch {
      return send(res, 400, { error: 'Expected a JSON request.' })
    }
    const since = body?.since ?? 'all'
    if (typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some((k) => k !== 'since') || !['all', 'day', 'start'].includes(since))
      return send(res, 400, { error: 'Expected {"since": "all"}, "day", or "start".' })
    if (check?.state === 'running') return send(res, 409, { error: 'A check is already running.' })
    const started = new Date()
    check = { state: 'running', since, started_at: iso(started), finished_at: null, error: null, result: null, report: null }
    const mine = generation
    const kind = scenario === 'idle' ? 'found' : scenario
    setTimeout(() => {
      if (generation === mine) check = finish(since, kind, started)
    }, RUN_MS)
    return send(res, 202, check)
  })

  fake.gcode((C, p) => {
    if (C !== 'VO_DIAGNOSTICS') return false
    apply((p.NAME ?? 'found').toLowerCase())
    fake.pushConsole(`// Diagnostics: ${scenario}`)
    return true
  })
  fake.commands({ VO_DIAGNOSTICS: `Fake: switch the Diagnostics check to a scenario (NAME=${SCENARIOS.join('|')})` })

  apply(fake.args.diagnostics ?? 'found')
}
