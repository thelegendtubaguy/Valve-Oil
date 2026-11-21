import { useEffect, useState, type ReactNode } from 'react'
import { Box as BoxIcon, CircleAlert, LoaderCircle, Pencil, RefreshCw, Settings2, Square, Waypoints } from 'lucide-react'
import { Card, PageHeader, Segmented } from '../components/ui'
import { FilamentPath } from '../components/FilamentPath'
import { attempt } from '../components/toast'
import { BoxUnits } from '../components/box/BoxUnits'
import { BoxSettingsDialog } from '../components/box/BoxSettingsDialog'
import { BoxToolsCard } from '../components/box/BoxTools'
import { SpoolmanCard } from '../components/spoolman/SpoolmanCard'
import { HeaterDialog } from '../components/box/HeaterDialog'
import { LoadedSummary } from '../components/box/LoadedSummary'
import { SlotActions, SlotPanel } from '../components/box/SlotPanel'
import { ToolMapDialog } from '../components/box/ToolMapDialog'
import { FilamentsPage } from '../components/box/filaments/FilamentsPage'
import { boxCommands, slotByIndex, useBox, QIDI_BOX_EXTERNAL_SLOT, type BoxState } from '../state/box'
import { runGcode, usePrinter } from '../state/printer'
import { href, useSubPath } from '../lib/router'
import { t } from '../i18n'

// Home places these on its Box card.
export { HeaterDialog, ToolMapDialog, SlotActions as SlotActionButtons }

export function BoxScreen() {
  const [tab] = useSubPath()
  return tab === 'filaments' ? (
    <>
      <Header tab="filaments" />
      <FilamentsPage />
    </>
  ) : (
    <BoxSlots />
  )
}

function Header({ tab, children }: { tab: 'box' | 'filaments'; children?: ReactNode }) {
  return (
    <PageHeader title="QIDI Box">
      <Segmented
        label="QIDI Box"
        value={tab}
        options={[
          { value: 'box', label: t('box.screen.slots'), href: href('box') },
          {
            value: 'filaments',
            label: t('box.screen.filaments'),
            href: href('box', 'filaments'),
          },
        ]}
      />
      <span className="flex-1" />
      {children}
    </PageHeader>
  )
}

