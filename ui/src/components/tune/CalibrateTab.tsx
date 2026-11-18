import { useMemo, useState } from 'react'
import { Activity, CircleCheck, CircleDot, Crosshair, Hand, House, RefreshCw, RotateCcw, RotateCw, Ruler, Scale, TriangleAlert, Waves } from 'lucide-react'
import { Card, Choices, Empty, Spinner, Stepper, confirm } from '../ui'
import { attempt } from '../toast'
import { client, runGcode, useObject, usePrinter } from '../../state/printer'
import { useStore } from '../../lib/store'
import { SaveConfigNotice } from './SaveConfigNotice'
import { reopenHelper } from './HelperDialogs'
import { calibrationLog } from './calibration-log'
import { lastProbeAccuracy, shaperResults, tiltScrews, type TiltScrew } from './calibrate'
import { useHasCommand, usePrinterFlags } from './hooks'

const run = (g: string) => attempt(() => runGcode(g))

export function CalibrateTab() {
  const has = useHasCommand()
  const settings = usePrinter((s) => s.settings)
  const hasZTilt = !!settings.z_tilt && has('Z_TILT_ADJUST')
  const hasScrews = !!settings.screws_tilt_adjust && has('SCREWS_TILT_CALCULATE')
  return (
    <>
      <SaveConfigNotice />
      <div className="grid items-start gap-3.5 lg:grid-cols-2 2xl:grid-cols-3">
        <div className="grid content-start gap-3.5">
          <HomeCard />
          {(hasZTilt || hasScrews) && <LevelingCard zTilt={hasZTilt} screws={hasScrews} />}
          <EndstopsCard />
        </div>
        <div className="grid content-start gap-3.5">
          <ProbeCard />
          {has('TLTG_TEST_PROBE') && <LoadCellCard />}
        </div>
        {has('SHAPER_CALIBRATE') && (
          <div className="grid content-start gap-3.5 lg:col-span-2 2xl:col-span-1">
            <InputShaperCard />
          </div>
        )}
      </div>
    </>
  )
}

/** Homing state, since most of these need the printer homed first. */
function HomeCard() {
  const { busy, homed } = usePrinterFlags()
  const axes = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const manual = !!useObject('manual_probe').is_active
  const screws = !!useObject('bed_screws').is_active
  return (
    <div className="card flex flex-wrap items-center gap-3">
      <span className={`pill ${homed ? 'tone-good' : 'tone-muted'}`}>
        {homed ? 'Homed' : axes ? `Homed ${axes.toUpperCase().split('').join(' ')}` : 'Not homed'}
      </span>
      <span className="min-w-0 flex-1 text-[12.5px] text-ink-3">{homed ? 'Ready for the tools below.' : 'Most of these tools need the printer homed.'}</span>
      {manual && (
        <button className="btn btn-sm btn-primary" onClick={() => reopenHelper('manual')}>
          Manual probe
        </button>
      )}
      {screws && (
        <button className="btn btn-sm btn-primary" onClick={() => reopenHelper('screws')}>
          Bed screws adjust
        </button>
      )}
      <button className={`btn btn-sm ${homed ? '' : 'btn-primary'}`} disabled={busy} onClick={() => run('G28')}>
        <House size={14} /> Home all
      </button>
    </div>
  )
}

