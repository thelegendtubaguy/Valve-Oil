// The part of the `shlex` npm package's split() that the G-code parser uses (Fluidd imports the package; Valve Oil UI
// adds no dependencies for it): POSIX shell-like words, with '...' kept literal, "..." taking \" and \\ escapes, and a
// backslash outside quotes escaping the next character. Like shlex, an unclosed quote or a trailing backslash is an
// error.

const WHITESPACE = ' \t\r\n'

export function split(input: string): string[] {
  const words: string[] = []
  let word: string | null = null
  let quote: '"' | "'" | null = null

  for (let i = 0; i < input.length; i++) {
    const c = input[i]

    if (quote === "'") {
      if (c === "'") quote = null
      else word += c
    } else if (quote === '"') {
      if (c === '"') quote = null
      else if (c === '\\' && (input[i + 1] === '"' || input[i + 1] === '\\')) word += input[++i]
      else word += c
    } else if (WHITESPACE.includes(c)) {
      if (word !== null) words.push(word)
      word = null
    } else {
      word ??= ''

      if (c === "'" || c === '"') quote = c
      else if (c === '\\') {
        if (i + 1 === input.length) throw new Error('Got EOF while in an escape sequence')
        word += input[++i]
      } else word += c
    }
  }

  if (quote) throw new Error('Got EOF while in a quoted string')
  if (word !== null) words.push(word)

  return words
}
