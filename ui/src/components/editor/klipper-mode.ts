// Klipper config and G-code highlighting for CodeMirror's StreamLanguage. Token names are @lezer/highlight tag
// names. Config: [section name] headers, key: value lines, comments, the SAVE_CONFIG block, and the G-code with
// Jinja templates inside gcode: (and *_gcode:) values, which run until the next unindented line.

import type { StreamParser, StringStream } from '@codemirror/language'

type State = {
  /** In a gcode: value: every indented line is G-code. Plain .gcode files are G-code throughout. */
  gcode: boolean
  /** Plain G-code file: never leaves G-code. */
  plain: boolean
  /** Inside a {% %}, {{ }}/{ }, or {# #} that may run past the end of a line. */
  jinja: 'stmt' | 'expr' | 'comment' | null
  /** Nesting of { inside an expression, so {a[{b}]} closes at the right brace. */
  depth: number
  /** The G-code command on this line has been seen; the rest are its parameters. */
  command: boolean
  /** After key: or key=, the rest of the line is the value. */
  value: boolean
  /** Inside a [section] header: 'type' before the first space, then 'name'. */
  header: 'type' | 'name' | null
}

const JINJA_KEYWORDS = new Set(['if', 'elif', 'else', 'endif', 'for', 'endfor', 'in', 'not', 'and', 'or', 'is', 'set', 'macro', 'endmacro', 'call', 'endcall', 'filter', 'endfilter', 'raw', 'endraw', 'break', 'continue', 'with', 'endwith', 'do'])
const JINJA_ATOMS = new Set(['true', 'false', 'none', 'True', 'False', 'None'])
const JINJA_SPECIAL = new Set(['printer', 'params', 'rawparams', 'action_respond_info', 'action_raise_error', 'action_emergency_stop', 'action_call_remote_method'])

const commentStart = (stream: StringStream) => {
  const ch = stream.peek()
  return (ch === '#' || ch === ';') && (stream.pos === 0 || /\s/.test(stream.string.charAt(stream.pos - 1)) || /^\s*$/.test(stream.string.slice(0, stream.pos)))
}

function jinja(stream: StringStream, state: State): string | null {
  if (state.jinja === 'comment') {
    if (stream.skipTo('#}')) {
      stream.pos += 2
      state.jinja = null
    } else stream.skipToEnd()
    return 'comment'
  }
  if (stream.eatSpace()) return null
  if (state.jinja === 'stmt' && stream.match('%}')) return (state.jinja = null), 'meta'
  if (state.jinja === 'expr') {
    if (stream.peek() === '}') {
      stream.next()
      if (state.depth > 0) return state.depth--, 'brace'
      stream.eat('}')
      state.jinja = null
      return 'meta'
    }
    if (stream.eat('{')) return state.depth++, 'brace'
  }
  if (stream.match(/^"(?:[^"\\]|\\.)*"?/) || stream.match(/^'(?:[^'\\]|\\.)*'?/)) return 'string'
  if (stream.match(/^-?\d+(?:\.\d+)?/)) return 'number'
  const word = stream.match(/^[A-Za-z_][A-Za-z0-9_]*/) as RegExpMatchArray | null
  if (word) {
    if (JINJA_KEYWORDS.has(word[0])) return 'controlKeyword'
    if (JINJA_ATOMS.has(word[0])) return 'bool'
    if (JINJA_SPECIAL.has(word[0])) return 'variableName.special'
    return stream.peek() === '(' ? 'function(variableName)' : 'variableName'
  }
  if (stream.match(/^[|.,:()[\]]/)) return 'punctuation'
  if (stream.match(/^(==|!=|<=|>=|[-+*/%<>=~])/)) return 'operator'
  stream.next()
  return null
}

