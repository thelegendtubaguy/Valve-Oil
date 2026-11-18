// The editor itself, loaded on demand (ConfigEditor imports this module dynamically) so CodeMirror stays out of the
// main bundle. Colors come from the app's CSS variables, so one theme serves dark and light.

import { basicSetup } from 'codemirror'
import { EditorView, keymap } from '@codemirror/view'
import { Compartment, EditorState, type Extension } from '@codemirror/state'
import { HighlightStyle, StreamLanguage, indentUnit, syntaxHighlighting } from '@codemirror/language'
import { indentWithTab } from '@codemirror/commands'
import { closeSearchPanel, openSearchPanel, search, searchPanelOpen } from '@codemirror/search'
import { tags as t } from '@lezer/highlight'
import { json } from '@codemirror/legacy-modes/mode/javascript'
import { yaml } from '@codemirror/legacy-modes/mode/yaml'
import { shell } from '@codemirror/legacy-modes/mode/shell'
import { python } from '@codemirror/legacy-modes/mode/python'
import { css } from '@codemirror/legacy-modes/mode/css'
import { toml } from '@codemirror/legacy-modes/mode/toml'
import { gcodeFile, klipperConfig } from './klipper-mode'

export type EditorHandle = {
  getText: () => string
  focus: () => void
  toggleSearch: () => void
  setDark: (dark: boolean) => void
  destroy: () => void
}

function languageFor(name: string): Extension {
  const n = name.toLowerCase()
  if (/\.(cfg|conf|ini|bkp|bak)$/.test(n) || n.endsWith('.cfg.bak')) return StreamLanguage.define(klipperConfig)
  if (/\.(gcode|g|gco|gc|nc)$/.test(n)) return StreamLanguage.define(gcodeFile)
  if (n.endsWith('.json')) return StreamLanguage.define(json)
  if (/\.ya?ml$/.test(n)) return StreamLanguage.define(yaml)
  if (n.endsWith('.sh')) return StreamLanguage.define(shell)
  if (n.endsWith('.py')) return StreamLanguage.define(python)
  if (n.endsWith('.css')) return StreamLanguage.define(css)
  if (n.endsWith('.toml')) return StreamLanguage.define(toml)
  return []
}

const highlight = HighlightStyle.define([
  { tag: [t.heading], color: 'var(--accent-ink)', fontWeight: '700' },
  { tag: [t.labelName], color: 'var(--ink)', fontWeight: '700' },
  { tag: [t.bracket], color: 'var(--accent-ink)' },
  { tag: [t.propertyName, t.attributeName], color: 'var(--series-1)' },
  { tag: [t.keyword], color: 'var(--accent-ink)', fontWeight: '600' },
  { tag: [t.controlKeyword, t.modifier, t.definitionKeyword], color: 'var(--series-7)', fontWeight: '600' },
  { tag: [t.meta, t.brace], color: 'var(--series-5)' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: 'var(--ink-3)', fontStyle: 'italic' },
  { tag: [t.number, t.bool, t.atom, t.null], color: 'var(--series-3)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--ink)' },
  { tag: [t.special(t.variableName), t.function(t.variableName)], color: 'var(--series-2)' },
  { tag: [t.variableName, t.name], color: 'var(--ink)' },
  { tag: [t.operator, t.punctuation], color: 'var(--ink-2)' },
  { tag: [t.typeName, t.className], color: 'var(--series-4)' },
  { tag: t.invalid, color: 'var(--red)' },
])

