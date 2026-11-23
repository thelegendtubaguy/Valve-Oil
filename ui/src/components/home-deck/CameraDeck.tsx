// Home's camera card. With more than one camera, a camera's name picks which one it shows, and a second camera can sit
// beside the first. While a print runs, the card switches to Layers, the file's current layer, as on Home.
import { useState } from 'react'
import { Check, ChevronDown, Columns2, X } from 'lucide-react'
import { MenuButton, Segmented } from '../ui'
import { LivePreview } from '../gcode-preview/LivePreview'
import { useJobActive } from '../../state/job'
import { readLocal, saveLocal, useStored } from '../../lib/browser'
import { t } from '../../i18n'
import { CameraView, camMaxWidth, cameraRatio, useWebcams, type Webcam } from '../../screens/Home'

const KEY = 'vo-home-cams'

function saved(): string[] {
  try {
    const v = JSON.parse(readLocal(KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function CameraDeck() {
  const cams = useWebcams()
  const printing = useJobActive()
  const [names, setNames] = useState<string[]>(saved)
  const [view, pickView] = useStored<'camera' | 'layers'>('vo-home-view', 'camera')
  const save = (n: string[]) => {
    setNames(n)
    saveLocal(KEY, JSON.stringify(n))
  }

  // The cameras shown: the saved ones that still exist, at most two and no repeats, else the first camera.
  const shown = [...new Set(names)]
    .map((n) => cams.find((c) => c.name === n))
    .filter((c): c is Webcam => c != null)
    .slice(0, 2)
  if (!shown.length && cams[0]) shown.push(cams[0])

  if (!cams.length && !printing) return null
  const layers = printing && (!cams.length || view === 'layers')
  const toggle = printing && cams.length > 0 && (
    <div className="absolute top-2.5 right-2.5 z-10">
      <Segmented
        label={t('home.camera.view')}
        value={layers ? 'layers' : 'camera'}
        onChange={pickView}
        options={[
          { value: 'camera', label: t('home.camera.camera') },
          { value: 'layers', label: t('home.camera.layers') },
        ]}
      />
    </div>
  )
  const layersRatio = shown[0] ? cameraRatio(shown[0]) : 16 / 9
  if (layers)
    return (
      <section className="card relative p-2.5!">
        {toggle}
        <div className="mx-auto" style={{ aspectRatio: layersRatio, maxWidth: camMaxWidth(layersRatio) }}>
          <LivePreview className="h-full w-full rounded-[10px]" />
        </div>
      </section>
    )

  const current = shown.map((c) => c.name)
  // Show `name` in place `i`; a camera already shown in the other place swaps with it.
  const choose = (i: number, name: string) => {
    const next = [...current]
    const j = next.indexOf(name)
    if (j >= 0 && j !== i) next[j] = next[i]
    next[i] = name
    save(next)
  }
  const picker = (i: number) =>
    cams.length > 1 ? (
      <MenuButton
        className="cam-tag"
        align="left"
        label={t('home.camera.change', { name: current[i] })}
        button={
          <>
            {current[i]} <ChevronDown size={12} />
          </>
        }
        items={[
          ...cams.map((c) => ({ label: c.name, icon: <Check size={14} className={c.name === current[i] ? '' : 'invisible'} />, onSelect: () => choose(i, c.name) })),
          current.length === 1 && { label: t('home.camera.side_by_side'), icon: <Columns2 size={14} />, onSelect: () => save([current[0], cams.find((c) => c.name !== current[0])!.name]) },
          current.length === 2 && { label: t('home.camera.close_this'), icon: <X size={14} />, onSelect: () => save(current.filter((_, k) => k !== i)) },
        ]}
      />
    ) : undefined

  return (
    <section className="card p-2.5!">
      <div className="flex justify-center gap-2.5">
        {shown.map((cam, i) => (
          <CameraView key={cam.name} cam={cam} fit name={picker(i)} toggle={i === shown.length - 1 ? toggle : undefined} />
        ))}
      </div>
    </section>
  )
}