function gcode(stream: StringStream, state: State): string | null {
  if (state.jinja) return jinja(stream, state)
  if (stream.eatSpace()) return null
  if (stream.match('{%')) return (state.jinja = 'stmt'), 'meta'
  if (stream.match('{#')) return (state.jinja = 'comment'), 'comment'
  if (stream.match('{')) return (state.jinja = 'expr'), (state.depth = 0), 'meta'
  if (commentStart(stream)) return stream.skipToEnd(), 'comment'
  if (!state.command) {
    // A command: G1, M104, T0, SET_HEATER_TEMPERATURE, _MY_MACRO, or a line number prefix N10.
    if (stream.match(/^[A-Za-z_][A-Za-z0-9_.]*/)) return (state.command = true), 'keyword'
  }
  if (stream.match(/^[A-Za-z_][A-Za-z0-9_]*(?==)/)) return 'propertyName'
  if (stream.eat('=')) return 'operator'
  if (stream.match(/^[A-Za-z](?=[-+.\d])/)) return 'propertyName'
  if (stream.match(/^[-+]?(?:\d+\.?\d*|\.\d+)/)) return 'number'
  if (stream.match(/^"(?:[^"\\]|\\.)*"?/)) return 'string'
  stream.match(/^[^\s{;#=]+/) || stream.next()
  return 'string'
}

function config(stream: StringStream, state: State): string | null {
  if (stream.sol()) {
    state.command = false
    if (!state.jinja && !state.plain) {
      // An unindented line ends a value. Comment lines don't: Python's configparser skips them inside values.
      if (/^[^\s#;]/.test(stream.string)) state.gcode = state.value = false
      state.header = null
    }
  }
  if (state.plain || (state.gcode && (state.jinja || !stream.sol() || /^\s/.test(stream.string)))) return gcode(stream, state)
  if (state.header) {
    if (stream.eat(']')) return (state.header = null), 'bracket'
    if (state.header === 'type') {
      if (stream.match(/^[^\s\]]+/)) return (state.header = 'name'), 'heading'
    }
    if (stream.eatSpace()) return null
    if (stream.match(/^[^\]]+/)) return 'labelName'
    stream.next()
    return null
  }
  if (stream.sol() && stream.match('#*#')) return stream.skipToEnd(), 'meta'
  if (stream.eatSpace()) return null
  if (commentStart(stream)) return stream.skipToEnd(), 'comment'
  if (stream.sol() && stream.eat('[')) return (state.header = 'type'), 'bracket'
  if (stream.sol() && !state.value) {
    const key = stream.match(/^[^\s:=#;][^:=]*?(?=\s*[:=])/) as RegExpMatchArray | null
    if (key) {
      const k = key[0].trim().toLowerCase()
      state.gcode = k === 'gcode' || k.endsWith('_gcode')
      return 'propertyName'
    }
  }
  if (!state.value && stream.match(/^[:=]/)) {
    state.value = true
    return 'operator'
  }
  if (state.gcode) return gcode(stream, state)
  if (stream.match(/^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?(?=$|[\s,:])/i)) return 'number'
  if (stream.match(/^(?:true|false)\b/i)) return 'bool'
  if (stream.eat(',')) return 'punctuation'
  stream.match(/^[^\s,#;]+/) || stream.next()
  return 'string'
}

const startState = (plain: boolean) => (): State => ({ gcode: plain, plain, jinja: null, depth: 0, command: false, value: false, header: null })
const copyState = (s: State): State => ({ ...s })

/** printer.cfg and other Klipper and Moonraker config files (ini-like). */
export const klipperConfig: StreamParser<State> = {
  name: 'klipper',
  startState: startState(false),
  copyState,
  token: config,
  blankLine: (state) => {
    state.command = false
  },
  languageData: { commentTokens: { line: '#' } },
}

/** Plain G-code files. */
export const gcodeFile: StreamParser<State> = {
  name: 'gcode',
  startState: startState(true),
  copyState,
  token: config,
  languageData: { commentTokens: { line: ';' } },
}