function LevelingCard({ zTilt, screws }: { zTilt: boolean; screws: boolean }) {
  const has = useHasCommand()
  const { busy, homed } = usePrinterFlags()
  const zt = useObject('z_tilt')
  const sta = useObject('screws_tilt_adjust')
  const settings = usePrinter((s) => s.settings)
  const toolhead = useObject('toolhead')
  const list = tiltScrews(settings.screws_tilt_adjust, sta)
  const hasResults = list.some((s) => s.adjust != null)
  const corner = has('TLTG_CORNER_BED_SCREW_CHECK')
  const thread = settings.screws_tilt_adjust?.screw_thread as string | undefined
  return (
    <Card title="Bed leveling" icon={<Scale size={14} />}>
      {zTilt && (
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="section-label">Z tilt</span>
            <span className={`pill ${zt.applied ? 'tone-good' : 'tone-muted'}`}>
              {zt.applied ? 'Applied' : 'Not applied since homing'}
            </span>
            <span className="flex-1" />
            <button className="btn btn-sm" disabled={busy || !homed} title={homed ? undefined : 'Home first'} onClick={() => run('Z_TILT_ADJUST')}>
              Z tilt adjust
            </button>
          </div>
          <p className="m-0 text-[12.5px] text-ink-3">Probes both sides of the bed and turns the Z motors until the gantry is level with it.</p>
        </div>
      )}
      {zTilt && screws && <div className="sep my-3.5" />}
      {screws && (
        <div className="grid gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="section-label">Bed screws</span>
            {sta.max_deviation != null && <span className={`pill ${sta.error ? 'tone-bad' : 'tone-muted'}`}>Max deviation {Number(sta.max_deviation).toFixed(3)} mm</span>}
            <span className="flex-1" />
            {corner ? (
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => run('TLTG_CORNER_BED_SCREW_CHECK')}>
                Check bed screws
              </button>
            ) : (
              <button className="btn btn-sm btn-primary" disabled={busy || !homed} onClick={() => run('SCREWS_TILT_CALCULATE')}>
                Calculate
              </button>
            )}
          </div>
          <ScrewMap screws={list} min={toolhead.axis_minimum as number[] | undefined} max={toolhead.axis_maximum as number[] | undefined} />
          <p className="m-0 text-[12.5px] text-ink-3">
            {corner ? 'Check bed screws homes, runs Z tilt adjust, then probes at each screw. ' : ''}
            {hasResults ? '01:15 means one full turn and 15 minutes, as on a clock face. Turn each screw the way its arrow shows, then check again.' : 'Results show here after the check.'}
            {thread ? ` Screws: ${thread}.` : ''}
          </p>
          {corner && homed && (
            <button className="btn btn-sm btn-ghost justify-self-start" disabled={busy} onClick={() => run('SCREWS_TILT_CALCULATE')}>
              Calculate again without homing
            </button>
          )}
          {has('BED_SCREWS_ADJUST') && settings.bed_screws && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-inset px-3 py-2">
              <span className="min-w-0 flex-1 text-[12.5px] text-ink-2">Or level by hand with a sheet of paper at each screw.</span>
              <button className="btn btn-sm" disabled={busy || !homed} onClick={() => run('BED_SCREWS_ADJUST')}>
                Bed screws adjust
              </button>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/** The bed from above, front at the bottom, each screw with its turn. */
function ScrewMap({ screws, min, max }: { screws: TiltScrew[]; min?: number[]; max?: number[] }) {
  const xs = screws.map((s) => s.x)
  const ys = screws.map((s) => s.y)
  const minX = Math.max(0, min?.[0] ?? 0)
  const minY = Math.max(0, min?.[1] ?? 0)
  const maxX = max?.[0] ?? Math.max(...xs) + 30
  const maxY = max?.[1] ?? Math.max(...ys) + 30
  const w = maxX - minX || 1
  const h = maxY - minY || 1
  return (
    // The labels hang outside the bed's outline, so the outline sits inside a padded frame.
    <div className="mx-auto w-full max-w-[380px] px-12 pt-3 pb-16">
      <div className="relative w-full rounded-xl border border-line-strong bg-inset" style={{ aspectRatio: `${w} / ${h}` }}>
      <span className="absolute -bottom-6 left-1/2 -translate-x-1/2 text-[10.5px] font-semibold tracking-wider text-ink-3 uppercase">Front</span>
      {screws.map((s) => {
        const good = s.minutes != null && s.minutes < 6
        const tone = s.isBase ? 'tone-muted' : s.adjust == null ? 'tone-muted' : good ? 'tone-good' : 'tone-warn'
        const left = ((s.x - minX) / w) * 100
        const top = (1 - (s.y - minY) / h) * 100
        return (
          <div key={s.key} className="absolute grid -translate-x-1/2 -translate-y-1/2 justify-items-center gap-1 text-center" style={{ left: `${left}%`, top: `${top}%` }}>
            <span className={`pill ${tone} px-2.5!`} title={s.z != null ? `Z ${s.z.toFixed(4)}` : undefined}>
              {s.isBase ? (
                'Base'
              ) : s.adjust == null ? (
                <CircleDot size={13} />
              ) : (
                <>
                  {s.minutes === 0 ? <CircleCheck size={13} /> : s.sign === 'CW' ? <RotateCw size={13} /> : <RotateCcw size={13} />}
                  <span className="num">{s.adjust}</span>
                </>
              )}
            </span>
            <span className="rounded-md px-1 text-[11px] leading-tight font-semibold whitespace-nowrap text-ink-2" style={{ background: "color-mix(in srgb, var(--surface) 85%, transparent)" }}>
              {s.name}
              {s.relativeZ != null && !s.isBase && <span className="num block font-normal text-ink-3">{`${s.relativeZ < 0 ? '−' : '+'}${Math.abs(s.relativeZ).toFixed(3)} mm`}</span>}
              {s.sign && !s.isBase && s.minutes !== 0 && <span className="block font-normal text-ink-3">{s.sign === 'CW' ? 'clockwise' : 'counterclockwise'}</span>}
            </span>
          </div>
        )
      })}
      </div>
    </div>
  )
}

function EndstopsCard() {
  const last = useObject('query_endstops').last_query as Record<string, string | number> | undefined
  const [queried, setQueried] = useState<Record<string, string> | null>(null)
  const [loading, setLoading] = useState(false)
  const states = queried ?? (last && Object.keys(last).length ? Object.fromEntries(Object.entries(last).map(([k, v]) => [k, v === 1 || v === 'TRIGGERED' ? 'TRIGGERED' : 'open'])) : null)
  const refresh = async () => {
    setLoading(true)
    const r = await attempt(() => client.call<Record<string, string>>('printer.query_endstops.status'))
    setLoading(false)
    if (r) setQueried(r)
  }
  return (
    <Card
      title="Endstops"
      icon={<Crosshair size={14} />}
      aside={
        <button className="iconbtn" onClick={refresh} disabled={loading} title="Refresh" aria-label="Refresh endstops">
          {loading ? <Spinner size={15} /> : <RefreshCw size={15} />}
        </button>
      }
    >
      {states ? (
        <div className="grid gap-1.5">
          {Object.keys(states)
            .sort()
            .map((k) => {
              const on = states[k].toUpperCase() === 'TRIGGERED'
              return (
                <div key={k} className="row-k items-center!">
                  <span className="k">Endstop {k.toUpperCase()}</span>
                  <span className={`pill ${on ? 'tone-warn' : 'tone-muted'} justify-self-end`}>
                    {on ? 'TRIGGERED' : 'OPEN'}
                  </span>
                </div>
              )
            })}
        </div>
      ) : (
        <Empty>Use the refresh button to update endstop status.</Empty>
      )}
      <p className="mt-2.5 mb-0 text-[12.5px] text-ink-3">The nozzle is the Z probe. QUERY_PROBE can&apos;t check its load cell; use the load-cell check instead.</p>
    </Card>
  )
}

function ProbeCard() {
  const has = useHasCommand()
  const { busy, homed } = usePrinterFlags()
  const log = useStore(calibrationLog, (l) => l)
  const acc = useMemo(() => lastProbeAccuracy(log), [log])
  const [samples, setSamples] = useState(20)
  const center = has('TLTG_PROBE_ACCURACY_CENTER')
  const manual = !!useObject('manual_probe').is_active
  return (
    <Card title="Probe" icon={<Ruler size={14} />}>
      <SectionTitle>Probe accuracy</SectionTitle>
      <div className="mt-2 flex flex-wrap items-end gap-2.5">
        <label className="grid w-[150px] gap-1">
          <span className="lbl">Samples</span>
          <Stepper value={samples} onChange={setSamples} min={2} max={100} step={5} label="Samples" />
        </label>
        <span className="flex-1" />
        {center ? (
          <button className="btn btn-primary" disabled={busy} onClick={() => run(`TLTG_PROBE_ACCURACY_CENTER SAMPLES=${samples}`)}>
            Run at bed center
          </button>
        ) : (
          <button className="btn btn-primary" disabled={busy || !homed} onClick={() => run(`PROBE_ACCURACY SAMPLES=${samples}`)}>
            Run here
          </button>
        )}
      </div>
      <p className="mt-2 mb-0 text-[12.5px] text-ink-3">
        {center ? 'Homes, moves to the middle of the bed at Z10, and probes the same spot again and again. ' : 'Probes the same spot again and again. '}
        The range and deviation show how repeatable the probe is.
      </p>
      {acc ? (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Tile label="Range" value={acc.range.toFixed(4)} unit="mm" hot />
            <Tile label="Std. deviation" value={acc.deviation.toFixed(4)} unit="mm" />
            <Tile label="Average" value={acc.average.toFixed(4)} unit="mm" />
            <Tile label="Median" value={acc.median.toFixed(4)} unit="mm" />
            <Tile label="Lowest" value={acc.minimum.toFixed(4)} unit="mm" />
            <Tile label="Highest" value={acc.maximum.toFixed(4)} unit="mm" />
          </div>
          <div className="mt-1.5 text-[12px] text-ink-3">
            {[acc.samples != null ? `${acc.samples} samples` : null, acc.at, new Date(acc.time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })].filter(Boolean).join(' · ')}
          </div>
        </>
      ) : null}
      {has('PROBE_CALIBRATE') && (
        <>
          <div className="sep my-3.5" />
          <SectionTitle>Probe Z offset</SectionTitle>
          <p className="mt-1 mb-2.5 text-[12.5px] text-ink-3">PROBE_CALIBRATE probes the bed, then you lower the nozzle onto a sheet of paper. The difference becomes the probe&apos;s Z offset, saved with Save config.</p>
          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={busy || !homed || manual} title={homed ? undefined : 'Home first'} onClick={() => run('PROBE_CALIBRATE')}>
              Probe calibrate
            </button>
            {has('Z_ENDSTOP_CALIBRATE') && (
              <button className="btn" disabled={busy || !homed || manual} onClick={() => run('Z_ENDSTOP_CALIBRATE')}>
                Z endstop calibrate
              </button>
            )}
            {manual && (
              <button className="btn btn-primary" onClick={() => reopenHelper('manual')}>
                Manual probe
              </button>
            )}
          </div>
        </>
      )}
    </Card>
  )
}

const LOAD_CELL_RESULTS: Record<string, { text: string; tone: string }> = {
  RESPONSE_DETECTED: { text: 'Your load cell readings look correct.', tone: 'tone-good' },
  NO_RELIABLE_RESPONSE: { text: 'Your load cell did not respond as expected.', tone: 'tone-bad' },
  INCONCLUSIVE: { text: 'There was not enough difference between pressing and not pressing to tell, or the test stopped early.', tone: 'tone-warn' },
}

/** TLTG_TEST_PROBE: a guided, motion-free check that the nozzle load cell responds to light pressure. */
function LoadCellCard() {
  const t = useObject('tltg_probe_test')
  const { busy } = usePrinterFlags()
  const last = usePrinter((s) => {
    for (let i = s.console.length - 1; i >= 0; i--) if (s.console[i].kind === 'response') return s.console[i].text.replace(/^\/\/\s?/, '')
    return ''
  })
  const active = !!t.active
  const result = LOAD_CELL_RESULTS[t.result as string]
  const metrics = t.metrics as Record<string, number> | undefined
  const start = async () => {
    if (
      await confirm({
        title: 'Nozzle load-cell check',
        body: (
          <div className="grid gap-2">
            <p className="m-0">The printer won&apos;t move, heat, or change calibration. It takes about 40 seconds.</p>
            <ul className="m-0 grid gap-1 pl-4">
              <li>The printer must be idle with all heaters off, and the nozzle and bed below 40 °C.</li>
              <li>Make sure you can reach the nozzle and it&apos;s clear of the bed. Don&apos;t home to prepare.</li>
              <li>Follow the prompts here: press lightly upward on the nozzle only when told to. Never force it.</li>
            </ul>
          </div>
        ),
        confirm: 'Start the check',
      })
    )
      run('TLTG_TEST_PROBE CONFIRM=1')
  }
  return (
    <Card title="Nozzle load-cell check" icon={<Hand size={14} />}>
      {active ? (
        <div className="grid justify-items-center gap-2 rounded-xl bg-accent-tint px-4 py-5 text-center">
          <Spinner size={20} />
          <div className="text-[22px] font-bold tracking-tight text-accent-ink">{last || 'Measuring...'}</div>
          <div className="text-[12.5px] text-ink-2">Press lightly upward only at PRESS, and let go when told to stop.</div>
        </div>
      ) : (
        <>
          <p className="mt-0 mb-3 text-[12.5px] text-ink-3">Checks that the nozzle load cell, which is both the probe and the Z endstop, responds to light pressure. No motion.</p>
          {result && (
            <div className={`${result.tone} mb-3 rounded-lg px-3 py-2.5`}>
              <div className="font-semibold">{String(t.result)}</div>
              <div className="text-ink">{result.text}</div>
              {metrics && metrics.change != null && (
                <div className="num mt-1 text-[12px] text-ink-2">
                  Signal change {Math.round(metrics.change)} counts (needed {Math.round(metrics.required_change)}); baseline noise {Math.round(metrics.noise)}; release error {Math.round(metrics.release_error)}.
                </div>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-primary" disabled={busy} onClick={start}>
              Start the check
            </button>
          </div>
        </>
      )}
      <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]">
        <TriangleAlert size={14} className="mt-0.5 flex-none" />
        <span className="text-ink">
          A detected response does not verify the homing trigger. For any other result, don&apos;t press harder, and don&apos;t home or probe until the cause is understood.
        </span>
      </div>
    </Card>
  )
}

function InputShaperCard() {
  const settings = usePrinter((s) => s.settings)
  const cf = useObject('configfile')
  const ms = useObject('tltg_macro_status')
  const { busy, homed } = usePrinterFlags()
  const log = useStore(calibrationLog, (l) => l)
  const results = useMemo(() => shaperResults(log), [log])
  const [axis, setAxis] = useState<'x' | 'y' | 'both'>('both')
  const cfg = (settings.input_shaper ?? {}) as Record<string, string | number>
  const pending = ((cf.save_config_pending_items as Record<string, Record<string, string>> | undefined) ?? {}).input_shaper
  const shaper = ms.input_shaper as { loaded?: boolean; unshaped_axes?: string; saved?: boolean } | undefined
  const unshaped = shaper?.unshaped_axes ?? ''
  const calibrate = async () => {
    const axes = axis === 'both' ? 'X and Y' : axis.toUpperCase()
    if (
      await confirm({
        title: `Calibrate input shaper, ${axes}`,
        body: (
          <div className="grid gap-2">
            <p className="m-0 font-semibold text-ink">Empty the bed first: take off any print and anything loose.</p>
            <p className="m-0">
              {homed ? '' : 'The printer homes first. '}The toolhead shakes hard at the test point for about two minutes per axis, moving up to about 20 mm either side. Then Klipper picks a shaper for each axis and applies it until it restarts; Save config keeps it.
            </p>
          </div>
        ),
        confirm: 'Start calibration',
      })
    )
      run(`${homed ? '' : 'G28\n'}SHAPER_CALIBRATE${axis === 'both' ? '' : ` AXIS=${axis.toUpperCase()}`}`)
  }
  const axisRow = (a: 'x' | 'y') => {
    const type = (cfg[`shaper_type_${a}`] ?? cfg.shaper_type) as string | undefined
    const freq = Number(cfg[`shaper_freq_${a}`] ?? 0)
    const damping = cfg[`damping_ratio_${a}`]
    const p = pending && (pending[`shaper_type_${a}`] || pending[`shaper_freq_${a}`])
    return (
      <div key={a} className="tile">
        <span className="section-label">{a.toUpperCase()} axis</span>
        {freq > 0 ? (
          <span className="v num">
            {type} <small>{freq.toFixed(1)} Hz</small>
          </span>
        ) : (
          <span className="v text-ink-3">Off</span>
        )}
        {damping != null && freq > 0 && <span className="text-[12px] text-ink-3">damping {Number(damping).toFixed(2)}</span>}
        {p && (
          <span className="mt-1 text-[12px] font-semibold text-accent-ink">
            New: {pending[`shaper_type_${a}`] ?? type} {pending[`shaper_freq_${a}`] ? `${Number(pending[`shaper_freq_${a}`]).toFixed(1)} Hz` : ''}, waiting for Save config
          </span>
        )}
        {unshaped.includes(a) && <span className="mt-1 text-[12px] font-semibold text-bad">Input shaping is off for this axis now</span>}
      </div>
    )
  }
  const shown = (['x', 'y'] as const).filter((a) => results[a])
  return (
    <Card title="Input shaper" icon={<Waves size={14} />}>
      <div className="grid grid-cols-2 gap-2">{(['x', 'y'] as const).map(axisRow)}</div>
      <div className="mt-1.5 text-[12px] text-ink-3">From printer.cfg when Klipper started.</div>
      {shaper && shaper.saved === false && (
        <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]">
          <TriangleAlert size={14} className="mt-0.5 flex-none" />
          <span className="text-ink">No input shaper calibration is saved. Until there is, homing keeps acceleration at QIDI&apos;s 10000 mm/s² for leveling and mesh moves.</span>
        </div>
      )}
      <div className="sep my-3.5" />
      <div className="flex flex-wrap items-center gap-2.5">
        <Choices
          value={axis}
          onChange={setAxis}
          disabled={busy}
          options={[
            { value: 'both', label: 'Both axes' },
            { value: 'x', label: 'X' },
            { value: 'y', label: 'Y' },
          ]}
        />
        <span className="flex-1" />
        <button className="btn btn-primary" disabled={busy} onClick={calibrate}>
          <Activity size={15} /> Calibrate
        </button>
      </div>
      <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]">
        <TriangleAlert size={14} className="mt-0.5 flex-none" />
        <span className="text-ink">Empty the bed before calibrating. The toolhead shakes hard during the test.</span>
      </div>
      {shown.length > 0 && (
        <div className="mt-3.5 grid gap-3">
          <SectionTitle>Last calibration</SectionTitle>
          {shown.map((a) => {
            const r = results[a]
            return (
              <div key={a} className="grid gap-1.5">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-semibold">{a.toUpperCase()} axis</span>
                  {r.recommended ? (
                    <span className="pill tone-accent">
                      Recommended {r.recommended.type} at {r.recommended.freq.toFixed(1)} Hz
                    </span>
                  ) : (
                    <span className="pill tone-muted">
                      <Spinner size={11} /> Calculating
                    </span>
                  )}
                  <span className="text-[12px] text-ink-3">{new Date(r.time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>
                </div>
                {r.fitted.length > 0 && (
                  <div className="-mx-2 overflow-x-auto">
                    <table className="table text-[12.5px]">
                      <thead>
                        <tr>
                          <th>Shaper</th>
                          <th className="text-right">Frequency</th>
                          <th className="text-right">Vibrations</th>
                          <th className="text-right">Smoothing</th>
                          <th className="text-right">Max accel</th>
                        </tr>
                      </thead>
                      <tbody>
                        {r.fitted.map((f) => {
                          const best = r.recommended?.type === f.type
                          return (
                            <tr key={f.type} className={best ? 'font-semibold text-accent-ink' : ''}>
                              <td>{f.type}</td>
                              <td className="num text-right">{f.freq.toFixed(1)} Hz</td>
                              <td className="num text-right">{f.vibrations.toFixed(1)}%</td>
                              <td className="num text-right">{f.smoothing.toFixed(3)}</td>
                              <td className="num text-right">{f.maxAccel != null ? `${f.maxAccel.toLocaleString()} mm/s²` : '—'}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

function SectionTitle({ children }: { children: string }) {
  return <div className="section-label">{children}</div>
}

function Tile({ label, value, unit, hot }: { label: string; value: string; unit: string; hot?: boolean }) {
  return (
    <div className={`tile ${hot ? 'hot' : ''}`}>
      <span className="section-label">{label}</span>
      <span className="v num">
        {value} <small>{unit}</small>
      </span>
    </div>
  )
}