const theme = (dark: boolean) =>
  EditorView.theme(
    {
      '&': { height: '100%', fontSize: '13px', backgroundColor: 'var(--field)', color: 'var(--ink)' },
      '&.cm-focused': { outline: 'none' },
      '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.55' },
      '.cm-content': { caretColor: 'var(--accent)', paddingBottom: '40vh' },
      '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)', borderLeftWidth: '2px' },
      '.cm-gutters': { backgroundColor: 'var(--surface)', color: 'var(--ink-3)', borderRight: '1px solid var(--line)' },
      '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--accent) 6%, transparent)' },
      '.cm-activeLineGutter': { backgroundColor: 'var(--hover)', color: 'var(--ink)' },
      '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': { backgroundColor: 'color-mix(in srgb, var(--accent) 30%, transparent) !important' },
      '.cm-selectionMatch': { backgroundColor: 'color-mix(in srgb, var(--accent) 14%, transparent)' },
      '.cm-searchMatch': { backgroundColor: 'color-mix(in srgb, var(--accent) 28%, transparent)', outline: '1px solid color-mix(in srgb, var(--accent) 60%, transparent)', borderRadius: '2px' },
      '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'var(--accent)', color: 'var(--on-accent)' },
      '.cm-searchMatch.cm-searchMatch-selected *': { color: 'var(--on-accent) !important' },
      '&.cm-focused .cm-matchingBracket': { backgroundColor: 'var(--accent-tint)', outline: '1px solid var(--accent)' },
      '.cm-foldPlaceholder': { backgroundColor: 'var(--inset)', border: '1px solid var(--line-strong)', color: 'var(--ink-2)', padding: '0 6px', borderRadius: '4px' },
      '.cm-tooltip': { backgroundColor: 'var(--surface)', color: 'var(--ink)', border: 'none', boxShadow: 'var(--shadow-overlay)', borderRadius: '10px', overflow: 'hidden' },
      '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: 'var(--accent-tint)', color: 'var(--ink)' },
      '.cm-panels': { backgroundColor: 'var(--surface)', color: 'var(--ink)', fontFamily: 'var(--font-sans)' },
      '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--line)', position: 'sticky' },
      '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--line)' },
      '.cm-panel.cm-search': { padding: '8px 36px 8px 10px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px' },
      '.cm-panel.cm-search br': { flexBasis: '100%', height: 0, content: '""' },
      '.cm-panel.cm-search label': { display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '12.5px', color: 'var(--ink-2)', margin: 0 },
      '.cm-panel.cm-search [name=close]': { position: 'absolute', top: '6px', right: '8px', fontSize: '20px', color: 'var(--ink-3)', background: 'none', border: 0, cursor: 'pointer' },
      '.cm-textfield': { backgroundColor: 'var(--field)', color: 'var(--ink)', border: 0, boxShadow: 'inset 0 0 0 1px var(--line-strong)', borderRadius: '8px', padding: '5px 8px', fontSize: '13px', margin: 0, minWidth: '0', width: '180px', maxWidth: '100%' },
      '.cm-textfield:focus': { outline: 'none', boxShadow: 'inset 0 0 0 1.5px var(--accent)' },
      '.cm-button': { backgroundImage: 'none', backgroundColor: 'var(--inset)', color: 'var(--ink)', border: 0, boxShadow: 'var(--shadow-btn)', borderRadius: '8px', padding: '4px 10px', fontSize: '12.5px', fontWeight: '650', margin: 0, textTransform: 'none' },
      '.cm-button:active': { backgroundImage: 'none', backgroundColor: 'var(--hover-2)' },
      'input[type=checkbox]': { accentColor: 'var(--accent)' },
    },
    { dark },
  )

export function createEditor(parent: HTMLElement, opts: { doc: string; filename: string; readOnly: boolean; dark: boolean; onChange: (text: string) => void; onSave: () => void }): EditorHandle {
  const themeSlot = new Compartment()
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: opts.doc,
      extensions: [
        keymap.of([
          { key: 'Mod-s', preventDefault: true, run: () => (opts.onSave(), true) },
          indentWithTab,
        ]),
        basicSetup,
        search({ top: true }),
        // CodeMirror's own words, in the app's sentence case.
        EditorState.phrases.of({ Find: 'Find', Replace: 'Replace', next: 'Next', previous: 'Previous', all: 'All', 'match case': 'Match case', regexp: 'Regexp', 'by word': 'By word', replace: 'Replace', 'replace all': 'Replace all', close: 'Close', 'Go to line': 'Go to line', go: 'Go' }),
        indentUnit.of('  '),
        EditorState.tabSize.of(2),
        languageFor(opts.filename),
        syntaxHighlighting(highlight),
        themeSlot.of(theme(opts.dark)),
        EditorState.readOnly.of(opts.readOnly),
        EditorView.editable.of(!opts.readOnly),
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' }),
        EditorView.updateListener.of((u) => u.docChanged && opts.onChange(u.state.doc.toString())),
      ],
    }),
  })
  return {
    getText: () => view.state.doc.toString(),
    focus: () => view.focus(),
    toggleSearch: () => (searchPanelOpen(view.state) ? closeSearchPanel(view) : openSearchPanel(view)),
    setDark: (dark) => view.dispatch({ effects: themeSlot.reconfigure(theme(dark)) }),
    destroy: () => view.destroy(),
  }
}
