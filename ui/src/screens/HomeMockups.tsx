// The four-column Home mockup with every control on Home. Reached at #/mock; not linked from the rail.
import { useJob } from '../state/job'
import { useHistory } from '../state/files'
import { useBoxPresent } from '../state/box'
import { MiniConsole } from '../components/MiniConsole'
import { SpoolmanCard } from '../components/spoolman/SpoolmanCard'
import { JobCard, ReadyCard, RecentFiles } from './Home'
import { CameraDeck } from '../components/home-deck/CameraDeck'
import { MacrosDeck, OutputsDeck } from '../components/home-deck/Deck'
import { BoxDeck } from '../components/home-deck/BoxDeck'
import { ToolheadCard } from '../components/home-deck/ToolheadCard'
import { TempsCard } from '../components/home-deck/TempsCard'

function useCommon() {
  const job = useJob()
  const showJob = !!job.filename && job.state !== 'standby'
  const jobs = useHistory(500)
  const boxPresent = useBoxPresent()
  const jobCard = showJob ? <JobCard /> : <ReadyCard jobs={jobs} noActions />
  return { jobCard, jobs, boxPresent }
}

/**
 * Four columns. The camera spans the left two, in its own shape, with the Box and the fans and lights under it; then
 * the toolhead over the temperatures; then the print, recent files, and the macros and console, folded until opened. The last card in each column stretches, so
 * the columns end together.
 */
export function HomeMockups() {
  const { jobCard, jobs, boxPresent } = useCommon()
  return (
    <div className="deck deck-4">
      <div className="half">
        <CameraDeck />
        <div className="half-cols">
          <div className="col">
            {boxPresent && <BoxDeck />}
            <SpoolmanCard />
          </div>
          <div className="col">
            <OutputsDeck />
          </div>
        </div>
      </div>
      <div className="col">
        <ToolheadCard />
        <TempsCard />
      </div>
      <div className="col">
        {jobCard}
        <RecentFiles jobs={jobs} className="min-h-[240px]" />
        <MacrosDeck grow fold="vo-home-macros-open" className="min-h-[260px]" />
        <MiniConsole noMacros fold="vo-home-console-open" className="min-h-[320px]" />
      </div>
    </div>
  )
}
