// The filament library: QIDI's list of filaments the Box and the touchscreen choose from, to add, edit, and remove
// filaments, and to export and import the list. Home's Box card opens it.
import { ChevronLeft } from 'lucide-react'
import { PageHeader } from '../components/ui'
import { FilamentsPage } from '../components/box/filaments/FilamentsPage'
import { href } from '../lib/router'

export function FilamentsScreen() {
  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-1.5">
            <a href={href('home')} className="iconbtn h-8 w-8" title="Back to Home" aria-label="Back to Home">
              <ChevronLeft size={18} />
            </a>
            Filament library
          </span>
        }
      />
      <FilamentsPage />
    </>
  )
}
