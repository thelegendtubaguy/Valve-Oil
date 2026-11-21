// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/workers/gcode-3mf.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679. Keep behavior in step with the fork.
// A sliced 3MF (.gcode.3mf from OrcaSlicer or QIDI Studio) is a zip archive holding the plate's G-code, such as
// Metadata/plate_1.gcode. QIDI's Moonraker prints and lists these files; the preview reads the G-code out of them.

import { PreviewError } from './preview-error'

const EOCD_SIGNATURE = 0x06054b50
const CENTRAL_SIGNATURE = 0x02014b50
const LOCAL_SIGNATURE = 0x04034b50

export interface ZipGcodeStream {
  body: ReadableStream<Uint8Array>;
  size: number;
}

export const is3mfUrl = (url: string): boolean => /\.3mf$/i.test(new URL(url, 'http://localhost').pathname)

export const extract3mfGcode = (archive: ArrayBuffer): ZipGcodeStream => {
  const view = new DataView(archive)
  let eocd = -1

  // The end-of-central-directory record sits in the last 22 bytes plus an optional comment of up to 64 KiB.
  for (let offset = archive.byteLength - 22; offset >= Math.max(0, archive.byteLength - 22 - 0xffff); offset--) {
    if (view.getUint32(offset, true) === EOCD_SIGNATURE) {
      eocd = offset
      break
    }
  }

  if (eocd < 0) throw new PreviewError('no_zip_directory', '3MF archive has no zip directory') // i18n-ignore: shown by code on the main thread

  const entries = view.getUint16(eocd + 10, true)
  let offset = view.getUint32(eocd + 16, true)
  const decoder = new TextDecoder()

  for (let i = 0; i < entries; i++) {
    if (view.getUint32(offset, true) !== CENTRAL_SIGNATURE) throw new PreviewError('damaged_zip_directory', '3MF archive has a damaged zip directory') // i18n-ignore: shown by code on the main thread

    const method = view.getUint16(offset + 10, true)
    const compressedSize = view.getUint32(offset + 20, true)
    const size = view.getUint32(offset + 24, true)
    const nameLength = view.getUint16(offset + 28, true)
    const extraLength = view.getUint16(offset + 30, true)
    const commentLength = view.getUint16(offset + 32, true)
    const localOffset = view.getUint32(offset + 42, true)
    const name = decoder.decode(new Uint8Array(archive, offset + 46, nameLength))

    if (/\.gcode$/i.test(name)) {
      if (view.getUint32(localOffset, true) !== LOCAL_SIGNATURE) throw new PreviewError('damaged_gcode_entry', '3MF archive has a damaged G-code entry') // i18n-ignore: shown by code on the main thread

      const start = localOffset + 30 + view.getUint16(localOffset + 26, true) + view.getUint16(localOffset + 28, true)
      const data = new Blob([new Uint8Array(archive, start, compressedSize)]).stream()

      if (method === 0) return { body: data, size }
      if (method === 8) return { body: data.pipeThrough(new DecompressionStream('deflate-raw')), size }

      throw new PreviewError('unsupported_compression', `3MF G-code uses unsupported zip compression ${method}`, { method }) // i18n-ignore: shown by code on the main thread
    }

    offset += 46 + nameLength + extraLength + commentLength
  }

  throw new PreviewError('no_gcode', '3MF archive holds no G-code') // i18n-ignore: shown by code on the main thread
}
