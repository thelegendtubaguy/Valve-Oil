#!/usr/bin/env node
// Checks Valve Oil UI's translations (src/i18n/locales/<language>/<area>.json) and the source that uses them:
//
// - every language has every English message, no message English lacks, the same {{values}} and <tags>, and each
//   plural message in every plural form the language uses;
// - no text written in English in JSX, in props and object fields that hold text (title, label, sub, ...), or in
//   toast(), confirm(), prompt(), and new Error();
// - dates, times, numbers, and sorting are formatted with locale(), the language shown;
// - t(), tr(), and locale() are not called at module level, where they would run before the language has loaded;
// - every English message is used.
//
// Text that isn't for people to read, or that is the same in every language, can stay with an `i18n-ignore` comment on
// its line or the line before. Run by `npm run build`; run it alone with `node scripts/check-i18n.mjs`.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'

const ROOT = new URL('..', import.meta.url).pathname
const SRC = join(ROOT, 'src')
const LOCALES = join(SRC, 'i18n/locales')
const errors = []
const fail = (msg) => errors.push(msg)

// ---- catalogs ----
const PLURAL = /_(zero|one|two|few|many|other)$/
const VAR = /\{\{\s*(\w+)\s*\}\}/g
const TAG = /<(\w+)>/g

function flatten(obj, prefix = '', into = new Map()) {
  for (const [k, v] of Object.entries(obj)) {
    if (typeof v === 'string') into.set(prefix + k, v)
    else if (v && typeof v === 'object') flatten(v, `${prefix}${k}.`, into)
    else fail(`${prefix}${k}: not a string or object`)
  }
  return into
}

function load(lang) {
  const table = new Map()
  for (const file of readdirSync(join(LOCALES, lang)).filter((f) => f.endsWith('.json'))) {
    const area = file.slice(0, -5)
    let data
    try {
      data = JSON.parse(readFileSync(join(LOCALES, lang, file), 'utf8'))
    } catch (e) {
      fail(`${lang}/${file}: ${e.message}`)
      continue
    }
    flatten(data, `${area}.`, table)
  }
  return table
}

/** Messages grouped by key without plural suffix: base -> Map(form -> text), form '' for a plain message. */
function group(table) {
  const out = new Map()
  for (const [key, text] of table) {
    const m = key.match(PLURAL)
    const base = m ? key.slice(0, -m[0].length) : key
    if (!out.has(base)) out.set(base, new Map())
    out.get(base).set(m ? m[1] : '', text)
  }
  return out
}

const names = (text, re) => new Set([...text.matchAll(re)].map((m) => m[1]))
const union = (forms, re) => new Set([...forms.values()].flatMap((t) => [...names(t, re)]))

/** The plural forms a language needs for whole numbers, and `other` as the fallback. */
function formsFor(lang) {
  const rules = new Intl.PluralRules(lang)
  const forms = new Set(['other'])
  for (let n = 0; n <= 1000; n++) forms.add(rules.select(n))
  return forms
}

const english = load('en')
const en = group(english)
for (const [base, forms] of en) {
  if (forms.size > 1 && forms.has('')) fail(`en: ${base} is both a plain and a plural message`)
  if (forms.size && !forms.has('') && !(forms.has('one') && forms.has('other'))) fail(`en: plural ${base} needs _one and _other`)
  for (const text of forms.values()) if (!text.trim()) fail(`en: ${base} is empty`)
}

