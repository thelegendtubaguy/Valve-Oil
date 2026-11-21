// What the G-code preview's worker reports when a file can't be read. The worker never has the UI's language, so each
// error carries a code (and values) that the main thread shows as preview.errors.<code>; the English message is for
// logs and for anything that reads it without the code.

export type PreviewErrorCode = 'no_zip_directory' | 'damaged_zip_directory' | 'damaged_gcode_entry' | 'unsupported_compression' | 'no_gcode' | 'download_failed' | 'eof_in_escape' | 'eof_in_quote'

export class PreviewError extends Error {
  constructor(
    readonly code: PreviewErrorCode,
    message: string,
    readonly vars: Record<string, string | number> = {},
  ) {
    super(message)
  }
}
