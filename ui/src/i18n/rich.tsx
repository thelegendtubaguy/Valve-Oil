import { Fragment, type ReactNode } from 'react'
import { message, type MessageKey } from '.'

type RichVars = Record<string, ReactNode>
type Tags = Record<string, (children: ReactNode) => ReactNode>

const TOKEN = /\{\{\s*(\w+)\s*\}\}|<(\w+)>([\s\S]*?)<\/\2>/g

function render(text: string, vars: RichVars, tags: Tags): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(TOKEN)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    if (m[1] != null) {
      const v = vars[m[1]]
      out.push(v === undefined ? m[0] : v)
    } else {
      const tag = tags[m[2]]
      const inner = render(m[3], vars, tags)
      out.push(tag ? tag(inner) : inner)
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

/**
 * A message with React content: `{{name}}` takes any node from `vars`, and `<tag>text</tag>` wraps its text with
 * `tags[tag]`, so a translation can move a link or a bold word within the sentence. Tags don't nest.
 *
 *   tr('updates.run_hint', { cmd: <code>./install.sh</code> }, { a: (c) => <a href={url}>{c}</a> })
 */
export function tr(key: MessageKey, vars: RichVars = {}, tags: Tags = {}): ReactNode {
  const count = typeof vars.count === 'number' ? vars.count : undefined
  return render(message(key, count), vars, tags).map((node, i) => <Fragment key={i}>{node}</Fragment>)
}
