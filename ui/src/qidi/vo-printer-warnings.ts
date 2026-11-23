/* eslint-disable @typescript-eslint/no-explicit-any */
// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/vo-printer-warnings.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679, with the board issue-link check of the fork's Valve Oil updates
// section. Keep behavior in step with the fork.
import { voIssueUrlValid } from './vo-status'

export interface VoPrinterWarning {
  id: string;
  level: 'warning' | 'error';
  title: string;
  message: string;
  /** A board's prefilled Valve Oil support issue. */
  url?: string;
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' &&
  value != null &&
  !Array.isArray(value)
)

const isVoPrinterWarning = (value: unknown): value is VoPrinterWarning => (
  isRecord(value) &&
  typeof value.id === 'string' &&
  value.id !== '' &&
  (value.level === 'warning' || value.level === 'error') &&
  typeof value.title === 'string' &&
  value.title !== '' &&
  typeof value.message === 'string'
)

/**
 * Warnings reported by Valve Oil's vo_printer_warnings. The
 * object is absent on printers without the package, so anything that is not
 * the expected shape yields no warnings, and malformed entries, or repeats of
 * an id, are skipped rather than hiding the rest. A board warning's issue link
 * is kept only when it is Valve Oil's issue form, as in the Valve Oil section.
 */
const voPrinterWarnings = (state: unknown): VoPrinterWarning[] => {
  if (!isRecord(state) || !Array.isArray(state.warnings)) {
    return []
  }

  const ids = new Set<string>()

  return state.warnings
    .filter(isVoPrinterWarning)
    .filter(warning => {
      if (ids.has(warning.id)) {
        return false
      }

      ids.add(warning.id)

      return true
    })
    .map(({ id, level, title, message, url }) => ({
      id,
      level,
      title,
      message,
      // A board's prefilled support issue, only when it is this project's issue form.
      ...(voIssueUrlValid(url) ? { url } : {})
    }))
}

export default voPrinterWarnings