const languages = readdirSync(LOCALES).filter((d) => d !== 'en' && statSync(join(LOCALES, d)).isDirectory())
for (const lang of languages) {
  const enFiles = new Set(readdirSync(join(LOCALES, 'en')))
  for (const f of readdirSync(join(LOCALES, lang))) if (!enFiles.has(f)) fail(`${lang}/${f}: no English file of that name`)
  const need = formsFor(lang)
  const tr = group(load(lang))
  for (const [base, enForms] of en) {
    const forms = tr.get(base)
    if (!forms) {
      fail(`${lang}: missing ${base}`)
      continue
    }
    const plural = !enForms.has('')
    if (plural) {
      for (const f of need) if (!forms.has(f)) fail(`${lang}: ${base} needs the _${f} form`)
      if (forms.has('')) fail(`${lang}: ${base} is plural in English`)
    } else if (!forms.has('') || forms.size > 1) fail(`${lang}: ${base} isn't plural in English`)
    for (const [f, text] of forms) if (!text.trim()) fail(`${lang}: ${base}${f ? '_' + f : ''} is empty`)
    const enVars = union(enForms, VAR)
    const trVars = union(forms, VAR)
    for (const v of trVars) if (!enVars.has(v)) fail(`${lang}: ${base} uses {{${v}}}, which English doesn't have`)
    for (const v of enVars) if (v !== 'count' && !trVars.has(v)) fail(`${lang}: ${base} leaves out {{${v}}}`)
    const enTags = union(enForms, TAG)
    const trTags = union(forms, TAG)
    if ([...enTags].sort().join() !== [...trTags].sort().join()) fail(`${lang}: ${base} has tags <${[...trTags].join('>, <')}>, English <${[...enTags].join('>, <')}>`)
  }
  for (const base of tr.keys()) if (!en.has(base)) fail(`${lang}: ${base} isn't an English message`)
}

// ---- source ----
// Names and symbols that read the same in every language.
const SAME = [
  'Valve Oil UI', 'Valve Oil', 'Fluidd', 'Klipper', 'Klippy', 'Moonraker', 'QIDI Studio', 'QIDI Box', 'QIDI', 'OrcaSlicer', 'Spoolman',
  'G-code', 'gcode', 'ID', 'IP', 'URL', 'API', 'Wi-Fi', 'ZeroTier', 'Tailscale', 'Obico', 'RFID', 'THR', 'MCU', 'CPU', 'USB', 'SSH', 'JSON', 'CSV', 'PID', 'ADXL',
  'Max 4', 'Plus 5', 'Smart 5', 'mm/s²', 'mm/s', 'mm³/s', 'mm', '°C', 'Hz', 'KB', 'MB', 'GB',
]
const SAME_RE = new RegExp(SAME.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).sort((a, b) => b.length - a.length).join('|'), 'g')
const wordy = (text) => /\p{L}{2,}/u.test(text.replace(SAME_RE, ' '))

// Props and object fields that hold text for people to read.
const TEXT_PROPS = new Set([
  'title', 'placeholder', 'aria-label', 'alt', 'label', 'sub', 'footer', 'confirm', 'body', 'hint', 'empty', 'heading',
  'description', 'text', 'message', 'tooltip', 'caption', 'aside', 'note', 'detail', 'help', 'summary', 'question',
])
const ASKS = new Set(['toast', 'confirm', 'prompt'])

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === '__tests__' || name === 'i18n' ? [] : files(path)
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

const used = new Set()
const prefixes = new Set()

