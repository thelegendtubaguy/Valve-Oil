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
import { locale, t, type MessageKey } from '../../i18n'

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
          {has('VO_TEST_PROBE') && <LoadCellCard />}
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
        {homed ? t('tune.calibrate.homed') : axes ? t('tune.calibrate.homed_axes', { axes: axes.toUpperCase().split('').join(' ') }) : t('tune.calibrate.not_homed')}
      </span>
      <span className="min-w-0 flex-1 text-[12.5px] text-ink-3">{homed ? t('tune.calibrate.ready') : t('tune.calibrate.need_home')}</span>
      {manual && (
        <button className="btn btn-sm btn-primary" onClick={() => reopenHelper('manual')}>
          {t('tune.shared.manual_probe')}
        </button>
      )}
      {screws && (
        <button className="btn btn-sm btn-primary" onClick={() => reopenHelper('screws')}>
          {t('tune.shared.bed_screws_adjust')}
        </button>
      )}
      <button className={`btn btn-sm ${homed ? '' : 'btn-primary'}`} disabled={busy} onClick={() => run('G28')}>
        <House size={14} /> {t('tune.shared.home_all')}
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
  const corner = has('VO_CORNER_BED_SCREW_CHECK')
  const thread = settings.screws_tilt_adjust?.screw_thread as string | undefined
  return (
    <Card title={t('tune.calibrate.leveling')} icon={<Scale size={14} />}>
      {zTilt && (
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="section-label">{t('tune.calibrate.z_tilt')}</span>
            <span className={`pill ${zt.applied ? 'tone-good' : 'tone-muted'}`}>
              {zt.applied ? t('tune.calibrate.applied') : t('tune.calibrate.not_applied')}
            </span>
            <span className="flex-1" />
            <button className="btn btn-sm" disabled={busy || !homed} title={homed ? undefined : t('tune.shared.home_first')} onClick={() => run('Z_TILT_ADJUST')}>
              {t('tune.calibrate.z_tilt_adjust')}
            </button>
          </div>
          <p className="m-0 text-[12.5px] text-ink-3">{t('tune.calibrate.z_tilt_hint')}</p>
        </div>
      )}
      {zTilt && screws && <div className="sep my-3.5" />}
      {screws && (
        <div className="grid gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="section-label">{t('tune.calibrate.bed_screws')}</span>
            {sta.max_deviation != null && <span className={`pill ${sta.error ? 'tone-bad' : 'tone-muted'}`}>{t('tune.calibrate.max_deviation', { value: Number(sta.max_deviation).toFixed(3) })}</span>}
            <span className="flex-1" />
            {corner ? (
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => run('VO_CORNER_BED_SCREW_CHECK')}>
                {t('tune.calibrate.check_screws')}
              </button>
            ) : (
              <button className="btn btn-sm btn-primary" disabled={busy || !homed} onClick={() => run('SCREWS_TILT_CALCULATE')}>
                {t('tune.calibrate.calculate')}
              </button>
            )}
          </div>
          <ScrewMap screws={list} min={toolhead.axis_minimum as number[] | undefined} max={toolhead.axis_maximum as number[] | undefined} />
          <p className="m-0 text-[12.5px] text-ink-3">
            {[corner ? t('tune.calibrate.corner_hint') : '', hasResults ? t('tune.calibrate.results_hint') : t('tune.calibrate.no_results'), thread ? t('tune.calibrate.screw_thread', { thread }) : '']
              .filter(Boolean)
              .join(' ')}
          </p>
          {corner && homed && (
            <button className="btn btn-sm btn-ghost justify-self-start" disabled={busy} onClick={() => run('SCREWS_TILT_CALCULATE')}>
              {t('tune.calibrate.calculate_again')}
            </button>
          )}
          {has('BED_SCREWS_ADJUST') && settings.bed_screws && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-inset px-3 py-2">
              <span className="min-w-0 flex-1 text-[12.5px] text-ink-2">{t('tune.calibrate.by_hand')}</span>
              <button className="btn btn-sm" disabled={busy || !homed} onClick={() => run('BED_SCREWS_ADJUST')}>
                {t('tune.shared.bed_screws_adjust')}
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
      <span className="absolute -bottom-6 left-1/2 -translate-x-1/2 text-[10.5px] font-semibold tracking-wider text-ink-3 uppercase">{t('tune.calibrate.front')}</span>
      {screws.map((s) => {
        const good = s.minutes != null && s.minutes < 6
        const tone = s.isBase ? 'tone-muted' : s.adjust == null ? 'tone-muted' : good ? 'tone-good' : 'tone-warn'
        const left = ((s.x - minX) / w) * 100
        const top = (1 - (s.y - minY) / h) * 100
        return (
          <div key={s.key} className="absolute grid -translate-x-1/2 -translate-y-1/2 justify-items-center gap-1 text-center" style={{ left: `${left}%`, top: `${top}%` }}>
            <span className={`pill ${tone} px-2.5!`} title={s.z != null ? `Z ${s.z.toFixed(4)}` : undefined}>
              {s.isBase ? (
                t('tune.calibrate.base')
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
              {s.sign && !s.isBase && s.minutes !== 0 && <span className="block font-normal text-ink-3">{s.sign === 'CW' ? t('tune.calibrate.clockwise') : t('tune.calibrate.counterclockwise')}</span>}
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
      title={t('tune.calibrate.endstops')}
      icon={<Crosshair size={14} />}
      aside={
        <button className="iconbtn" onClick={refresh} disabled={loading} title={t('tune.calibrate.refresh')} aria-label={t('tune.calibrate.refresh_endstops')}>
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
                  <span className="k">{t('tune.calibrate.endstop', { name: k.toUpperCase() })}</span>
                  <span className={`pill ${on ? 'tone-warn' : 'tone-muted'} justify-self-end`}>
                    {on ? t('tune.calibrate.triggered') : t('tune.calibrate.open')}
                  </span>
                </div>
              )
            })}
        </div>
      ) : (
        <Empty>{t('tune.calibrate.endstops_empty')}</Empty>
      )}
      <p className="mt-2.5 mb-0 text-[12.5px] text-ink-3">{t('tune.calibrate.endstops_note')}</p>
    </Card>
  )
}

function ProbeCard() {
  const has = useHasCommand()
  const { busy, homed } = usePrinterFlags()
  const log = useStore(calibrationLog, (l) => l)
  const acc = useMemo(() => lastProbeAccuracy(log), [log])
  const [samples, setSamples] = useState(20)
  const center = has('VO_PROBE_ACCURACY_CENTER')
  const manual = !!useObject('manual_probe').is_active
  return (
    <Card title={t('tune.calibrate.probe')} icon={<Ruler size={14} />}>
      <SectionTitle>{t('tune.calibrate.probe_accuracy')}</SectionTitle>
      <div className="mt-2 flex flex-wrap items-end gap-2.5">
        <label className="grid w-[150px] gap-1">
          <span className="lbl">{t('tune.calibrate.samples')}</span>
          <Stepper value={samples} onChange={setSamples} min={2} max={100} step={5} label={t('tune.calibrate.samples')} />
        </label>
        <span className="flex-1" />
        {center ? (
          <button className="btn btn-primary" disabled={busy} onClick={() => run(`VO_PROBE_ACCURACY_CENTER SAMPLES=${samples}`)}>
            {t('tune.calibrate.run_center')}
          </button>
        ) : (
          <button className="btn btn-primary" disabled={busy || !homed} onClick={() => run(`PROBE_ACCURACY SAMPLES=${samples}`)}>
            {t('tune.calibrate.run_here')}
          </button>
        )}
      </div>
      <p className="mt-2 mb-0 text-[12.5px] text-ink-3">
        {center ? t('tune.calibrate.accuracy_hint_center') : t('tune.calibrate.accuracy_hint_here')}
      </p>
      {acc ? (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Tile label={t('tune.shared.range')} value={acc.range.toFixed(4)} unit="mm" hot />
            <Tile label={t('tune.shared.deviation')} value={acc.deviation.toFixed(4)} unit="mm" />
            <Tile label={t('tune.shared.average')} value={acc.average.toFixed(4)} unit="mm" />
            <Tile label={t('tune.shared.median')} value={acc.median.toFixed(4)} unit="mm" />
            <Tile label={t('tune.shared.lowest')} value={acc.minimum.toFixed(4)} unit="mm" />
            <Tile label={t('tune.shared.highest')} value={acc.maximum.toFixed(4)} unit="mm" />
          </div>
          <div className="mt-1.5 text-[12px] text-ink-3">
            {[acc.samples != null ? t('tune.calibrate.samples_count', { count: acc.samples }) : null, acc.at, new Date(acc.time).toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' })].filter(Boolean).join(' · ')}
          </div>
        </>
      ) : null}
      {has('PROBE_CALIBRATE') && (
        <>
          <div className="sep my-3.5" />
          <SectionTitle>{t('tune.calibrate.z_offset')}</SectionTitle>
          <p className="mt-1 mb-2.5 text-[12.5px] text-ink-3">{t('tune.calibrate.z_offset_hint')}</p>
          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={busy || !homed || manual} title={homed ? undefined : t('tune.shared.home_first')} onClick={() => run('PROBE_CALIBRATE')}>
              {t('tune.calibrate.probe_calibrate')}
            </button>
            {has('Z_ENDSTOP_CALIBRATE') && (
              <button className="btn" disabled={busy || !homed || manual} onClick={() => run('Z_ENDSTOP_CALIBRATE')}>
                {t('tune.calibrate.z_endstop_calibrate')}
              </button>
            )}
            {manual && (
              <button className="btn btn-primary" onClick={() => reopenHelper('manual')}>
                {t('tune.shared.manual_probe')}
              </button>
            )}
          </div>
        </>
      )}
    </Card>
  )
}

const LOAD_CELL_RESULTS: Record<string, { key: MessageKey; tone: string }> = {
  RESPONSE_DETECTED: { key: 'tune.load_cell.result_ok', tone: 'tone-good' },
  NO_RELIABLE_RESPONSE: { key: 'tune.load_cell.result_bad', tone: 'tone-bad' },
  INCONCLUSIVE: { key: 'tune.load_cell.result_unclear', tone: 'tone-warn' },
}

/** VO_TEST_PROBE: a guided, motion-free check that the nozzle load cell responds to light pressure. */
function LoadCellCard() {
  const test = useObject('tltg_probe_test')
  const { busy } = usePrinterFlags()
  const last = usePrinter((s) => {
    for (let i = s.console.length - 1; i >= 0; i--) if (s.console[i].kind === 'response') return s.console[i].text.replace(/^\/\/\s?/, '')
    return ''
  })
  const active = !!test.active
  const result = LOAD_CELL_RESULTS[test.result as string]
  const metrics = test.metrics as Record<string, number> | undefined
  const start = async () => {
    if (
      await confirm({
        title: t('tune.load_cell.title'),
        body: (
          <div className="grid gap-2">
            <p className="m-0">{t('tune.load_cell.intro')}</p>
            <ul className="m-0 grid gap-1 pl-4">
              <li>{t('tune.load_cell.rule_idle')}</li>
              <li>{t('tune.load_cell.rule_reach')}</li>
              <li>{t('tune.load_cell.rule_prompts')}</li>
            </ul>
          </div>
        ),
        confirm: t('tune.load_cell.start'),
      })
    )
      run('VO_TEST_PROBE CONFIRM=1')
  }
  return (
    <Card title={t('tune.load_cell.title')} icon={<Hand size={14} />}>
      {active ? (
        <div className="grid justify-items-center gap-2 rounded-xl bg-accent-tint px-4 py-5 text-center">
          <Spinner size={20} />
          <div className="text-[22px] font-bold tracking-tight text-accent-ink">{last || t('tune.load_cell.measuring')}</div>
          <div className="text-[12.5px] text-ink-2">{t('tune.load_cell.press_hint')}</div>
        </div>
      ) : (
        <>
          <p className="mt-0 mb-3 text-[12.5px] text-ink-3">{t('tune.load_cell.about')}</p>
          {result && (
            <div className={`${result.tone} mb-3 rounded-lg px-3 py-2.5`}>
              <div className="font-semibold">{String(test.result)}</div>
              <div className="text-ink">{t(result.key)}</div>
              {metrics && metrics.change != null && (
                <div className="num mt-1 text-[12px] text-ink-2">
                  {t('tune.load_cell.metrics', { change: Math.round(metrics.change), needed: Math.round(metrics.required_change), noise: Math.round(metrics.noise), release: Math.round(metrics.release_error) })}
                </div>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-primary" disabled={busy} onClick={start}>
              {t('tune.load_cell.start')}
            </button>
          </div>
        </>
      )}
      <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]">
        <TriangleAlert size={14} className="mt-0.5 flex-none" />
        <span className="text-ink">
          {t('tune.load_cell.caution')}
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
    const axes = axis === 'both' ? t('tune.shaper.axes_both') : axis.toUpperCase()
    if (
      await confirm({
        title: t('tune.shaper.confirm_title', { axes }),
        body: (
          <div className="grid gap-2">
            <p className="m-0 font-semibold text-ink">{t('tune.shaper.confirm_empty')}</p>
            <p className="m-0">{homed ? t('tune.shaper.confirm_body') : t('tune.shaper.confirm_body_home')}</p>
          </div>
        ),
        confirm: t('tune.shaper.start'),
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
        <span className="section-label">{t('tune.shaper.axis', { axis: a.toUpperCase() })}</span>
        {freq > 0 ? (
          <span className="v num">
            {type} <small>{freq.toFixed(1)} Hz</small>
          </span>
        ) : (
          <span className="v text-ink-3">{t('common.off')}</span>
        )}
        {damping != null && freq > 0 && <span className="text-[12px] text-ink-3">{t('tune.shaper.damping', { value: Number(damping).toFixed(2) })}</span>}
        {p && (
          <span className="mt-1 text-[12px] font-semibold text-accent-ink">
            {t('tune.shaper.pending', { type: pending[`shaper_type_${a}`] ?? type, freq: pending[`shaper_freq_${a}`] ? `${Number(pending[`shaper_freq_${a}`]).toFixed(1)} Hz` : '' })}
          </span>
        )}
        {unshaped.includes(a) && <span className="mt-1 text-[12px] font-semibold text-bad">{t('tune.shaper.unshaped')}</span>}
      </div>
    )
  }
  const shown = (['x', 'y'] as const).filter((a) => results[a])
  return (
    <Card title={t('tune.shaper.title')} icon={<Waves size={14} />}>
      <div className="grid grid-cols-2 gap-2">{(['x', 'y'] as const).map(axisRow)}</div>
      <div className="mt-1.5 text-[12px] text-ink-3">{t('tune.shaper.from_config')}</div>
      {shaper && shaper.saved === false && (
        <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]">
          <TriangleAlert size={14} className="mt-0.5 flex-none" />
          <span className="text-ink">{t('tune.shaper.not_saved')}</span>
        </div>
      )}
      <div className="sep my-3.5" />
      <div className="flex flex-wrap items-center gap-2.5">
        <Choices
          value={axis}
          onChange={setAxis}
          disabled={busy}
          options={[
            { value: 'both', label: t('tune.shaper.both_axes') },
            { value: 'x', label: 'X' },
            { value: 'y', label: 'Y' },
          ]}
        />
        <span className="flex-1" />
        <button className="btn btn-primary" disabled={busy} onClick={calibrate}>
          <Activity size={15} /> {t('tune.shared.calibrate')}
        </button>
      </div>
      <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px]">
        <TriangleAlert size={14} className="mt-0.5 flex-none" />
        <span className="text-ink">{t('tune.shaper.empty_bed')}</span>
      </div>
      {shown.length > 0 && (
        <div className="mt-3.5 grid gap-3">
          <SectionTitle>{t('tune.shaper.last_calibration')}</SectionTitle>
          {shown.map((a) => {
            const r = results[a]
            return (
              <div key={a} className="grid gap-1.5">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-semibold">{t('tune.shaper.axis', { axis: a.toUpperCase() })}</span>
                  {r.recommended ? (
                    <span className="pill tone-accent">
                      {t('tune.shaper.recommended', { type: r.recommended.type, freq: r.recommended.freq.toFixed(1) })}
                    </span>
                  ) : (
                    <span className="pill tone-muted">
                      <Spinner size={11} /> {t('tune.shaper.calculating')}
                    </span>
                  )}
                  <span className="text-[12px] text-ink-3">{new Date(r.time).toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' })}</span>
                </div>
                {r.fitted.length > 0 && (
                  <div className="-mx-2 overflow-x-auto">
                    <table className="table text-[12.5px]">
                      <thead>
                        <tr>
                          <th>{t('tune.shaper.col_shaper')}</th>
                          <th className="text-right">{t('tune.shaper.col_frequency')}</th>
                          <th className="text-right">{t('tune.shaper.col_vibrations')}</th>
                          <th className="text-right">{t('tune.shaper.col_smoothing')}</th>
                          <th className="text-right">{t('tune.shaper.col_max_accel')}</th>
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
                              <td className="num text-right">{f.maxAccel != null ? `${f.maxAccel.toLocaleString(locale())} mm/s²` : '—'}</td>
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
