/**
 * catalog-emit.ts — the shared catalog grammar engine, HR→XML direction (TS port).
 *
 * A faithful TypeScript port of the reference converter's HR→fmxmlsnippet path
 * (its HR-param matcher, its step orchestrator and every per-type emit helper): parse an
 * HR bracket line into per-param values (two-phase label/flag then positional),
 * then emit the step's XML in **catalog param order** with the full grammar —
 * discriminators, attrGroup/repeatGroup/fieldList/findRequests/parametersList,
 * bitmaskGroup packing, wrapper (parentElement) stacks, and the G10/G11 attribute
 * grammars.
 *
 * The inverse of catalog-grammar.ts (XML→HR). Both read the same catalog grammar
 * model (`GrammarEntry`/`GrammarParam`, incl. the untyped `raw` facet tail) and
 * are kept deliberately parallel to the Python port and the reference so a facet added to
 * one direction is obviously missing from the other (plan's structural-parity
 * risk). Control-flow steps are NOT emitted here — they stay hand-coded in
 * steps/control.ts (the sanctioned exception).
 */

import type { ParsedLine } from './parser';
import type { IdResolver } from './id-resolver';
import { cdata, escXml } from './step-registry';
import {
  decodeWholeCalcLiteral,
  hrBracketDepthDelta,
  hrCalcOpaqueSpanAt,
  isCalcCloseBracket,
  isCalcCloseParen,
  isCalcOpenBracket,
  isCalcOpenParen,
  readsAsQuotedLiteralText,
} from './calc-literal';
import {
  FIELD_MISSING_SENTINEL,
  TABLE_MISSING_SENTINEL,
  type GrammarEntry,
  type GrammarParam,
  paramKey,
  groupFieldsOf,
  projectionOwner,
  isGoverningDiscriminator,
  isDrivenDiscriminator,
  governingDiscriminatorFor,
  valueRevealsCompanion,
  candidateHrLabels,
  hrParamOrder,
  closedVocabEnum,
  rendersBareInHr,
  shapeRoutedValues,
  groupQuoteValue,
  groupUnquoteValue,
} from './catalog-grammar';

// ---------------------------------------------------------------------------
// String helpers — ported to match the reference exactly (Trim strips only
// spaces/tabs, not newlines, so multi-line calcs survive).
// ---------------------------------------------------------------------------
function trim(s: string): string {
  return s.replace(/^[ \t]+/, '').replace(/[ \t]+$/, '');
}

/**
 * Trim a split piece: a line comment ends at a line break, so a piece can end
 * with the break XML→HR wrote after it (calc-literal.ts hrSep); it is not part
 * of the value.
 */
function trimPiece(s: string): string {
  return s.replace(/^[ \t\r\n]+/, '').replace(/[ \t\r\n]+$/, '');
}

function ciEquals(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

function startsWithCi(s: string, prefix: string): boolean {
  return s.slice(0, prefix.length).toLowerCase() === prefix.toLowerCase();
}

/** G11 'Elem/@attr' notation → [true, 'Elem', 'attr']; else [false, '', '']. */
function splitElementAttr(xmlElement: string): [boolean, string, string] {
  const pos = xmlElement.indexOf('/@');
  if (pos === -1) return [false, '', ''];
  return [true, xmlElement.slice(0, pos), xmlElement.slice(pos + 2)];
}

/** Attribute-value escaping: like escXml but numeric-refs whitespace (FM `&#9;`). */
function escXmlAttr(s: string): string {
  let r = '';
  for (const c of s) {
    switch (c) {
      case '&': r += '&amp;'; break;
      case '<': r += '&lt;'; break;
      case '>': r += '&gt;'; break;
      case '"': r += '&quot;'; break;
      case '\t': r += '&#9;'; break;
      case '\n': r += '&#10;'; break;
      case '\r': r += '&#13;'; break;
      default: r += c;
    }
  }
  return r;
}

/**
 * Split on top-level commas, respecting paren/bracket/brace depth + text literals.
 * Used by the parametersList grammar and the attrGroup/repeatGroup body parser,
 * whose quoted values are written as calculation literals (groupQuoteValue). A
 * literal or a calculation comment is stepped over whole with the calculation
 * grammar (calc-literal.ts hrCalcOpaqueSpanAt), so a comma inside `"a\", b"`,
 * `“c, d”` or a block comment is content.
 */
function splitTopLevelCommas(s: string): string[] {
  const out: string[] = [];
  if (!s) return out;
  let cur = '';
  let paren = 0, bracket = 0, brace = 0;
  for (let i = 0; i < s.length; i++) {
    const lit = hrCalcOpaqueSpanAt(s, i, ',');
    if (lit) { cur += s.slice(i, i + lit); i += lit - 1; continue; }
    const c = s[i];
    // Every form FileMaker reads as a parenthesis or bracket counts
    // (`Left （ "abc" , 2 ）` is one item).
    if (isCalcOpenParen(c)) paren++;
    else if (isCalcCloseParen(c)) { if (paren > 0) paren--; }
    else if (isCalcOpenBracket(c)) bracket++;
    else if (isCalcCloseBracket(c)) { if (bracket > 0) bracket--; }
    else if (c === '{') brace++;
    else if (c === '}') { if (brace > 0) brace--; }
    if (c === ',' && paren === 0 && bracket === 0 && brace === 0) {
      const item = trimPiece(cur);
      if (item) out.push(item);
      cur = '';
      continue;
    }
    cur += c;
  }
  const tail = trimPiece(cur);
  if (tail) out.push(tail);
  return out;
}

/**
 * Split `s` on every top-level occurrence of multi-char `delim`, keeping a
 * delimiter inside a quoted region. With `calcLiterals` the region is a
 * calculation text literal or comment (the repeatGroup entries, whose values are
 * calculations or groupQuoteValue-quoted text); without it a straight `"`
 * toggles a find-request phrase (the findRequests rows and criteria: find text
 * is not a calculation, and `"` is FileMaker's match-phrase operator there).
 */
function splitOnDelim(s: string, delim: string, calcLiterals: boolean): string[] {
  const out: string[] = [];
  if (!delim) { out.push(s); return out; }
  let start = 0;
  let inQuote = false;
  let i = 0;
  while (i + delim.length <= s.length) {
    if (calcLiterals) {
      const lit = hrCalcOpaqueSpanAt(s, i, delim.trim()[0] ?? delim[0]);
      if (lit) { i += lit; continue; }
    } else if (s[i] === '"') { inQuote = !inQuote; i++; continue; }
    if (!inQuote && s.slice(i, i + delim.length) === delim) {
      const piece = trimPiece(s.slice(start, i));
      if (piece) out.push(piece);
      i += delim.length;
      start = i;
      continue;
    }
    i++;
  }
  const tail = trimPiece(s.slice(start));
  if (tail) out.push(tail);
  return out;
}

/**
 * Split a fieldList body on top-level commas, PRESERVING empty entries. Text
 * literals and comments are stepped over whole, as in splitTopLevelCommas.
 */
function splitListEntries(s: string): string[] {
  const out: string[] = [];
  if (!trimPiece(s)) return out;
  let cur = '';
  let paren = 0, bracket = 0, brace = 0;
  for (let i = 0; i < s.length; i++) {
    const lit = hrCalcOpaqueSpanAt(s, i, ',');
    if (lit) { cur += s.slice(i, i + lit); i += lit - 1; continue; }
    const c = s[i];
    // Every form FileMaker reads as a parenthesis or bracket counts
    // (`Left （ "abc" , 2 ）` is one item).
    if (isCalcOpenParen(c)) paren++;
    else if (isCalcCloseParen(c)) { if (paren > 0) paren--; }
    else if (isCalcOpenBracket(c)) bracket++;
    else if (isCalcCloseBracket(c)) { if (bracket > 0) bracket--; }
    else if (c === '{') brace++;
    else if (c === '}') { if (brace > 0) brace--; }
    if (c === ',' && paren === 0 && bracket === 0 && brace === 0) {
      out.push(trimPiece(cur));
      cur = '';
      continue;
    }
    cur += c;
  }
  out.push(trimPiece(cur));
  return out;
}

/** Split a parentElement path into wrapper segments (empty path → no segments). */
function splitPath(path: string): string[] {
  return (path ?? '').split('/').filter((seg) => seg.length > 0);
}

function unquote(s: string): string {
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) return s.slice(1, -1);
  return s;
}

function lowerKey(k: string): string {
  return k.toLowerCase();
}

function stripParens(s: string): string {
  const t = trim(s);
  if (t.length >= 2 && t.startsWith('(') && t.endsWith(')')) return trim(t.slice(1, -1));
  return t;
}

/** A target string is a variable ($local / $$global) when its first non-blank char is '$'. */
function isVariable(s: string): boolean {
  const t = s.replace(/^[ \t]+/, '');
  return t.length > 0 && t[0] === '$';
}

/**
 * Split a trailing `[N]` repetition subscript off a field/variable ref.
 *
 * `Invoices::Notes[2]` -> [`Invoices::Notes`, 2]; `$rep[3]` -> [`$rep`, 3].
 * Only a positive integer subscript at the very end counts, with something
 * before it; anything else (no subscript, `[]`, `[0]`, `[$i]`) returns the ref
 * unchanged and 0. FileMaker repetitions are 1-based.
 */
