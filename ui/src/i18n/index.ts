// Valve Oil UI's translations. The browser chooses the language: the one picked in Settings → General, saved in this
// browser, or else the browser's own. The printer only ever sends English, and text that comes from it is shown as it
// arrives.
//
// Messages live in locales/<language>/<area>.json, nested objects of strings in i18next's JSON format: `{{name}}` for a
// value, `<tag>…</tag>` for markup (see rich.tsx), and `_one`/`_few`/`_many`/`_other` suffixes for plural forms, chosen
// with Intl.PluralRules from the `count` value. English is bundled; other languages load when chosen. A message missing
// from a language falls back to English.
import { createStore } from '../lib/store'
import { en } from './en'

export type Messages = typeof en

type PluralSuffix = 'zero' | 'one' | 'two' | 'few' | 'many' | 'other'
type Base<K extends string> = K extends `${infer B}_${PluralSuffix}` ? B : K
type Leaves<T, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${P}${Base<K>}` : Leaves<T[K], `${P}${K}.`>
}[keyof T & string]

/** Every message's key, such as `settings.general.title`, with a plural message's suffixes left off. */
export type MessageKey = Leaves<Messages>
export type Vars = Record<string, string | number | null | undefined>

export const LANGUAGES = [
  { code: 'en', name: 'English' },
  { code: 'es', name: 'Español' },
  { code: 'ru', name: 'Русский' },
] as const
export type Lang = (typeof LANGUAGES)[number]['code']
/** A language picked in Settings, or `auto` to follow the browser. */
export type LangChoice = Lang | 'auto'

const STORAGE_KEY = 'vo-language'
const isLang = (v: unknown): v is Lang => LANGUAGES.some((l) => l.code === v)

type Table = Map<string, string>

function flatten(obj: unknown, prefix = '', into: Table = new Map()): Table {
  if (obj && typeof obj === 'object') {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      if (typeof v === 'string') into.set(prefix + k, v)
      else flatten(v, `${prefix}${k}.`, into)
    }
  }
  return into
}

const english = flatten(en)
const tables: Partial<Record<Lang, Table>> = { en: english }
const pluralRules = new Map<Lang, Intl.PluralRules>()
const rules = (lang: Lang) => {
  let r = pluralRules.get(lang)
  if (!r) pluralRules.set(lang, (r = new Intl.PluralRules(lang)))
  return r
}

// Every other language's area files, loaded on demand as their own chunks.
const loaders = import.meta.glob<{ default: unknown }>(['./locales/*/*.json', '!./locales/en/*.json'])

async function loadTable(lang: Lang): Promise<Table> {
  const have = tables[lang]
  if (have) return have
  const files = Object.entries(loaders).filter(([path]) => path.startsWith(`./locales/${lang}/`))
  const table: Table = new Map()
  await Promise.all(
    files.map(async ([path, load]) => {
      const area = path.slice(path.lastIndexOf('/') + 1, -'.json'.length)
      flatten((await load()).default, `${area}.`, table)
    }),
  )
  return (tables[lang] = table)
}

/** The language shown, and the choice it came from. The app remounts when `lang` changes. */
export const languageStore = createStore<{ lang: Lang; choice: LangChoice }>({ lang: 'en', choice: 'auto' })
let current: Table = english
let ready = false

function savedChoice(): LangChoice {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return isLang(v) ? v : 'auto'
  } catch {
    return 'auto'
  }
}

/** The first of the browser's languages that the UI has, else English. */
export function browserLanguage(): Lang {
  const list = typeof navigator === 'undefined' ? [] : navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const tag of list) {
    const primary = tag?.toLowerCase().split('-')[0]
    if (isLang(primary)) return primary
  }
  return 'en'
}

async function apply(choice: LangChoice) {
  const lang = choice === 'auto' ? browserLanguage() : choice
  let table = english
  try {
    table = await loadTable(lang)
  } catch {
    // A chunk that can't load (a build replaced underneath an open tab) leaves the UI in English.
  }
  current = table
  ready = true
  if (typeof document !== 'undefined') document.documentElement.lang = table === english ? 'en' : lang
  languageStore.set({ lang: table === english ? 'en' : lang, choice })
}

/** Loads the saved or browser language. Call before the first render. */
export const initLanguage = () => apply(savedChoice())

/** Switches language and remembers the choice in this browser. */
export async function setLanguage(choice: LangChoice) {
  try {
    if (choice === 'auto') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, choice)
  } catch {
    // Private browsing without storage: the choice lasts until the page reloads.
  }
  await apply(choice)
}

/**
 * The locale for dates, times, and lists: the browser's own when it's in the language shown, so an en-GB browser keeps
 * its date order, else the language shown.
 */
export function locale(): string {
  const lang = languageStore.get().lang
  const own = typeof navigator === 'undefined' ? '' : navigator.language
  return own && own.toLowerCase().split('-')[0] === lang ? own : lang
}

function pick(table: Table, lang: Lang, key: string, count: unknown): string | undefined {
  if (typeof count === 'number') {
    const form = table.get(`${key}_${rules(lang).select(count)}`) ?? table.get(`${key}_other`)
    if (form != null) return form
  }
  return table.get(key)
}

const VAR = /\{\{\s*(\w+)\s*\}\}/g

export function interpolate(text: string, vars?: Vars): string {
  if (!vars) return text
  return text.replace(VAR, (whole, name: string) => {
    const v = vars[name]
    return v == null ? whole : String(v)
  })
}

/** The raw message for a key in the language shown, falling back to English, before values are filled in. */
export function message(key: MessageKey, count?: number): string {
  if (!ready && import.meta.env.DEV && import.meta.env.MODE !== 'test') {
    console.warn(`t('${key}') was called before the language loaded; call it while rendering, not at module level.`)
  }
  const lang = languageStore.get().lang
  return pick(current, lang, key, count) ?? pick(english, 'en', key, count) ?? key
}

/** A message in the language shown, with `{{name}}` filled from `vars`. `count` picks the plural form. */
export function t(key: MessageKey, vars?: Vars): string {
  return interpolate(message(key, typeof vars?.count === 'number' ? vars.count : undefined), vars)
}
