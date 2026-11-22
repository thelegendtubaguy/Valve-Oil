import { expect, it, vi } from 'vitest'

const delayedRussian = vi.hoisted(() => {
  let release!: () => void
  const wait = new Promise<void>((resolve) => { release = resolve })
  return { wait, release: () => release() }
})

vi.mock('./locales/ru/app.json', async (importOriginal) => {
  await delayedRussian.wait
  return importOriginal()
})

import { languageStore, setLanguage } from './index'

it('keeps the latest language when an earlier translation finishes loading afterward', async () => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    setItem: (key: string, value: string) => values.set(key, value),
    getItem: (key: string) => values.get(key) ?? null,
  })
  const document = { documentElement: { lang: 'en' } }
  vi.stubGlobal('document', document)

  const russian = setLanguage('ru')
  await setLanguage('es')
  delayedRussian.release()
  await russian

  expect(languageStore.get()).toEqual({ lang: 'es', choice: 'es' })
  expect(document.documentElement.lang).toBe('es')
  expect(values.get('vo-language')).toBe('es')
  vi.unstubAllGlobals()
})