function parseFieldRepetition(ref: string): [string, number] {
  if (ref.length < 3 || ref[ref.length - 1] !== ']') return [ref, 0];
  const open = ref.lastIndexOf('[');
  if (open <= 0 || open + 1 === ref.length - 1) return [ref, 0];
  const digits = ref.slice(open + 1, -1);
  if (!/^[0-9]{1,7}$/.test(digits)) return [ref, 0];
  const rep = parseInt(digits, 10);
  if (rep < 1 || rep > 1000000) return [ref, 0];
  return [ref.slice(0, open), rep];
}

/**
 * Split a trailing repetition subscript that may be a literal OR a calculation.
 *
 * `T::F[2]` -> [`T::F`, 2, '']; `T::F[$i + 1]` -> [`T::F`, 0, `$i + 1`]. The
 * subscript opens at the first `[` and must close, bracket-balanced (text
 * literals and comments are opaque), on the last character; the calc is
 * trimmed (XML→HR writes a line break before the closing `]` after a `//`
 * comment). A digits-only subscript is the literal form with
 * `parseFieldRepetition`'s rules. Anything else returns [ref, 0, ''].
 */
function parseFieldSubscript(ref: string): [string, number, string] {
  if (ref.length < 3 || ref[ref.length - 1] !== ']') return [ref, 0, ''];
  const open = ref.indexOf('[');
  if (open <= 0) return [ref, 0, ''];
  let depth = 0;
  let close = -1;
  for (let i = open; i < ref.length; ) {
    const span = hrCalcOpaqueSpanAt(ref, i);
    if (span) {
      i += span;
      continue;
    }
    // The subscript's own `[ ]` are ASCII; the calculation inside may use `［ ］` too.
    const delta = hrBracketDepthDelta(ref, i, depth);
    depth += delta;
    if (delta < 0 && depth === 0) {
      close = i;
      break;
    }
    i++;
  }
  if (close !== ref.length - 1) return [ref, 0, ''];
  const inner = ref.slice(open + 1, close).replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '');
  if (!inner) return [ref, 0, ''];
  if (/^[0-9]+$/.test(inner)) {
    const [bare, rep] = parseFieldRepetition(ref.slice(0, open) + '[' + inner + ']');
    return [bare, rep, ''];
  }
  return [ref.slice(0, open), 0, inner];
}

/** FileMaker's `repetition` attribute: "0" for the calculated form, else "N" only for N > 1. */
function repetitionAttr(rep: number, calc = ''): string {
  if (calc) return ' repetition="0"';
  return rep > 1 ? ` repetition="${rep}"` : '';
}

/**
 * FileMaker's calculated-repetition sibling, written right after the `<Field>`
 * it subscripts: `<Repetition><Calculation>` (measured, FileMaker Pro 26.0.2:
 * Set Field, Go to Field, Insert Text field and variable targets).
 */
function repetitionCalcSibling(calc: string, indent: string): string {
  if (!calc) return '';
  return `\n${indent}<Repetition>\n${indent}  <Calculation>${cdata(calc)}</Calculation>\n${indent}</Repetition>`;
}

/**
 * Resolve `Table::Field`; FileMaker's `Table::<Field Missing>` (a table with no
 * field) reads back to its stored form, id 0 and an empty name, never a field
 * literally named `<Field Missing>`, and its `<Table Missing>` (no table, no
 * field) to the empty reference.
 */
function resolveFieldRef(
  resolver: IdResolver,
  ref: string,
): { table: string; fieldId: number; fieldName: string } {
  if (trim(ref) === TABLE_MISSING_SENTINEL) return { table: '', fieldId: 0, fieldName: '' };
  const sep = ref.indexOf('::');
  if (sep >= 0 && trim(ref.slice(sep + 2)) === FIELD_MISSING_SENTINEL) {
    return { table: trim(ref.slice(0, sep)), fieldId: 0, fieldName: '' };
  }
  return resolver.resolveField(ref);
}

/**
 * `<Field table id [repetition] name/>` for a `Table::Field[N]` ref, in
 * FileMaker's attribute order, plus the `<Repetition>` sibling for a calculated
 * `Table::Field[calc]`. The subscript is stripped before name resolution. Group
 * children (`allowCalc` false) have no calculated form in FileMaker, whose
 * input-target dialog takes digits only, so a calculated subscript is dropped.
 */
function fieldRefXml(
  resolver: IdResolver,
  ref: string,
  extraAttrs = '',
  indent = '    ',
  allowCalc = true,
): string {
  const [bare, rep, parsedCalc] = parseFieldSubscript(trim(ref));
  const calc = allowCalc ? parsedCalc : '';
  const rf = resolveFieldRef(resolver, bare);
  return (
    `<Field${extraAttrs} table="${escXml(rf.table)}" id="${rf.fieldId}"${repetitionAttr(rep, calc)} name="${escXml(rf.fieldName)}"/>` +
    repetitionCalcSibling(calc, indent)
  );
}

/** `<Field [repetition]>$var</Field>` for a `$var[N]` target, plus the sibling for `$var[calc]`. */
function variableFieldXml(v: string, extraAttrs = '', indent = '    ', allowCalc = true): string {
  const [bare, rep, parsedCalc] = parseFieldSubscript(trim(v));
  const calc = allowCalc ? parsedCalc : '';
  return `<Field${extraAttrs}${repetitionAttr(rep, calc)}>${escXml(bare)}</Field>` + repetitionCalcSibling(calc, indent);
}

/**
 * True when `s`, after trimming and stripping one layer of matching quotes
 * (straight "…" or smart “…”), is a single variable token; returns the bare
 * variable via the tuple's second element.
 */
/** Strip one surrounding quote pair (`"…"`, `“…”` or `‘…’`); a no-op on bare text. */
function unquoteTextValue(s: string): string {
  if (s.length >= 2 && ((s[0] === '"' && s.endsWith('"')) || (s[0] === '“' && s.endsWith('”'))
      || (s[0] === '‘' && s.endsWith('’')))) {
    return s.slice(1, -1);
  }
  return s;
}

/**
 * The table name for a `tableRef` / `tableOccurrence` HR token. An `hrQuoted` one
 * (Save Records as JSONL's `Table: “Invoices”`) was rendered as one escaped
 * literal: decode it, else strip one quote pair.
 */
function tableParamValue(param: GrammarParam, hrValue: string): string {
  if (param.hrQuoted) {
    const t = trim(hrValue);
    return decodeWholeCalcLiteral(t) ?? unquoteTextValue(t);
  }
  return unquote(hrValue);
}

/**
 * The raw XML body for a `text`/`name` param's HR token. An `hrQuoted` param was
 * rendered as one escaped text literal: decode it. A token that is not exactly one
 * literal is FileMaker's own unescaped display (`“He said “hi”`) or bare text:
 * strip one outer quote pair (a no-op on bare text). Any other text param is taken
 * as written, except that a quoted lone variable used as a path/target unquotes to
 * the bare `$` reference.
 */
function textParamValue(param: GrammarParam, hrValue: string): string {
  if (param.hrQuoted) {
    const t = trim(hrValue);
    return decodeWholeCalcLiteral(t) ?? unquoteTextValue(t);
  }
  const [quoted, bare] = isQuotedLoneVariable(hrValue);
  return quoted ? bare : hrValue;
}