for (const path of files(SRC)) {
  const code = readFileSync(path, 'utf8')
  const lines = code.split('\n')
  const sf = ts.createSourceFile(path, code, ts.ScriptTarget.Latest, true, path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const where = (node) => {
    const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf))
    return { line, at: `${relative(ROOT, path)}:${line + 1}` }
  }
  const ignored = (node) => {
    const { line } = where(node)
    return /i18n-ignore/.test(lines[line] ?? '') || /i18n-ignore/.test(lines[line - 1] ?? '')
  }
  const literalText = (n) => (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) ? n.text : ts.isTemplateExpression(n) ? [n.head.text, ...n.templateSpans.map((s) => s.literal.text)].join(' ') : null)
  const report = (node, text, what) => {
    if (!ignored(node)) fail(`${where(node).at}: English ${what}: ${JSON.stringify(text.trim().slice(0, 70))}`)
  }
  // Text a JSX child or text prop shows: literals, through ?:, &&, ||, ??, and parentheses.
  const shown = (n, what) => {
    if (!n) return
    const text = literalText(n)
    if (text != null) {
      if (wordy(text)) report(n, text, what)
      return
    }
    if (ts.isParenthesizedExpression(n)) shown(n.expression, what)
    else if (ts.isConditionalExpression(n)) (shown(n.whenTrue, what), shown(n.whenFalse, what))
    else if (ts.isBinaryExpression(n) && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(n.operatorToken.kind)) shown(n.right, what)
  }
  const propName = (n) => (n && (ts.isIdentifier(n) || ts.isStringLiteral(n)) ? n.text : null)

  const visit = (node, inFunction) => {
    // Keys: string literals that could be message keys, and the fixed start of `area.${...}` templates.
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) used.add(node.text)
    if (ts.isTemplateExpression(node) && /^\w+\./.test(node.head.text)) prefixes.add(node.head.text)

    if (ts.isJsxText(node)) {
      if (wordy(node.text)) report(node, node.text, 'JSX text')
    } else if (ts.isJsxExpression(node) && node.parent && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      shown(node.expression, 'JSX text')
    } else if (ts.isJsxAttribute(node) && TEXT_PROPS.has(propName(node.name) ?? '')) {
      const init = node.initializer
      if (init && ts.isStringLiteral(init)) {
        if (wordy(init.text)) report(init, init.text, `${propName(node.name)}=`)
      } else if (init && ts.isJsxExpression(init)) shown(init.expression, `${propName(node.name)}=`)
    } else if (ts.isPropertyAssignment(node) && TEXT_PROPS.has(propName(node.name) ?? '')) {
      shown(node.initializer, `${propName(node.name)}:`)
    } else if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      const fn = node.expression.text
      if (ASKS.has(fn) && node.arguments[0]) shown(node.arguments[0], `${fn}()`)
      if ((fn === 't' || fn === 'tr' || fn === 'locale') && !inFunction) fail(`${where(node).at}: ${fn}() at module level runs before the language loads; call it while rendering`)
    } else if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && /Error$/.test(node.expression.text) && node.arguments?.[0]) {
      shown(node.arguments[0], 'error')
    }
    // Dates, times, numbers, and sorting follow the language shown: pass locale() rather than nothing, [], or undefined.
    const browserLocale = (arg) => !arg || (ts.isArrayLiteralExpression(arg) && !arg.elements.length) || (ts.isIdentifier(arg) && arg.text === 'undefined')
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && /^toLocale(Date|Time)?String$/.test(node.expression.name.text) && browserLocale(node.arguments[0]) && !ignored(node)) {
      fail(`${where(node).at}: ${node.expression.name.text}() without locale()`)
    }
    if (ts.isNewExpression(node) && ts.isPropertyAccessExpression(node.expression) && ts.isIdentifier(node.expression.expression) && node.expression.expression.text === 'Intl' && browserLocale(node.arguments?.[0]) && !ignored(node)) {
      fail(`${where(node).at}: Intl.${node.expression.name.text} without locale()`)
    }
    const fnNode = ts.isFunctionLike(node) || ts.isClassStaticBlockDeclaration(node)
    ts.forEachChild(node, (child) => visit(child, inFunction || fnNode))
  }
  visit(sf, false)
}

for (const base of en.keys()) {
  if (used.has(base)) continue
  if ([...prefixes].some((p) => base.startsWith(p))) continue
  fail(`en: ${base} isn't used`)
}

if (errors.length) {
  console.error(errors.join('\n'))
  console.error(`\n${errors.length} translation problem${errors.length === 1 ? '' : 's'}`)
  process.exitCode = 1
} else {
  console.log(`Translations: ${en.size} messages in ${['en', ...languages].join(', ')}`)
}
