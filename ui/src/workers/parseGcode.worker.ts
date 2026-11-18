// Parses a G-code (or sliced 3MF) file off the main thread. Ported from the Valve Oil Fluidd fork
// (src/workers/parseGcode.worker.ts), with download progress added for 3MF archives.
import type { ParsedGcode } from '../components/gcode-preview/types'
import parseGcode from './parseGcode'

export type ParseGcodeWorkerRequestMessage = {
  action: 'parse'
  url: string
  fileSize: number
}

export type ParseGcodeWorkerResultMessage = { action: 'result' } & ParsedGcode

export type ParseGcodeWorkerResponseMessage =
  | { action: 'progress'; filePosition: number }
  | { action: 'download'; received: number; total: number }
  | ParseGcodeWorkerResultMessage
  | { action: 'error'; error?: string }

const post = (message: ParseGcodeWorkerResponseMessage, transfer: Transferable[] = []) => self.postMessage(message, { transfer })

// Download progress arrives per network chunk; a few updates a second is plenty.
let lastDownload = 0
const sendDownload = (received: number, total: number) => {
  const now = performance.now()

  if (now - lastDownload < 100 && received < total) return

  lastDownload = now
  post({ action: 'download', received, total })
}

self.onmessage = async (event: MessageEvent<ParseGcodeWorkerRequestMessage>) => {
  const message = event.data

  try {
    if (message.action === 'parse') {
      const result = await parseGcode(message.url, message.fileSize, (filePosition) => post({ action: 'progress', filePosition }), sendDownload)

      post(
        { action: 'result', ...result },
        Object.values(result.moves)
          .filter(ArrayBuffer.isView)
          .map((v) => v.buffer),
      )
    }
  } catch (e) {
    console.error('[ParseGcode] error in worker', e)

    post({ action: 'error', error: e instanceof Error ? e.message : String(e) })
  }
}
