import { useEffect, useRef } from 'react'
import { Cpu } from 'lucide-react'
import { Card, confirm, Spinner, Notice } from '../ui'
import { usePrinter } from '../../state/printer'
import { clearKlipperError, klipperSwitchDisabled, poll, switchKlipper, useValveOil, watchValveOil } from '../../state/valve-oil'
import { VO_KLIPPER_SWITCH_BLOCKERS, voKlipperState, voSentence, type VoKlipper, type VoKlipperTree, type VoStatusBlocker } from '../../qidi/vo-status'
import { t, type MessageKey } from '../../i18n'
import { tr } from '../../i18n/rich'
import { WaitingCard } from './common'

// Which Klipper runs: QIDI's, or Valve Oil's (upstream Klipper with QIDI's changes), which is off unless chosen. The
// choice is the status service's (POST api/klipper), which restarts Klipper; VO_KLIPPER does the same from the console.

const TREES: VoKlipperTree[] = ['qidi', 'valve_oil']
const treeName = (tree: VoKlipperTree) => t(`settings.klipper.name.${tree}` as MessageKey)

// A reason worded here, or else as the printer says it. Valve Oil's Klipper being unavailable is said beside it.
function blockedText(blockedBy: VoStatusBlocker[]) {
  const shown = blockedBy.filter((b) => b.code !== 'valve_oil_unavailable')
  if (shown.length === 0) return blockedBy.length ? '' : t('settings.klipper.blocked.none')
  return shown
    .map((b) =>
      VO_KLIPPER_SWITCH_BLOCKERS.includes(b.code) ? t(`settings.klipper.blocked.code.${b.code}` as MessageKey) : b.code === 'recovery_required' ? voSentence(b.message) : t('settings.klipper.blocked.other', { message: b.message }),
    )
    .join(' ')
}

function unavailableText(k: VoKlipper) {
  if (k.available) return ''
  return !k.unavailable_reason || k.unavailable_reason === 'not installed' ? t('settings.klipper.not_installed') : t('settings.klipper.unavailable_reason', { reason: k.unavailable_reason })
}

async function choose(k: VoKlipper, use: VoKlipperTree) {
  const kind = use === k.selected ? 'retry' : use
  const ok = await confirm({
    title: t(`settings.klipper.confirm.${kind}.title` as MessageKey),
    body: t(`settings.klipper.confirm.${kind}.body` as MessageKey),
    confirm: t('settings.klipper.confirm.button'),
  })
  if (ok) switchKlipper(use)
}

export function KlipperSettings() {
  useEffect(watchValveOil, [])
  const s = useValveOil()
  const klippy = usePrinter((p) => p.klippy)
  // Klipper coming back after a switch shows which one started.
  const lastKlippy = useRef(klippy)
  useEffect(() => {
    if (klippy === 'ready' && lastKlippy.current !== 'ready') poll()
    lastKlippy.current = klippy
  }, [klippy])

  const k = s.status?.klipper
  if (!k)
    return (
      <WaitingCard title={t('settings.klipper.title')} icon={<Cpu size={14} />} waiting={!s.status && !s.unavailable}>
        {s.status ? t('settings.klipper.not_reported') : s.unavailable ? t('settings.klipper.unavailable') : t('settings.klipper.loading')}
      </WaitingCard>
    )

  const state = voKlipperState(k)
  const disabled = klipperSwitchDisabled(s) || state === 'starting'
  const blocked = !k.switch.allowed && state !== 'starting' ? blockedText(k.switch.blocked_by) : ''
  const unavailable = unavailableText(k)

  return (
    <Card title={t('settings.klipper.title')} icon={<Cpu size={14} />}>
      <div className="grid gap-3">
        <div className="flex items-center gap-2 font-semibold">
          {state === 'starting' && <Spinner size={14} />}
          {state === 'starting' ? t(`settings.klipper.state.starting_${k.selected}` as MessageKey) : t(`settings.klipper.state.${state}` as MessageKey)}
        </div>

        {state === 'fallback' && (
          <Notice tone="warn">
            <div>{k.fallback_reason ? t('settings.klipper.fallback_reason', { reason: voSentence(k.fallback_reason) }) : t('settings.klipper.fallback_no_reason')}</div>
            {k.available && (
              <button className="btn btn-sm mt-2" disabled={disabled} onClick={() => choose(k, 'valve_oil')}>
                {s.klipperRequest === 'valve_oil' && <Spinner size={12} />}
                {t('settings.klipper.try_again')}
              </button>
            )}
          </Notice>
        )}

        <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={t('settings.klipper.title')}>
          {TREES.map((tree) => {
            const chosen = k.selected === tree
            const off = disabled || chosen || !k.switch.allowed || (tree === 'valve_oil' && !k.available)
            return (
              <button
                key={tree}
                type="button"
                role="radio"
                aria-checked={chosen}
                disabled={off && !chosen}
                onClick={() => !off && choose(k, tree)}
                className={`grid content-start gap-1 rounded-xl px-3.5 py-3 text-left disabled:opacity-60 ${chosen ? 'bg-accent-tint shadow-[inset_0_0_0_1px_var(--accent)]' : 'shadow-[inset_0_0_0_1px_var(--line)] hover:bg-hover'}`}
              >
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className={`font-semibold ${chosen ? 'text-accent-ink' : ''}`}>{treeName(tree)}</span>
                  {s.klipperRequest === tree && <Spinner size={12} />}
                  {chosen && <span className="pill tone-accent py-0.5!">{t('settings.klipper.chosen')}</span>}
                  {k.running === tree && <span className="pill tone-good py-0.5!">{t('settings.klipper.running')}</span>}
                </span>
                <span className="text-[12.5px] text-ink-3">{t(`settings.klipper.sub.${tree}` as MessageKey)}</span>
                {tree === 'valve_oil' && k.version && <span className="text-[12.5px] text-ink-3">{t('settings.klipper.version', { version: k.version })}</span>}
                {tree === 'valve_oil' && unavailable && <span className="text-[12.5px] text-ink-2">{unavailable}</span>}
              </button>
            )
          })}
        </div>

        {blocked && <div className="text-[12.5px] text-ink-3">{blocked}</div>}
        {k.restart_error && <Notice tone="bad">{voSentence(k.restart_error)}</Notice>}
        {s.klipperError && (
          <Notice tone="bad" onClose={clearKlipperError}>
            {s.klipperBlockedBy.length ? blockedText(s.klipperBlockedBy) || voSentence(s.klipperBlockedBy[0].message) : t('settings.klipper.switch_failed', { error: s.klipperError })}
          </Notice>
        )}
      </div>
      <div className="mt-3 border-t border-line pt-2.5 text-[12px] text-ink-3">
        {/* i18n-ignore: command names */}
        {tr('settings.klipper.footer', { status: <code>VO_KLIPPER</code>, valve_oil: <code>VO_KLIPPER USE=VALVE_OIL</code>, qidi: <code>VO_KLIPPER USE=QIDI</code> })}
      </div>
    </Card>
  )
}