function BoxSlots() {
  const box = useBox()
  const nozzle = usePrinter((s) => (s.status.extruder?.temperature as number | undefined) ?? null)
  const [selected, setSelected] = useState<number | null>(null)
  const [mapOpen, setMapOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [heater, setHeater] = useState<number | null>(null)

  // Show the stranded slot, whose Unload and Eject recover it, when filament strands; another slot can still be
  // chosen afterwards.
  useEffect(() => {
    if (box.strandedSlot != null) setSelected(box.strandedSlot)
  }, [box.strandedSlot])
  const fallback = box.loadedSlot ?? box.slots.find((s) => s.present && s.index !== QIDI_BOX_EXTERNAL_SLOT)?.index ?? box.slots[0]?.index ?? null
  const current = slotByIndex(box, selected) ? selected : fallback
  const slot = slotByIndex(box, current)

  // A tool chip opens the mapping, or selects its slot while mappings can't change.
  const onTool = (_tool: number, s: number) => (box.mappingLocked ? setSelected(s) : setMapOpen(true))

  return (
    <>
      <Header tab="box">
        <span className="inline-flex gap-2">
          <button
            className="btn btn-sm"
            disabled={box.mappingLocked}
            onClick={() => setMapOpen(true)}
            aria-label={t('box.screen.tool_mapping')}
            title={box.mappingLocked ? t('box.screen.tool_mapping_locked') : t('box.screen.tool_mapping')}
          >
            <Pencil size={13} /> <span className="hidden sm:inline">{t('box.screen.tool_mapping')}</span>
          </button>
          <button className="btn btn-sm" onClick={() => setSettingsOpen(true)} aria-label={t('box.screen.settings')} title={t('box.screen.settings')}>
            <Settings2 size={14} /> <span className="hidden sm:inline">{t('box.screen.settings')}</span>
          </button>
        </span>
      </Header>
      <Operation box={box} />
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-3.5 xl:grid-cols-[minmax(0,1fr)_410px]">
        <div className="contents xl:grid xl:gap-3.5">
          <Card className="order-2 xl:order-none">
            <BoxUnits box={box} selected={current} onSelect={setSelected} onTool={onTool} onHeater={setHeater} />
          </Card>
          <Card title={t('box.screen.filament_path')} icon={<Waypoints size={14} />} className="order-5 hidden md:block xl:order-none">
            <FilamentPath box={box} selected={current} onSelect={setSelected} nozzleTemp={nozzle} />
            {!box.flowPaused && box.warnings.length > 0 && (
              <div className="mt-3 grid gap-1.5">
                {box.warnings.map((w) => (
                  <div key={w} className="tone-warn flex items-center gap-2 rounded-lg px-3 py-2">
                    <CircleAlert size={15} /> <span className="text-ink">{w}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="contents xl:grid xl:content-start xl:gap-3.5">
          <Card className="order-1 xl:order-none">
            <LoadedSummary box={box} />
          </Card>
          <div className="order-3 xl:order-none">{slot && <SlotPanel box={box} slot={slot} />}</div>
          <BoxToolsCard box={box} className="order-4 xl:order-none" />
          <SpoolmanCard className="order-5 xl:order-none" />
        </div>
      </div>
      <ToolMapDialog box={box} open={mapOpen} onClose={() => setMapOpen(false)} />
      <BoxSettingsDialog box={box} open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {heater != null && <HeaterDialog box={box} boxNumber={heater} onClose={() => setHeater(null)} />}
    </>
  )
}

/** A running Box flow, or the failed step with Retry and Stop, as the fork's QIDI Box card shows them. */
export function Operation({ box, bare = false }: { box: BoxState; bare?: boolean }) {
  const [pending, setPending] = useState(false)
  const ready = usePrinter((s) => s.klippy === 'ready')
  if (!box.task) return null
  const send = async (g: string) => {
    setPending(true)
    await attempt(() => runGcode(g))
    setPending(false)
  }
  if (box.flowPaused) {
    return (
      <Frame bare={bare} tone="bad">
        <div className="flex items-start gap-2.5">
          <CircleAlert size={18} className="mt-0.5 flex-none text-bad" />
          <div className="min-w-0 flex-1">
            <div className="font-bold text-ink">{box.taskText}</div>
            {box.problems.map((p) => (
              <div key={p} className="text-ink-2">
                {p}
              </div>
            ))}
            <div className="mt-1 text-[12.5px] text-ink-3">{t('box.operation.retry_hint')}</div>
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button className="btn btn-sm btn-primary" disabled={!ready || pending} onClick={() => send(boxCommands.retry())}>
                <RefreshCw size={13} /> {t('common.retry')}
              </button>
              <button className="btn btn-sm" disabled={!ready || pending} onClick={() => send(boxCommands.stop())}>
                <Square size={12} /> {t('box.operation.stop')}
              </button>
            </div>
          </div>
        </div>
      </Frame>
    )
  }
  return (
    <Frame bare={bare} tone="accent">
      <div className="flex items-center gap-2.5">
        <LoaderCircle size={17} className="spin flex-none text-accent-ink" />
        <span className="min-w-0 flex-1">
          <span className="section-label mr-2">{t('box.operation.working')}</span>
          <span className="shimmer font-semibold">{box.taskText}</span>
        </span>
        <BoxIcon size={16} className="flex-none text-ink-3" />
      </div>
    </Frame>
  )
}

function Frame({ bare, tone, children }: { bare: boolean; tone: 'bad' | 'accent'; children: ReactNode }) {
  if (bare) return <div>{children}</div>
  return (
    <div
      className={`rounded-[14px] px-4 py-3 ${tone === 'bad' ? 'tone-bad' : 'tone-accent'}`}
      style={{
        boxShadow: `inset 0 0 0 1px ${tone === 'bad' ? 'var(--crimson)' : 'color-mix(in srgb, var(--accent) 50%, transparent)'}`,
      }}
    >
      {children}
    </div>
  )
}
