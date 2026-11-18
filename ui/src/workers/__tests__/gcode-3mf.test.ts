import { describe, expect, it } from 'vitest'
import { extract3mfGcode, is3mfUrl } from '../gcode-3mf'

// A zip holding the given entries, stored without compression.
function zip(entries: { name: string; text: string }[]) {
  const enc = new TextEncoder()
  const parts: Uint8Array[] = []
  const central: Uint8Array[] = []
  let offset = 0
  for (const e of entries) {
    const name = enc.encode(e.name)
    const data = enc.encode(e.text)
    const local = new DataView(new ArrayBuffer(30))
    local.setUint32(0, 0x04034b50, true)
    local.setUint32(18, data.length, true)
    local.setUint32(22, data.length, true)
    local.setUint16(26, name.length, true)
    const dir = new DataView(new ArrayBuffer(46))
    dir.setUint32(0, 0x02014b50, true)
    dir.setUint32(20, data.length, true)
    dir.setUint32(24, data.length, true)
    dir.setUint16(28, name.length, true)
    dir.setUint32(42, offset, true)
    parts.push(new Uint8Array(local.buffer), name, data)
    central.push(new Uint8Array(dir.buffer), name)
    offset += 30 + name.length + data.length
  }
  const end = new DataView(new ArrayBuffer(22))
  end.setUint32(0, 0x06054b50, true)
  end.setUint16(10, entries.length, true)
  end.setUint32(16, offset, true)
  const all = [...parts, ...central, new Uint8Array(end.buffer)]
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0))
  all.reduce((at, p) => (out.set(p, at), at + p.length), 0)
  return out.buffer
}

describe('extract3mfGcode', () => {
  it('reads a stored G-code entry', async () => {
    const { body, size } = extract3mfGcode(zip([{ name: 'Metadata/plate_1.png', text: 'png' }, { name: 'Metadata/plate_1.gcode', text: 'G1 X1\n' }]))
    expect(size).toBe(6)
    expect(await new Response(body).text()).toBe('G1 X1\n')
  })

  it('refuses an archive without G-code or a zip directory', () => {
    expect(() => extract3mfGcode(zip([{ name: '3D/3dmodel.model', text: '<model/>' }]))).toThrow('3MF archive holds no G-code')
    expect(() => extract3mfGcode(new ArrayBuffer(64))).toThrow('3MF archive has no zip directory')
  })

  it('recognises 3MF URLs by path', () => {
    expect(is3mfUrl('http://printer/server/files/gcodes/a%20b.gcode.3mf?x=1')).toBe(true)
    expect(is3mfUrl('/server/files/gcodes/a.gcode')).toBe(false)
  })
})
