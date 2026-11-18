import { useEffect } from 'react'
import { PageHeader, Segmented } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { registerObjects, usePrinter } from '../state/printer'
import { BedMeshTab } from '../components/tune/BedMeshTab'
import { BedSoakTab } from '../components/tune/BedSoakTab'
import { CalibrateTab } from '../components/tune/CalibrateTab'
import { meshPreview } from '../components/tune/mesh'

// Klipper's BED_SCREWS_ADJUST helper isn't in the app's default subscription.
registerObjects((name) => name === 'bed_screws')

type Tab = 'mesh' | 'soak' | 'calibrate'

export function TuneScreen() {
  const sub = useSubPath()[0]
  const hasMesh = usePrinter((s) => 'bed_mesh' in s.status)
  const hasSoak = usePrinter((s) => 'tltg_bed_soak' in s.status)
  const tab: Tab = sub === 'soak' && hasSoak ? 'soak' : sub === 'calibrate' || !hasMesh ? 'calibrate' : 'mesh'

  // A bed soak mesh shown on the Bed mesh tab belongs to this screen.
  useEffect(() => () => meshPreview.set(null), [])

  return (
    <div className="grid min-w-0 gap-3.5">
      <PageHeader title="Tune">
        <Segmented
          label="Tune"
          value={tab}
          options={[
            ...(hasMesh ? [{ value: 'mesh' as const, label: 'Bed mesh', href: href('tune') }] : []),
            ...(hasSoak ? [{ value: 'soak' as const, label: 'Bed soak', href: href('tune', 'soak') }] : []),
            { value: 'calibrate' as const, label: 'Calibrate', href: href('tune', 'calibrate') },
          ]}
        />
      </PageHeader>
      {tab === 'mesh' && <BedMeshTab />}
      {tab === 'soak' && <BedSoakTab />}
      {tab === 'calibrate' && <CalibrateTab />}
    </div>
  )
}