function isQuotedLoneVariable(v: string): [boolean, string] {
  let t = trim(v);
  const lq = '“', rq = '”';
  if (t.length >= 2 && t.startsWith('"') && t.endsWith('"')) {
    t = t.slice(1, -1);
  } else if (t.length >= 2 && t.startsWith(lq) && t.endsWith(rq)) {
    t = t.slice(1, -1);
  } else {
    return [false, ''];
  }
  if (!t || t[0] !== '$') return [false, ''];
  if (/[ \t"]/.test(t)) return [false, ''];
  return [true, t];
}

/** Parse one group body ("k1=v1, k2=(…)") into a lower-cased key→raw-value map. */
function parseGroupKV(body: string): Map<string, string> {
  const kv = new Map<string, string>();
  for (const item of splitTopLevelCommas(body)) {
    const eq = item.indexOf('=');
    if (eq === -1) { kv.set(lowerKey(trim(item)), ''); continue; }
    kv.set(lowerKey(trim(item.slice(0, eq))), trim(item.slice(eq + 1)));
  }
  return kv;
}

// ---------------------------------------------------------------------------
// Raw-facet accessors (the untyped tail on GrammarParam.raw).
// ---------------------------------------------------------------------------
type Rec = Record<string, unknown>;
function rawStr(r: Rec, k: string): string {
  const v = r[k];
  return typeof v === 'string' ? v : '';
}
function rawBool(r: Rec, k: string): boolean {
  return r[k] === true;
}
function rawNum(r: Rec, k: string): number {
  const v = r[k];
  return typeof v === 'number' ? v : 0;
}
function rawArr(r: Rec, k: string): Rec[] {
  const v = r[k];
  return Array.isArray(v) ? (v as Rec[]) : [];
}
function rawStrArr(r: Rec, k: string): string[] {
  const v = r[k];
  return Array.isArray(v) ? (v.filter((x) => typeof x === 'string') as string[]) : [];
}

// ---------------------------------------------------------------------------
// Table resolution — the TS IdResolver has no resolveTable; offline (no context)
// a name resolves to id 0 with the name passed through, matching the reference's
// empty-context behaviour (both corpus Table refs are the unconfigured default).
// ---------------------------------------------------------------------------
function resolveTable(name: string): { toId: number; toName: string } {
  return { toId: 0, toName: name };
}

// ---------------------------------------------------------------------------
// Value resolvers (HR token → XML value)
// ---------------------------------------------------------------------------
function resolveEnumXmlValue(param: GrammarParam, hrValue: string): string {
  const enumValues = rawStrArr(param.raw, 'enumValues');
  if (param.flagStyle) {
    const flagged =
      hrValue === 'True' ||
      (hrValue !== '' && !!param.hrLabel && ciEquals(hrValue, param.hrLabel));
    if (flagged) {
      for (const v of enumValues) if (v !== (param.defaultValue ?? '')) return v;
    }
    return param.defaultValue ?? '';
  }
  // Reverse the hrEnumValues map (FM-friendly HR token → raw XML value). std::map
  // iterates keys ascending; sort to match on a (rare) shared-label collision.
  if (Object.keys(param.hrEnumValues).length > 0 && hrValue !== '') {
    for (const k of Object.keys(param.hrEnumValues).sort()) {
      const label = param.hrEnumValues[k];
      if (label && ciEquals(label, hrValue)) return k;
    }
  }
  return hrValue === '' ? (param.defaultValue ?? '') : hrValue;
}

// HR->XML counterpart of catalog-grammar's paramVisible: whether `param` passes its
// `visibleWhen` gate given the sibling param's resolved value from the parsed HR
// `values`. An enum sibling's HR token is resolved to its FM XML value (so "Custom"
// -> "Other" is compared against the value the gate lists); any other sibling uses
// its raw HR value; an empty value falls back to the sibling's default. An
// unresolved gate reference is inert (visible), so a catalog typo degrades to
// always-emit. Symmetric with paramVisible so both directions agree.
function paramVisibleHr(entry: GrammarEntry, values: string[], param: GrammarParam): boolean {
  const vw = param.visibleWhen;
  if (vw === null || !vw.param) return true;
  for (let i = 0; i < entry.params.length; i++) {
    const q = entry.params[i];
    if (paramKey(q) !== vw.param) continue;
    const raw = i < values.length ? values[i] : '';
    let v = q.type === 'enum' ? resolveEnumXmlValue(q, raw) : raw;
    if (v === '') v = q.defaultValue || '';
    return vw.values.includes(v);
  }
  return true;
}

function resolveBoolState(param: GrammarParam, hrValue: string): string {
  let state = param.defaultValue ? param.defaultValue : 'False';
  // A flagStyle boolean's HR presence is its value: the bare label means True and
  // its absence means False. `defaultValue` documents FileMaker's fresh-step
  // default; letting it answer for an absent token makes a flag whose default is
  // "True" impossible to turn off from HR (`Copy [ Invoices::Description ]` would
  // select the field's contents). The explicit off token keeps invertedHr and the
  // hrEnumValues map on one path.
  if (param.flagStyle && hrValue === '') hrValue = 'Off';
  if (hrValue !== '') {
    const lower = hrValue.toLowerCase();
    let hrMeansTrue: boolean;
    if (lower === 'on' || lower === 'true' || lower === 'yes') {
      hrMeansTrue = true;
    } else if (lower === 'off' || lower === 'false' || lower === 'no') {
      hrMeansTrue = false;
    } else {
      hrMeansTrue = state === 'True';
      for (const xmlState of Object.keys(param.hrEnumValues).sort()) {
        if (param.hrEnumValues[xmlState].toLowerCase() === lower) {
          hrMeansTrue = xmlState === 'True';
          break;
        }
      }
    }
    if (param.invertedHr) hrMeansTrue = !hrMeansTrue;
    state = hrMeansTrue ? 'True' : 'False';
  }
  return state;
}

/**
 * Whether a positional (bare) token may be assigned to `param` at all.
 *
 * Pass 2 hands out leftover tokens by POSITION alone, which is safe for a param
 * that accepts free text and wrong for one with a closed vocabulary: the enum
 * swallows whatever token lands at its index and every later bare param shifts
 * up one, silently, because the result is still well-formed XML.
 *
 * Measured (FileMaker Pro 26.0.1): `Go to Record/Request/Page [ With dialog:
 * Off ; 1 ]` is FileMaker's OWN rendering of the ByCalculation form — it prints
 * the row CALCULATION in the slot where the location token would go and prints
 * no location token at all. Reading it positionally produced
 * `<RowPageLocation value="1"/>` with an empty <Calculation>, i.e. the step's
 * only payload dropped. `Go to Portal Row` renders and lost it identically.
 *
 * Only enums that DECLARE a vocabulary are checked, so this narrows nothing
 * that was previously working.
 */
function positionalTokenFitsParam(param: GrammarParam, token: string): boolean {
  if (!closedVocabEnum(param)) return true;
  const enumValues = (param.raw.enumValues as string[] | undefined) ?? [];
  for (const v of enumValues) if (ciEquals(token, v)) return true;
  // hrEnumValues holds FileMaker's DISPLAY spelling of a value; a token in that
  // form is legal input and resolves on emit.
  for (const label of Object.values(param.hrEnumValues))
    if (label && ciEquals(token, label)) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Layout token (self-describing) → LayoutDestination value + <Layout> child XML
// ---------------------------------------------------------------------------
// Strip FileMaker's display decoration from a resolved-layout token: the curly
// quotes it wraps a layout NAME in, and the trailing " (TableOccurrence)". FM
// renders calculation source text with whatever straight quotes the calc itself
// contains, so a curly-quoted token is its unambiguous "this is a layout
// reference" marker. Measured FM 26.0.1: SelectedLayout prints “Dashboard”
// (Admin); a LayoutNameByCalc of "Dashboard" prints "Dashboard".
function stripResolvedLayoutDecoration(tok: string): { wasResolvedForm: boolean; tok: string } {
  if (!tok.startsWith('“')) return { wasResolvedForm: false, tok };
  const close = tok.lastIndexOf('”');
  if (close < 1) return { wasResolvedForm: false, tok };
  return { wasResolvedForm: true, tok: tok.slice(1, close) };
}

// Whether a bare layout token is calculation source text rather than a name.
// FileMaker never renders a layout name bare — resolved is curly-quoted,
// unresolved is <unknown> — so every bare token it emits here is a calculation.
// An agent may reasonably write a bare layout name though, and reading that as a
// calculation would emit a step that evaluates the name as an expression, so only
// tokens that cannot be a name are taken as calculations.
function looksLikeLayoutCalculation(tok: string): boolean {
  if (!tok) return false;
  if (isVariable(tok)) return true;
  if (/^[0-9]+$/.test(tok)) return true;
  return tok.includes('(') || tok.includes('&');
}

function resolveLayoutToken(
  hrValue: string,
  resolver: IdResolver,
): { dest: string; piece: string } {
  const tok = trim(hrValue);
  if (ciEquals(tok, 'original layout') || ciEquals(tok, '<original layout>')) {
    return { dest: 'OriginalLayout', piece: '' };
  }
  if (ciEquals(tok, 'current layout') || ciEquals(tok, '<current layout>')) {
    return { dest: 'CurrentLayout', piece: '' };
  }
  // FM's rendering of a SelectedLayout whose <Layout> is the empty default.
  if (ciEquals(tok, '<unknown>')) {
    return { dest: 'SelectedLayout', piece: '    <Layout id="0" name=""/>' };
  }
  const byCalc = (kw: string, dest: string): { dest: string; piece: string } | null => {
    if (startsWithCi(tok, kw)) {
      const calc = trim(tok.slice(kw.length));
      return {
        dest,
        piece: `    <Layout>\n      <Calculation>${cdata(calc)}</Calculation>\n    </Layout>`,
      };
    }
    return null;
  };
  let r = byCalc('by name:', 'LayoutNameByCalc');
  if (r) return r;
  r = byCalc('by number:', 'LayoutNumberByCalc');
  if (r) return r;
  {
    const [quoted, loneVar] = isQuotedLoneVariable(tok);
    let calc = '';
    if (quoted) calc = loneVar;
    else if (isVariable(tok)) calc = tok;
    if (calc) {
      return {
        dest: 'LayoutNameByCalc',
        piece: `    <Layout>\n      <Calculation>${cdata(calc)}</Calculation>\n    </Layout>`,
      };
    }
  }
  // Curly quotes mark a layout REFERENCE; strip them (and the trailing table
  // occurrence) before resolving. Checked before the bare-calculation test so a
  // name carrying calculation-looking punctuation is never mistaken for a calc.
  const stripped = stripResolvedLayoutDecoration(tok);
  if (!stripped.wasResolvedForm && looksLikeLayoutCalculation(stripped.tok)) {
    return {
      dest: 'LayoutNameByCalc',
      piece: `    <Layout>\n      <Calculation>${cdata(stripped.tok)}</Calculation>\n    </Layout>`,
    };
  }
  const resolved = resolver.resolveLayout(unquote(stripped.tok));
  const piece =
    resolved.id === 0 && resolved.name
      ? `    <Layout name="${escXml(resolved.name)}"/>`
      : `    <Layout id="${resolved.id}" name="${escXml(resolved.name)}"/>`;
  return { dest: 'SelectedLayout', piece };
}

// ---------------------------------------------------------------------------
// Two-phase HR-param matching (parse-HR): pass 1 flags/labels, pass 2 positional.
// ---------------------------------------------------------------------------
function matchParamValues(entry: GrammarEntry, hrParams: string[]): string[] {
  const values: string[] = new Array(entry.params.length).fill('');
  const resolved: boolean[] = new Array(entry.params.length).fill(false);
  const consumed: boolean[] = new Array(hrParams.length).fill(false);
  // Which params actually claimed an HR token, by label or by position.
  // Deliberately distinct from "has a non-empty value": FileMaker renders a
  // present-but-empty slot for a revealed companion, and that empty slot is
  // still evidence of which gate value is active (see the P7.2 derive below).
  const gotToken: boolean[] = new Array(entry.params.length).fill(false);

  for (let pi = 0; pi < entry.params.length; pi++) {
    const param = entry.params[pi];
    if (param.hrHidden) { resolved[pi] = true; continue; }
    if (param.type === 'complex') { resolved[pi] = true; continue; }
    if (isDrivenDiscriminator(entry, param)) { resolved[pi] = true; continue; }

    if (param.type === 'bitmaskGroup') {
      resolved[pi] = true;
      let styleVal = '', controlsVal = '';
      let styleFound = false, ctrlPresent = false;
      const controlsLabel = rawStr(param.raw, 'hrControlsLabel');
      const sp = (param.hrLabel ?? '') + ':';
      const cp = controlsLabel + ':';
      for (let i = 0; i < hrParams.length; i++) {
        if (consumed[i]) continue;
        const t = trim(hrParams[i]);
        if (!styleFound && param.hrLabel && startsWithCi(t, sp)) {
          styleVal = trim(t.slice(sp.length)); styleFound = true; consumed[i] = true;
        } else if (!ctrlPresent && controlsLabel && startsWithCi(t, cp)) {
          controlsVal = trim(t.slice(cp.length)); ctrlPresent = true; consumed[i] = true;
        }
      }
      values[pi] = styleVal;
      if (ctrlPresent) values[pi] += '\x1e' + controlsVal;
      continue;
    }

    if (isGoverningDiscriminator(param)) {
      resolved[pi] = true;
      for (let i = 0; i < hrParams.length && values[pi] === ''; i++) {
        if (consumed[i]) continue;
        const t = trim(hrParams[i]);
        for (const [value, spec] of Object.entries(param.discriminatorValues)) {
          if (spec.hrToken && ciEquals(t, spec.hrToken)) {
            consumed[i] = true;
            values[pi] = value;
            break;
          }
        }
      }
      // Claim the enum's own "hrLabel: value" token (Set Web Viewer "Action: …").
      if (values[pi] === '' && param.hrLabel) {
        const prefix = param.hrLabel + ':';
        for (let i = 0; i < hrParams.length; i++) {
          if (consumed[i]) continue;
          const t = trim(hrParams[i]);
          if (startsWithCi(t, prefix)) {
            consumed[i] = true;
            values[pi] = resolveEnumXmlValue(param, trim(t.slice(prefix.length)));
            break;
          }
        }
      }
      continue;
    }

    const isFlag = (param.flagStyle || param.type === 'flagElement') && !!param.hrLabel;
    if (isFlag) {
      resolved[pi] = true;
      for (let i = 0; i < hrParams.length; i++) {
        if (consumed[i]) continue;
        if (ciEquals(trim(hrParams[i]), param.hrLabel!)) {
          consumed[i] = true;
          values[pi] = 'True';
          gotToken[pi] = true;
          break;
        }
      }
      continue;
    }

    if (param.hrLabel) {
      for (const label of candidateHrLabels(param)) {
        const prefix = label + ':';
        for (let i = 0; i < hrParams.length; i++) {
          if (consumed[i]) continue;
          const t = trim(hrParams[i]);
          if (startsWithCi(t, prefix)) {
            consumed[i] = true;
            values[pi] = trim(t.slice(prefix.length));
            gotToken[pi] = true;
            resolved[pi] = true;
            break;
          }
        }
        if (resolved[pi]) break;
      }
    }
  }

  // Shape-routed governing discriminator (shapeRoutedValues): FileMaker's line shows
  // only the revealed companion, so its shape is the only evidence of the value.
  // Perform AppleScript prints Text mode as the quoted literal (`[ "a "b";c] d" ]`,
  // unescaped) and Calculation mode as the calculation (`[ "say " & $x ]`). A bare
  // token naming one of the two values at the companion's position selects it
  // explicitly (rendered when the companion's shape alone would read as the other
  // value). Otherwise the token there selects the value revealing the hrQuoted
  // companion when it reads as quoted literal text, else the other value. No token
  // leaves the enum at its default.
  for (let pi = 0; pi < entry.params.length; pi++) {
    if (values[pi] !== '') continue;
    const routed = shapeRoutedValues(entry, entry.params[pi]);
    if (!routed) continue;
    // The companion renders at the discriminator's HR slot, so it takes the bare
    // token after those of the positional params rendered before it.
    let before = 0;
    for (const qi of hrParamOrder(entry)) {
      if (qi === pi) break;
      const q = entry.params[qi];
      if (resolved[qi] || !rendersBareInHr(q) || governingDiscriminatorFor(entry, q)) continue;
      before++;
    }
    for (let i = 0; i < hrParams.length; i++) {
      if (consumed[i]) continue;
      if (before > 0) {
        before--;
        continue;
      }
      const t = trim(hrParams[i]);
      if (ciEquals(t, routed.quoted) || ciEquals(t, routed.other)) {
        consumed[i] = true;
        values[pi] = ciEquals(t, routed.quoted) ? routed.quoted : routed.other;
      } else if (t) {
        values[pi] = readsAsQuotedLiteralText(t) ? routed.quoted : routed.other;
      }
      break;
    }
  }

  // Pass 2 — positional for the remaining bare-rendering params, in HR order.
  const positionalTarget = (pi: number): boolean => {
    if (resolved[pi]) return false;
    if (!rendersBareInHr(entry.params[pi])) return false;
    const gov = governingDiscriminatorFor(entry, entry.params[pi]);
    if (gov) {
      const gi = entry.params.indexOf(gov);
      const gval = values[gi] === '' ? (gov.defaultValue ?? '') : values[gi];
      if (!valueRevealsCompanion(gov, gval, paramKey(entry.params[pi]))) return false;
    }
    return true;
  };
  const order = hrParamOrder(entry);
  // Missing optional slot, told apart by vocabulary: with FEWER bare tokens than
  // positional slots, a token naming a value of a LATER closed-vocabulary enum is
  // that enum's token, so a free param ahead of it is the one left out (Save a
  // Copy as with no path: `[ copy of current file ; Create folders: On ]`). Only a
  // deficit enables the skip. Mirrors the reference converter.
  let missingSlots =
    order.filter((pi) => positionalTarget(pi)).length -
    hrParams.filter((t, i) => !consumed[i] && trim(t) !== '').length;
  const namesLaterClosedEnum = (oi: number, token: string): boolean =>
    order
      .slice(oi + 1)
      .some(
        (qi) =>
          closedVocabEnum(entry.params[qi]) &&
          positionalTarget(qi) &&
          positionalTokenFitsParam(entry.params[qi], token),
      );
  let pos = 0;
  for (let oi = 0; oi < order.length; oi++) {
    const pi = order[oi];
    if (!positionalTarget(pi)) continue;
    while (pos < hrParams.length && consumed[pos]) pos++;
    if (pos < hrParams.length) {
      const token = trim(hrParams[pos]);
      // A closed-vocabulary enum only claims a token that names one of its own
      // values; anything else belongs to a later bare param.
      if (!positionalTokenFitsParam(entry.params[pi], token)) continue;
      if (missingSlots > 0 && !closedVocabEnum(entry.params[pi]) && namesLaterClosedEnum(oi, token)) {
        missingSlots--;
        continue;
      }
      consumed[pos] = true;
      values[pi] = token;
      gotToken[pi] = true;
      resolved[pi] = true;
      pos++;
    }
  }

  // hrPositionalKey repeatGroup: FileMaker prints each non-empty entry's
  // positional field bare after the step's own bare params (Show Custom Dialog
  // `[ "T" ; "M" ; $a ; Invoices::Number ]`), so the bare tokens left after pass 2
  // are those entries, in order -- capped at repeatCount, and only a token that
  // can BE a target (a `$variable` or a `Table::Field` reference); anything else
  // stays unconsumed and is reported dropped. Mirrors the reference converter.
  for (let pi = 0; pi < entry.params.length; pi++) {
    const param = entry.params[pi];
    const key = rawStr(param.raw, 'hrPositionalKey');
    if (param.type !== 'repeatGroup' || !key || resolved[pi]) continue;
    const posField = rawArr(param.raw, 'fields').find((f) => rawStr(f, 'key') === key);
    if (!posField) continue;
    const kind = rawStr(posField, 'kind');
    const cap = Number(param.raw.repeatCount ?? 0) || 0;
    const fits = (tok: string): boolean => {
      if (kind !== 'fieldOrVariable' && kind !== 'field') return true;
      if (!tok || tok[0] === '"') return false;
      if (kind === 'fieldOrVariable' && tok[0] === '$') return true;
      return tok.includes('::');
    };
    const parts: string[] = [];
    for (let i = 0; i < hrParams.length; i++) {
      if (consumed[i]) continue;
      const tok = trim(hrParams[i]);
      if (!tok) continue;
      if (cap > 0 && parts.length >= cap) break;
      if (!fits(tok)) break;
      consumed[i] = true;
      parts.push(key + '=' + groupQuoteValue(tok));
    }
    if (parts.length > 0) {
      values[pi] = parts.join(' | ');
      gotToken[pi] = true;
      resolved[pi] = true;
    }
  }

  // P7.2 governed-visibility ENUM: a `visibleWhen` gate names a sibling enum and
  // the values of it under which this param is shown. FileMaker renders a gate
  // value that reveals a companion as the COMPANION ALONE, with no token of its
  // own, so the gate cannot be read back from the HR the way a value that prints
  // a bare token can -- it falls through to its catalog default instead, and
  // FileMaker obeys the default and discards the companion.
  //
  // "A token was present" is the test rather than "the value was non-empty":
  // FileMaker renders the ByCalculation form with an empty calculation as
  // `[ With dialog: Off ;    ]`, and that empty slot is still evidence. Refused
  // when two gate values would explain the same companions equally well.
  for (let gi = 0; gi < entry.params.length; gi++) {
    const gate = entry.params[gi];
    if (gate.type !== 'enum' || values[gi] !== '') continue;
    // A display projection's value comes from its token or the group body, never
    // from its companions (FileMaker's default PDF step has no <PDFSaveType>).
    if (groupFieldsOf(gate).length > 0) continue;
    const gateKey = paramKey(gate);
    let chosen = '';
    let ambiguous = false;
    for (let qi = 0; qi < entry.params.length && !ambiguous; qi++) {
      const gated = entry.params[qi];
      const vw = gated.visibleWhen;
      if (!vw || vw.param !== gateKey || vw.values.length === 0) continue;
      if (!gotToken[qi]) continue;
      for (const v of vw.values) {
        if (chosen === '') chosen = v;
        else if (chosen !== v) { ambiguous = true; break; }
      }
    }
    if (!ambiguous && chosen !== '') values[gi] = chosen;
  }

  // hrEmptySlotWith: an empty token at a param's slot is FileMaker drawing the
  // sibling it stands for (Save Records as Excel `With dialog: Off ; ; Records
  // being browsed` is a step with a <Profile> and no path). An attrGroup with no
  // token of its own takes a body of its attribute defaults.
  for (let pi = 0; pi < entry.params.length; pi++) {
    const slotWith = (entry.params[pi].raw.hrEmptySlotWith as string) ?? '';
    if (!slotWith || !gotToken[pi] || trim(values[pi])) continue;
    for (let qi = 0; qi < entry.params.length; qi++) {
      const q = entry.params[qi];
      if (paramKey(q) !== slotWith) continue;
      if (q.type === 'attrGroup' && !trim(values[qi])) {
        values[qi] = rawArr(q.raw, 'fields')
          .filter((f) => rawStr(f, 'kind') === 'attr' && !rawBool(f, 'optional') && rawStr(f, 'defaultValue'))
          .map((f) => rawStr(f, 'key') + '=' + groupQuoteValue(rawStr(f, 'defaultValue')))
          .join(', ');
      }
      break;
    }
  }

  return values;
}

// ---------------------------------------------------------------------------
// Emit helpers
// ---------------------------------------------------------------------------
function emitBoolean(param: GrammarParam, hrValue: string): string {
  const state = resolveBoolState(param, hrValue);
  const attr = param.xmlAttr || 'state';
  return `    <${param.xmlElement} ${attr}="${state}"/>`;
}

function emitEnum(param: GrammarParam, hrValue: string): string {
  const value = resolveEnumXmlValue(param, hrValue);
  if (param.enumStyle === 'text') {
    return `    <${param.xmlElement}>${escXml(value)}</${param.xmlElement}>`;
  }
  const attr = param.xmlAttr || 'value';
  return `    <${param.xmlElement} ${attr}="${escXml(value)}"/>`;
}

function emitNamedCalc(param: GrammarParam, hrValue: string): string {
  const wrapper = param.wrapperElement || param.xmlElement;
  const wa = rawStr(param.raw, 'wrapperAttr');
  const openAttr = wa ? ' ' + wa : '';
  return `    <${wrapper}${openAttr}>\n      <Calculation>${cdata(hrValue)}</Calculation>\n    </${wrapper}>`;
}

function emitParametersList(param: GrammarParam, hrValue: string): string {
  if (!trim(hrValue)) return '';
  const items = splitTopLevelCommas(hrValue);
  if (items.length === 0) return '';
  const wrapper = param.xmlElement || 'Parameters';
  let out = `    <${wrapper} Count="${items.length}">\n`;
  for (const item of items) {
    out += '      <P>\n';
    out += `        <Calculation>${cdata(item)}</Calculation>\n`;
    out += '      </P>\n';
  }
  out += `    </${wrapper}>`;
  return out;
}

function emitFindRequests(hrValue: string, resolver: IdResolver): string {
  const v = trim(hrValue);
  if (!v) return '';
  const rows = splitOnDelim(v, ' | ', false);
  if (rows.length === 0) return '';
  let out = '    <Query>\n';
  for (const row of rows) {
    let r = trim(row);
    let op = 'Include';
    if (startsWithCi(r, 'Omit ')) { op = 'Exclude'; r = trim(r.slice(5)); }
    else if (startsWithCi(r, 'Include ')) { r = trim(r.slice(8)); }
    out += `      <RequestRow operation="${op}">\n`;
    const crits = splitOnDelim(r, ' & ', false);
    if (crits.length === 0) crits.push('');
    for (const crit of crits) {
      const c = trim(crit);
      let field = '', text = '';
      const colon = c.indexOf(': ');
      if (colon !== -1) { field = trim(c.slice(0, colon)); text = trim(c.slice(colon + 2)); }
      else text = c;
      out += '        <Criteria>\n';
      if (!field) {
        out += '          <Field table="" id="0" name=""/>\n';
      } else {
        const rf = resolveFieldRef(resolver, field);
        out += `          <Field table="${escXml(rf.table)}" id="${rf.fieldId}" name="${escXml(rf.fieldName)}"/>\n`;
      }
      out += `          <Text>${escXml(text)}</Text>\n`;
      out += '        </Criteria>\n';
    }
    out += '      </RequestRow>\n';
  }
  out += '    </Query>';
  return out;
}

function emitFieldOrVariable(
  param: GrammarParam,
  hrValue: string,
  resolver: IdResolver,
  precededByTextElement: boolean,
): string {
  if (!hrValue) {
    if (rawBool(param.raw, 'emitEmptyDefault')) return '    <Field table="" id="0" name=""/>';
    return '';
  }
  // A typed same-element child (typeAttr/typeValue) carries its fixed
  // discriminator attribute on the <Field …> open tag — FM's variable-only
  // <Field type="Messages">/<Field type="ToolCalls"> children of one wrapper.
  // Generic, driven entirely by the catalog. Mirrors the C++/Python converters.
  const typeAttr = rawStr(param.raw, 'typeAttr');
  const typeAttrStr = typeAttr
    ? ` ${typeAttr}="${escXml(rawStr(param.raw, 'typeValue'))}"`
    : '';
  if (isVariable(hrValue)) {
    let out = '';
    if (!precededByTextElement) out += '    <Text/>\n';
    out += '    ' + variableFieldXml(hrValue, typeAttrStr);
    return out;
  }
  let out = '';
  if (rawBool(param.raw, 'textMarker') && !precededByTextElement) out += '    <Text/>\n';
  out += '    ' + fieldRefXml(resolver, hrValue, typeAttrStr);
  return out;
}

// ── attrGroup / repeatGroup ──────────────────────────────────────────────────
function emitGroupElement(
  element: string,
  fields: Rec[],
  kv: Map<string, string>,
  indent: string,
  resolver: IdResolver,
): string {
  let attrs = '';
  let children = '';
  const ci = indent + '  ';
  for (const f of fields) {
    const kind = rawStr(f, 'kind');
    const key = rawStr(f, 'key');
    const it = kv.get(lowerKey(key));
    const present = it !== undefined && it !== '';
    if (kind === 'attr') {
      if (rawBool(f, 'optional') && !present) continue;
      const v = present ? groupUnquoteValue(it!) : rawStr(f, 'defaultValue');
      attrs += ` ${rawStr(f, 'xmlAttr')}="${escXmlAttr(v)}"`;
    } else if (kind === 'text') {
      if (present) {
        const el = rawStr(f, 'childElement');
        children += `${ci}<${el}>${escXml(groupUnquoteValue(it!))}</${el}>\n`;
      }
    } else if (kind === 'calc') {
      const requireAttr = rawStr(f, 'requireAttr');
      if (requireAttr) attrs += ` ${requireAttr}="${present ? 'True' : 'False'}"`;
      if (present) {
        const childElement = rawStr(f, 'childElement');
        if (!childElement) {
          children += `${ci}<Calculation>${cdata(it!)}</Calculation>\n`;
        } else {
          children += `${ci}<${childElement}>\n`;
          children += `${ci}  <Calculation>${cdata(it!)}</Calculation>\n`;
          children += `${ci}</${childElement}>\n`;
        }
      }
    } else if (kind === 'field') {
      if (present) children += `${ci}${fieldRefXml(resolver, it!, '', ci, false)}\n`;
    } else if (kind === 'script') {
      if (present) {
        const rs = resolver.resolveScript(groupUnquoteValue(it!));
        children += `${ci}<${rawStr(f, 'element')} id="${rs.id}" name="${escXml(rs.name)}"/>\n`;
      }
    } else if (kind === 'fieldOrVariable') {
      const v = present ? groupUnquoteValue(it!) : '';
      if (v && isVariable(v)) {
        children += `${ci}${variableFieldXml(v, '', ci, false)}\n`;
      } else if (v) {
        children += `${ci}${fieldRefXml(resolver, v, '', ci, false)}\n`;
      } else {
        children += `${ci}<Field table="" id="0" name=""/>\n`;
      }
    } else if (kind === 'group') {
      if (present) {
        children += emitGroupElement(
          rawStr(f, 'element'), rawArr(f, 'fields'),
          parseGroupKV(stripParens(it!)), ci, resolver,
        ) + '\n';
      }
    }
  }
  const open = `${indent}<${element}${attrs}`;
  if (!children) return open + '/>';
  return `${open}>\n${children}${indent}</${element}>`;
}

function emitRepeatGroup(param: GrammarParam, hrValue: string, resolver: IdResolver): string {
  // hrDefault: no token means FileMaker's default content (its HR line omits it);
  // repeatCount: FileMaker always serializes that many entries, padded.
  if (!trim(hrValue)) hrValue = rawStr(param.raw, 'hrDefault');
  if (!trim(hrValue)) return '';
  const entries = splitOnDelim(hrValue, ' | ', true);
  const cap = Number(param.raw.repeatCount ?? 0) || 0;
  while (cap > 0 && entries.length < cap) entries.push('');
  let out = `    <${param.xmlElement}>\n`;
  for (const e of entries) {
    out += emitGroupElement(
      rawStr(param.raw, 'entryElement'), rawArr(param.raw, 'fields'),
      parseGroupKV(trim(e)), '      ', resolver,
    ) + '\n';
  }
  out += `    </${param.xmlElement}>`;
  return out;
}

function emitAttrGroup(
  param: GrammarParam, hrValue: string, resolver: IdResolver, projected?: Map<string, string>,
): string {
  // `projected`: fields a display projection (groupFields) read from its own
  // token, keyed lower-case; they override the body's and make the group present.
  if (!trim(hrValue) && !param.hrHidden && !(projected && projected.size > 0)) return '';
  const kv = parseGroupKV(hrValue);
  for (const [k, v] of projected ?? []) kv.set(k, v);
  return emitGroupElement(param.xmlElement, rawArr(param.raw, 'fields'), kv, '    ', resolver);
}

// ── bitmaskGroup ─────────────────────────────────────────────────────────────
function bitmaskStyleByHr(param: GrammarParam, tok: string): Rec | null {
  for (const s of rawArr(param.raw, 'bitmaskStyles')) {
    if (ciEquals(tok, rawStr(s, 'hrToken'))) return s;
    for (const a of rawStrArr(s, 'aliases')) if (ciEquals(tok, a)) return s;
  }
  return null;
}

function bitmaskMaskForFlags(param: GrammarParam, labels: string[]): number {
  let m = 0;
  for (const lbl of labels) {
    for (const f of rawArr(param.raw, 'bitmaskFlags')) {
      if (ciEquals(rawStr(f, 'hrLabel'), lbl)) { m |= rawNum(f, 'bit'); break; }
    }
  }
  return m;
}

function bitmaskBitForFlag(param: GrammarParam, label: string): number {
  if (!label) return 0;
  for (const f of rawArr(param.raw, 'bitmaskFlags')) {
    if (ciEquals(rawStr(f, 'hrLabel'), label)) return rawNum(f, 'bit');
  }
  return 0;
}

function computeBitmaskInteger(param: GrammarParam, style: Rec, chrome: number): number {
  let v = rawNum(param.raw, 'bitmaskBase') | rawNum(style, 'baseBit') | chrome;
  const resizeBit = bitmaskBitForFlag(param, rawStr(param.raw, 'bitmaskResizeFlag'));
  const userResizable = rawBool(style, 'docResizable') && (chrome & resizeBit) !== 0;
  if (!userResizable) v |= rawNum(param.raw, 'bitmaskFixedBit');
  return v >>> 0;
}

function parseControlsMask(param: GrammarParam, list: string): number {
  const v = trim(list);
  if (!v || ciEquals(v, 'None')) return 0;
  let m = 0;
  for (const tok of splitTopLevelCommas(v)) {
    const t = trim(tok);
    for (const f of rawArr(param.raw, 'bitmaskFlags')) {
      if (ciEquals(t, rawStr(f, 'hrLabel'))) { m |= rawNum(f, 'bit'); break; }
    }
  }
  return m;
}

function emitBitmaskGroup(param: GrammarParam, packed: string): string {
  const styles = rawArr(param.raw, 'bitmaskStyles');
  if (styles.length === 0) return '';
  let styleTok = packed, controlsTok = '';
  let ctrlPresent = false;
  const sep = packed.indexOf('\x1e');
  if (sep !== -1) { styleTok = packed.slice(0, sep); controlsTok = packed.slice(sep + 1); ctrlPresent = true; }
  styleTok = trim(styleTok);
  const st = (styleTok === '' ? styles[0] : bitmaskStyleByHr(param, styleTok)) ?? styles[0];
  const legal = bitmaskMaskForFlags(param, rawStrArr(st, 'legalFlags'));
  let chrome = ctrlPresent
    ? parseControlsMask(param, controlsTok)
    : bitmaskMaskForFlags(param, rawStrArr(st, 'defaultFlags'));
  chrome &= legal;
  const integer = computeBitmaskInteger(param, st, chrome);
  const styleAttr = rawStr(param.raw, 'bitmaskStyleAttr');
  const valueAttr = rawStr(param.raw, 'bitmaskValueAttr');
  let out = `    <${param.xmlElement}`;
  for (const attr of rawStrArr(param.raw, 'bitmaskAttrOrder')) {
    if (attr === styleAttr) {
      out += ` ${attr}="${escXmlAttr(rawStr(st, 'xmlValue'))}"`;
    } else if (attr === valueAttr) {
      out += ` ${attr}="${integer}"`;
    } else {
      let on = false;
      for (const f of rawArr(param.raw, 'bitmaskFlags')) {
        if (rawStr(f, 'xmlAttr') === attr) { on = (chrome & rawNum(f, 'bit')) !== 0; break; }
      }
      out += ` ${attr}="${on ? 'Yes' : 'No'}"`;
    }
  }
  out += '/>';
  return out;
}

// ── fieldList ────────────────────────────────────────────────────────────────
function emitFieldList(param: GrammarParam, hrValue: string, resolver: IdResolver): string {
  let cattrs = '';
  for (const f of rawArr(param.raw, 'fields')) {
    cattrs += ` ${rawStr(f, 'xmlAttr')}="${escXmlAttr(rawStr(f, 'defaultValue'))}"`;
  }
  const entryElement = rawStr(param.raw, 'entryElement');
  const fieldWrapper = rawStr(param.raw, 'fieldWrapper');
  const entryAttr = rawStr(param.raw, 'entryAttr');
  const entryAttrDefault = rawStr(param.raw, 'entryAttrDefault');
  const fieldFixedAttrs = rawArr(param.raw, 'fieldFixedAttrs');
  let children = '';
  for (const raw of splitListEntries(hrValue)) {
    const entryStr = trim(raw);
    let fieldref = entryStr;
    let attrval = entryAttrDefault;
    const eq = entryStr.indexOf('=');
    if (eq !== -1) { fieldref = trim(entryStr.slice(0, eq)); attrval = trim(entryStr.slice(eq + 1)); }
    const rf = resolveFieldRef(resolver, groupUnquoteValue(fieldref));
    let fattrs = '';
    for (const ff of fieldFixedAttrs) {
      fattrs += ` ${rawStr(ff, 'xmlAttr')}="${escXmlAttr(rawStr(ff, 'defaultValue'))}"`;
    }
    if (entryAttr && !entryElement) fattrs += ` ${entryAttr}="${escXmlAttr(attrval)}"`;
    let node = `<Field${fattrs} table="${escXml(rf.table)}" id="${rf.fieldId}" name="${escXml(rf.fieldName)}"/>`;
    if (fieldWrapper) node = `<${fieldWrapper}>${node}</${fieldWrapper}>`;
    if (entryElement) {
      const eattr = entryAttr ? ` ${entryAttr}="${escXmlAttr(attrval)}"` : '';
      node = `<${entryElement}${eattr}>${node}</${entryElement}>`;
    }
    children += `      ${node}\n`;
  }
  const out = `    <${param.xmlElement}${cattrs}`;
  if (!children) return out + '/>';
  return `${out}>\n${children}    </${param.xmlElement}>`;
}

// ---------------------------------------------------------------------------
// The orchestrator: parse-HR → emit-XML in catalog param order.
// ---------------------------------------------------------------------------
export function convertStepWithCatalog(
  entry: GrammarEntry,
  line: ParsedLine,
  resolver: IdResolver,
): string {
  const params = entry.params;
  let xml = `  <Step enable="${!line.disabled ? 'True' : 'False'}" id="${entry.id}" name="${escXml(entry.name)}">\n`;

  const values = matchParamValues(entry, line.params);

  // G10 attribute-bearing wrapper: a wrapper element may carry an enum value as an
  // attribute (FM serializes <Action value="Queue"> holding children).
  const wrapperAttr = new Map<string, string>();
  const skipParam: boolean[] = new Array(params.length).fill(false);
  {
    const isWrapper = new Set<string>();
    for (const p of params) {
      const segs = splitPath(p.parentElement ?? '');
      if (segs.length > 0) isWrapper.add(segs[0]);
    }
    for (let pi = 0; pi < params.length; pi++) {
      const p = params[pi];
      if (p.type === 'enum' && !p.parentElement && isWrapper.has(p.xmlElement)) {
        const v = values[pi] === '' ? (p.defaultValue ?? '') : values[pi];
        if (v === '') continue;
        const attr = p.xmlAttr || 'value';
        wrapperAttr.set(p.xmlElement, ` ${attr}="${escXml(v)}"`);
        skipParam[pi] = true;
      }
    }
  }

  // G11 attribute-on-element: an enum/boolean param whose xmlElement uses "Elem/@attr"
  // contributes the attribute to the body element emitted by a sibling text/name param.
  const elementAttr = new Map<string, string>();
  for (let pi = 0; pi < params.length; pi++) {
    const p = params[pi];
    const [isEA, elem, attr] = splitElementAttr(p.xmlElement);
    if (!isEA) continue;
    let v: string;
    if (p.type === 'enum') v = resolveEnumXmlValue(p, values[pi]);
    else if (p.type === 'boolean') v = resolveBoolState(p, values[pi]);
    else continue;
    if (v === '') { skipParam[pi] = true; continue; }
    elementAttr.set(elem, (elementAttr.get(elem) ?? '') + ` ${attr}="${escXml(v)}"`);
    skipParam[pi] = true;
  }

  // attrGroup display projection (groupFields): a projection owns no element;
  // its token's value (resolved back through hrEnumValues) is split over the
  // fields it projects and handed to the owning group's emit.
  const projectedFields = new Map<number, Map<string, string>>();
  for (let pi = 0; pi < params.length; pi++) {
    const p = params[pi];
    const gf = groupFieldsOf(p);
    if (gf.length === 0) continue;
    skipParam[pi] = true;
    if (!trim(values[pi])) continue;
    const owner = projectionOwner(entry, p);
    if (owner === null) continue;
    const oi = params.indexOf(owner);
    const parts = resolveEnumXmlValue(p, trim(values[pi])).split('|');
    gf.forEach((key, i) => {
      const part = parts[i] ?? '';
      if (!part) return;
      if (!projectedFields.has(oi)) projectedFields.set(oi, new Map());
      projectedFields.get(oi)!.set(lowerKey(key), part);
    });
  }

  // Discriminator-driven layout: pre-resolve destination + <Layout> piece.
  const discrimValue = new Map<string, string>();
  const layoutPiece = new Map<number, string>();
  for (let pi = 0; pi < params.length; pi++) {
    const p = params[pi];
    if (p.type !== 'layout' || !p.discriminator) continue;
    const { dest, piece } = resolveLayoutToken(values[pi], resolver);
    discrimValue.set(p.discriminator, dest);
    layoutPiece.set(pi, piece);
  }

  // Governing-discriminator pre-pass: resolve each governing enum's XML value.
  const govDiscrimValue = new Map<string, string>();
  for (let pi = 0; pi < params.length; pi++) {
    const p = params[pi];
    if (!isGoverningDiscriminator(p)) continue;
    govDiscrimValue.set(p.xmlElement, values[pi] === '' ? (p.defaultValue ?? '') : values[pi]);
  }

  // Governed-visibility boolean: an `hrHidden` boolean that some sibling's
  // `visibleWhen` gates on carries NO HR token of its own — FileMaker's HR shows
  // none either — so it cannot be read back from HR the way a flag-style boolean
  // can. Its state is instead DERIVED on emit from whether any gated sibling
  // contributed a token, which is exactly how FileMaker's own HR encodes it:
  // Import Records shows Table/method/charset only under Restore=True, and
  // FileMaker discards the stored import order when the flag is off.
  //
  // Without this the flag falls through to its catalog `defaultValue` (True for
  // both Restore params), so HR that says "no stored import order" would
  // serialize as "restore the stored order".
  //
  // The `values[gi] === ''` guard is what keeps the SaXML reader whole: on the
  // HR path a hidden param is excluded from matching so its slot is always
  // empty, but a SaXML decoder may have read the gate's real value from the
  // source (Export Records sets its own Restore), and that reading wins.
  const impliedBool = new Map<number, string>(); // param idx -> state
  for (let gi = 0; gi < params.length; gi++) {
    const gate = params[gi];
    if (!gate.hrHidden || gate.type !== 'boolean' || values[gi] !== '') continue;
    const gateKey = paramKey(gate);
    let onValue = ''; // the gate value that REVEALS a companion
    let gates = false, anyContent = false;
    for (let pi = 0; pi < params.length; pi++) {
      const vw = params[pi].visibleWhen;
      if (!vw || vw.param !== gateKey || vw.values.length === 0) continue;
      gates = true;
      if (onValue === '') onValue = vw.values[0];
      if (trim(values[pi]) !== '') anyContent = true;
    }
    if (!gates) continue; // hrHidden but nothing gates on it: default emit
    const offValue = onValue === 'True' ? 'False' : 'True';
    impliedBool.set(gi, anyContent ? onValue : offValue);
  }

  // G10 nested attribute-bearing wrapper: the step-level rule one level down. An
  // enum or boolean INSIDE a group whose xmlElement names a wrapper other params
  // nest inside (parentElement "<its parent>/<it>") carries its value as that
  // wrapper's attribute: Insert from Device writes <ScanFrom type="Field"><Field
  // …/></ScanFrom> and <MaxDuration state="True"><Calculation>…</Calculation>
  // </MaxDuration>. FileMaker serializes such a wrapper in every state, child or
  // not (<ScanFrom type="Camera"/>, <MaxDuration state="False"/>), so it opens at
  // its own slot and closes self-closed when no child follows. A governed one is
  // gated like any companion; a hidden boolean takes its derived state.
  const wrapperAttrByPath = new Map<string, string>();
  const nestedWrapperOpen = new Map<number, string>();
  {
    const wrapperPaths = new Set<string>();
    for (const p of params) {
      let prefix = '';
      for (const seg of splitPath(p.parentElement ?? '')) {
        prefix = prefix ? prefix + '/' + seg : seg;
        wrapperPaths.add(prefix);
      }
    }
    for (let pi = 0; pi < params.length; pi++) {
      const p = params[pi];
      if (!p.parentElement || (p.type !== 'enum' && p.type !== 'boolean')) continue;
      if (splitElementAttr(p.xmlElement)[0]) continue;
      const path = p.parentElement + '/' + p.xmlElement;
      if (!wrapperPaths.has(path)) continue;
      skipParam[pi] = true;
      const gov = governingDiscriminatorFor(entry, p);
      if (gov) {
        const dval = govDiscrimValue.get(gov.xmlElement) ?? (gov.defaultValue ?? '');
        if (!valueRevealsCompanion(gov, dval, paramKey(p))) continue;
      }
      const v =
        p.type === 'boolean'
          ? (impliedBool.get(pi) ?? resolveBoolState(p, values[pi]))
          : resolveEnumXmlValue(p, values[pi]);
      const attr = p.xmlAttr || (p.type === 'boolean' ? 'state' : 'value');
      if (v) wrapperAttrByPath.set(path, ` ${attr}="${escXml(v)}"`);
      nestedWrapperOpen.set(pi, path);
    }
  }

  let prevWasTextElement = false;
  const openGroups: string[] = [];
  // xml.length right after each open wrapper's start tag: a wrapper closed with
  // nothing written inside it closes self-closed (<ScanFrom type="Camera"/>).
  const openTagEnd: number[] = [];
  // Keep the shared prefix of the open-wrapper stack, close what we left, open what
  // is new (with any G10 attribute resolved for it).
  const reconcileWrappers = (want: string[]): void => {
    let common = 0;
    while (common < openGroups.length && common < want.length && openGroups[common] === want[common]) {
      common++;
    }
    for (let k = openGroups.length; k > common; k--) {
      if (xml.length === openTagEnd[k - 1]) xml = xml.slice(0, -2) + '/>\n';
      else xml += `    </${openGroups[k - 1]}>\n`;
    }
    openGroups.length = common;
    openTagEnd.length = common;
    let path = '';
    for (let k = 0; k < want.length; k++) {
      path = path ? path + '/' + want[k] : want[k];
      if (k < common) continue;
      const wa = wrapperAttrByPath.get(path) ?? wrapperAttr.get(want[k]) ?? '';
      xml += `    <${want[k]}${wa}>\n`;
      openGroups.push(want[k]);
      openTagEnd.push(xml.length);
    }
  };

  for (let pi = 0; pi < params.length; pi++) {
    const param = params[pi];
    const hrValue = values[pi];
    const nested = nestedWrapperOpen.get(pi);
    if (nested !== undefined) {
      reconcileWrappers(splitPath(nested));
      prevWasTextElement = false;
      continue;
    }
    if (skipParam[pi]) continue;

    const isTextElement =
      (param.type === 'text' || param.type === 'name') && param.xmlElement === 'Text';

    let piece = '';
    const gov = governingDiscriminatorFor(entry, param);
    let govHandled = false;
    if (gov) {
      const dval = govDiscrimValue.has(gov.xmlElement)
        ? govDiscrimValue.get(gov.xmlElement)!
        : (gov.defaultValue ?? '');
      const revealed = valueRevealsCompanion(gov, dval, paramKey(param));
      if (param.type === 'boolean' && !revealed && param.omitWhenEmpty) {
        govHandled = true;
      } else if (param.type === 'boolean') {
        const attr = param.xmlAttr || 'state';
        let st: string;
        if (!revealed) st = param.defaultValue ? param.defaultValue : 'False';
        else st = resolveBoolState(param, hrValue === '' ? 'Off' : hrValue);
        piece = `    <${param.xmlElement} ${attr}="${st}"/>`;
        govHandled = true;
      } else if (!revealed) {
        govHandled = true;
      }
    }

    if (govHandled) {
      // piece already decided (a value or intentionally empty).
    } else if (
      param.type === 'boolean' &&
      param.visibleWhen !== null &&
      param.visibleWhen.param &&
      param.omitWhenEmpty
    ) {
      // P7.3 governed-visibility boolean: a boolean whose PRESENCE (not just value)
      // is governed by a sibling enum's value declares a `visibleWhen` gate — the
      // generic "governed by another param's enum value" primitive, symmetric with
      // the XML->HR paramVisible skip and the discriminator-revealed omit. When the
      // gate value is not met FileMaker omits the element ENTIRELY (confirmed live:
      // Configure AI Account drops <VerifySSLCertificates> for every LLMType except
      // "Other"); when met it emits with normal flag-style presence semantics.
      // Opt-in via omitWhenEmpty. Benefits any step of this shape, not just #212.
      if (paramVisibleHr(entry, values, param)) {
        piece = emitBoolean(param, hrValue);
      }
      // else: gate not met -> omit entirely (piece stays '').
    } else if (param.type === 'boolean') {
      if (impliedBool.has(pi)) {
        const attr = param.xmlAttr || 'state';
        piece = `    <${param.xmlElement} ${attr}="${impliedBool.get(pi)!}"/>`;
      } else {
        piece = emitBoolean(param, hrValue);
      }
    } else if (param.type === 'enum') {
      if (discrimValue.has(param.xmlElement)) {
        const attr = param.xmlAttr || 'value';
        piece = `    <${param.xmlElement} ${attr}="${escXml(discrimValue.get(param.xmlElement)!)}"/>`;
      } else if (isGoverningDiscriminator(param)) {
        const v = govDiscrimValue.has(param.xmlElement)
          ? govDiscrimValue.get(param.xmlElement)!
          : (param.defaultValue ?? '');
        if (param.enumStyle === 'text') {
          piece = `    <${param.xmlElement}>${escXml(v)}</${param.xmlElement}>`;
        } else {
          const attr = param.xmlAttr || 'value';
          piece = `    <${param.xmlElement} ${attr}="${escXml(v)}"/>`;
        }
      } else if (param.omitWhenEmpty && !trim(hrValue)) {
        // present-driven enum omitted when unset.
      } else {
        piece = emitEnum(param, hrValue);
      }
    } else if (param.type === 'calculation' || param.type === 'calc') {
      if (!param.omitWhenEmpty || trim(hrValue)) {
        piece = `    <Calculation>${cdata(hrValue)}</Calculation>`;
      }
    } else if (param.type === 'attrGroup') {
      piece = emitAttrGroup(param, hrValue, resolver, projectedFields.get(pi));
    } else if (param.type === 'bitmaskGroup') {
      piece = emitBitmaskGroup(param, hrValue);
    } else if (param.type === 'repeatGroup') {
      piece = emitRepeatGroup(param, hrValue, resolver);
    } else if (param.type === 'fieldList') {
      if (trim(hrValue)) piece = emitFieldList(param, hrValue, resolver);
    } else if (param.type === 'namedCalc') {
      if (hrValue !== '' || param.required) piece = emitNamedCalc(param, hrValue);
    } else if (param.type === 'parametersList') {
      piece = emitParametersList(param, hrValue);
    } else if (param.type === 'findRequests') {
      piece = emitFindRequests(hrValue, resolver);
    } else if (param.type === 'fieldOrVariable') {
      piece = emitFieldOrVariable(param, hrValue, resolver, prevWasTextElement);
    } else if (param.type === 'flagElement') {
      if (hrValue !== '') piece = `    <${param.xmlElement}/>`;
    } else if (param.type === 'field') {
      if (!param.omitWhenEmpty || trim(hrValue)) piece = '    ' + fieldRefXml(resolver, hrValue);
    } else if (param.type === 'tableRef') {
      if (!trim(hrValue)) {
        // A present-driven, gated table (Fine-Tune Model's training <Table>,
        // revealed only in the DataTable branch) opts into omitWhenEmpty and
        // emits nothing when unset.
        if (!param.omitWhenEmpty) piece = '    <Table id="" name=""/>';
      } else {
        const rt = resolveTable(tableParamValue(param, hrValue));
        piece = `    <Table id="${rt.toId}" name="${escXml(rt.toName)}"/>`;
      }
    } else if (param.type === 'tableOccurrence') {
      // An unset present-driven TO (omitWhenEmpty) emits no <Table> — matching
      // FM's TrainingFile form; otherwise the TO always serializes.
      if (trim(hrValue) || !param.omitWhenEmpty) {
        const rt = resolveTable(tableParamValue(param, hrValue));
        piece = `    <Table id="${rt.toId}" name="${escXml(rt.toName)}"/>`;
      }
    } else if (param.type === 'fileReference') {
      if (trim(hrValue)) {
        const [quoted, bare] = isQuotedLoneVariable(hrValue);
        const emitValue = quoted ? bare : hrValue;
        piece =
          `    <${param.xmlElement} id="0" name="">\n` +
          `      <UniversalPathList>${escXml(emitValue)}</UniversalPathList>\n    </${param.xmlElement}>`;
      }
    } else if (param.type === 'reference') {
      // Fixed-id built-ins (enumValues = id FM writes, hrEnumValues = name FM
      // displays) emit with their id: FM resolves some references by id alone on
      // paste (Install Menu Set's <CustomMenuSet>). Mirrors the reference.
      let name = unquote(hrValue);
      let fixedId = '';
      for (const k of Object.keys(param.hrEnumValues).sort()) {
        const label = param.hrEnumValues[k];
        if (label && ciEquals(label, name)) {
          fixedId = k;
          name = label;
          break;
        }
      }
      if (name !== '' || param.required) {
        const wa = rawStr(param.raw, 'wrapperAttr');
        const extra = wa ? ' ' + wa : '';
        const idAttr = fixedId ? ` id="${escXml(fixedId)}"` : '';
        piece = `    <${param.xmlElement}${idAttr} name="${escXml(name)}"${extra}/>`;
      }
    } else if (param.type === 'layout') {
      if (param.discriminator) {
        if (layoutPiece.has(pi)) piece = layoutPiece.get(pi)!;
      } else {
        const layoutName = unquote(hrValue);
        if (layoutName === '' || ciEquals(layoutName, '<unknown>')) {
          piece = '    <Layout id="0" name=""/>';
        } else {
          const resolved = resolver.resolveLayout(layoutName);
          if (resolved.id === 0 && resolved.name) {
            piece = `    <Layout name="${escXml(resolved.name)}"/>`;
          } else {
            piece = `    <Layout id="${resolved.id}" name="${escXml(resolved.name)}"/>`;
          }
        }
      }
    } else if (param.type === 'script') {
      if (!param.omitWhenEmpty || trim(hrValue)) {
        // Cross-file reference (Perform Script id 1): FM renders a call into
        // another file as the positional token `"NAME" from file: "FILE"`. When
        // the catalog opts this script param into the from-file grammar
        // (`fromFileElement` set), split that infix clause into a
        // <FileReference name="FILE"> sibling emitted BEFORE the <Script>,
        // carrying a <UniversalPathList>file:FILE</> child, and emit the <Script>
        // NAME-ONLY. FM binds the reference by the external-data-source NAME when
        // one of that name exists in the destination — the path is then ignored
        // (even a wrong path binds, no dialog), so an in-solution cross-file call
        // never regresses. When the destination does NOT yet reference FILE, the
        // child preserves the file name (`from file: "FILE"`) and lets FM raise
        // its normal locate prompt (fail-loud) instead of a name-only reference's
        // silent `from file: ""` drop. The path uses the display NAME — the only
        // value available offline; name-binding makes the real filename moot for
        // resolvable references. The <Script> stays name-only: its binding id
        // lives in the OTHER file (absent from the current-file context), so
        // resolving it would bind the WRONG same-file script of the same name; FM
        // shows it <unknown> until rebound while the file + name survive intact.
        const fromFileElement = rawStr(param.raw, 'fromFileElement');
        let fileName = '';
        let scriptToken = hrValue;
        if (fromFileElement) {
          const marker = ' from file: ';
          const fp = hrValue.indexOf(marker);
          if (fp !== -1) {
            scriptToken = trim(hrValue.slice(0, fp));
            fileName = unquote(trim(hrValue.slice(fp + marker.length)));
          }
        }
        const scriptName = unquote(scriptToken);
        if (fileName) {
          const scriptXml = `    <Script name="${escXml(scriptName)}"/>`;
          piece =
            `    <${fromFileElement} name="${escXml(fileName)}">\n` +
            `      <UniversalPathList>file:${escXml(fileName)}</UniversalPathList>\n    </${fromFileElement}>\n` +
            scriptXml;
        } else {
          const resolved = resolver.resolveScript(scriptName);
          piece = `    <Script id="${resolved.id}" name="${escXml(resolved.name)}"/>`;
        }
      }
    } else if (param.type === 'text' || param.type === 'name') {
      if (!param.omitWhenEmpty || trim(hrValue)) {
        const emitValue = textParamValue(param, hrValue);
        const attrs = elementAttr.get(param.xmlElement) ?? '';
        piece = `    <${param.xmlElement}${attrs}>${escXml(emitValue)}</${param.xmlElement}>`;
      }
    }
    // complex + unhandled types yield no piece.

    if (piece === '') continue;

    // Reconcile the open-wrapper stack with this param's parentElement path.
    reconcileWrappers(splitPath(param.parentElement ?? ''));

    xml += piece + '\n';
    prevWasTextElement = isTextElement;
  }

  reconcileWrappers([]);

  xml += '  </Step>';
  return xml;
}
