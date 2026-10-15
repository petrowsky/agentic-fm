/**
 * TypeScript interfaces for the step catalog.
 * The catalog itself lives at agent/catalogs/step-catalog-en.json
 * and is the universal format consumed by all environments.
 */

export interface StepCatalogEntry {
  /** Step name as shown in FileMaker (e.g. "Go to Portal Row") */
  name: string;
  /** Numeric step ID from FileMaker (null if unknown) */
  id: number | null;
  /** Category derived from snippet_examples subdirectory */
  category: string;
  /** Relative path to snippet_examples file (e.g. "navigation/Go to Portal Row.xml") */
  snippetFile: string;
  /** True if the step has no child elements (emits <Step .../>) */
  selfClosing: boolean;
  /** Structured parameter definitions in XML child element order */
  params: StepParam[];
  /** Human-readable bracket format (null = not yet defined). The Monaco editor
   *  derives its completion snippet (tab-stops + block scaffold) from this — see
   *  completion.ts::deriveSnippet. The former per-step `monacoSnippet` field was
   *  retired in favour of this single source. */
  hrSignature: string | null;
  /** Block pairing info for matched steps like If/End If */
  blockPair: StepBlockPair | null;
  /** Catalog entry status: auto-generated, human-reviewed, or complete */
  status: 'auto' | 'reviewed' | 'complete';
  /** Link to Claris help documentation */
  helpUrl: string | null;
  /** Minimum FileMaker version of the step (see FmMinVersion). Absent = unknown. */
  fmMin?: FmMinVersion;
  /** How `fmMin` is known. Present exactly when `fmMin` is. */
  fmMinSource?: FmMinSource;
}

/**
 * Version data. A version ("MAJOR", "MAJOR.MINOR" or "MAJOR.MINOR.PATCH",
 * major not zero, e.g. "21.0") is the first FileMaker version that has the
 * item; "any" means every FileMaker version the version data covers has it;
 * "unknown" means looked at and still undecided (or a retracted value). An
 * absent `fmMin` also means unknown. Read by version checks only, never by
 * the converters.
 *
 * The union is documentation only: `'any' | 'unknown' | string` collapses to
 * `string` for the type checker, which therefore accepts any text. Validate a
 * value before comparing it as a version.
 */
export type FmMinVersion = 'any' | 'unknown' | string;

/** "measured" = observed on a running FileMaker; "documented" = Claris documentation. */
export type FmMinSource = 'measured' | 'documented';

/** One `fmMinValues` entry: the minimum version of one enum value. */
export interface FmMinValue {
  fmMin: FmMinVersion;
  fmMinSource: FmMinSource;
}

/**
 * All parameter type classifications observed across the canonical 216-step
 * catalog. Kept as a named union so it stays deliberately parallel to the Python
 * KNOWN_PARAM_TYPES set in agent/scripts/catalog_grammar.py — a type added to one
 * port is obviously missing from the other.
 */
export type ParamType =
  | 'boolean'
  | 'flagBoolean'
  | 'flagElement'
  | 'enum'
  | 'text'
  | 'name'
  | 'calculation'
  | 'calc'
  | 'namedCalc'
  | 'field'
  | 'fieldOrVariable'
  | 'fieldList'
  | 'layout'
  | 'script'
  | 'table'
  | 'tableOccurrence'
  | 'tableRef'
  | 'tableReference'
  | 'reference'
  | 'fileReference'
  | 'attrGroup'
  | 'repeatGroup'
  | 'bitmaskGroup'
  | 'findRequests'
  | 'parametersList'
  | 'complex';

/**
 * One branch of a `discriminatorValues` map (keyed by the governing enum value).
 * `reveal` lists sibling ParamKeys that become live for the branch; `hrToken`
 * substitutes a fixed HR string for the slot; `labeled` marks a branch whose
 * revealed params render with their HR labels rather than positionally.
 */
export interface DiscriminatorValue {
  hrToken?: string;
  reveal?: string[];
  labeled?: boolean;
}

/** Gate: the param renders only when `param` holds one of `values`. */
export interface VisibleWhen {
  param: string;
  values: string[];
}

/** One entry of `hrLabelWhen` — swap the HR label when a sibling param matches. */
export interface HrLabelRule {
  param: string;
  values: string[];
  hrLabel?: string;
}

/**
 * A member of an `attrGroup` / `repeatGroup` `fields` list. `kind` is `"attr"`
 * (an XML attribute on the group element) or `"calc"` (a nested `<Calculation>`).
 */
export interface AttrGroupField {
  key: string;
  kind?: 'attr' | 'calc';
  xmlAttr?: string;
  defaultValue?: string;
}

/** One flag within a `bitmaskGroup` param (see StepParam.bitmask* fields). */
export interface BitmaskFlag {
  hrLabel: string;
  xmlAttr: string;
  bit: number;
}

export interface StepParam {
  /** XML element name (e.g. "SelectAll", "RowPageLocation", "Calculation") */
  xmlElement: string;
  /** Parameter type classification */
  type: ParamType;
  /** HR label prefix (e.g. "Select") — null means positional */
  hrLabel: string | null;
  /** XML attribute name for boolean/enum (e.g. "state", "value") */
  xmlAttr?: string;
  /** Valid values for enum parameters */
  enumValues?: string[];
  /** HR enum labels mapped to XML state values (e.g. { "True": "Off", "False": "On" }) */
  hrEnumValues?: Record<string, string>;
  /**
   * P7.2: the enum values FileMaker renders NO HR token for. `hrHidden`
   * suppresses a param in every state; this suppresses it in some. FileMaker
   * shows the companion such a value reveals instead of the value itself, so
   * the value is read back from that companion's `visibleWhen` gate rather than
   * from a token of its own. Emit is unaffected -- only HR rendering.
   */
  hrHiddenValues?: string[];
  /** When true, the HR label is inverted from the XML attribute value */
  invertedHr?: boolean;
  /** Rendering style for enum values (e.g. bare token vs labeled) */
  enumStyle?: string;
  /** Boolean rendered as a bare flag token when set (Close Window "Current file", etc.) */
  flagStyle?: boolean;
  /** Parent element name for namedCalc parameters */
  wrapperElement?: string;
  /** Enclosing element the param nests under, when not a direct Step child */
  parentElement?: string;
  /** HR slot index — overrides catalog order for HR rendering (see `hrParamOrder`) */
  hrSlot?: number;
  /** When true, the param is hidden from HR but still emitted to XML (attrGroup defaults) */
  hrHidden?: boolean;
  /** When true, a labeled-type param (namedCalc) FileMaker prints bare/positional:
   *  rendered without a "Label: " prefix and parsed by position (Show Custom Dialog's
   *  Title/Message). Set only where FileMaker renders bare, verified against live FM. */
  hrBare?: boolean;
  /** Inverse of `hrBare`: a `calc`/`field`/`script`, step-level `text`/`name` or legacy
   *  `calculation` param FileMaker renders WITH its
   *  `hrLabel` prefix (and parsed by that label), even though those types print bare by
   *  default. FileMaker labels the same value type per step (Perform Script's name is
   *  bare; Configure Region Monitor Script's is `Script: "…"`; Delete File's path is
   *  `Target file: “…”`). Needs a non-empty
   *  hrLabel; set only where FileMaker renders the label, verified against live FM. */
  hrLabeled?: boolean;
  /** A literal-text param (`text`/`name`) FileMaker displays wrapped in quotes (Insert
   *  Text's `… ; “x” ]`). XML→HR writes the value as ONE text literal (`quoteCalcLiteral`:
   *  embedded `"`/`“`/`”` backslash-escaped) so the line parser reads it back as one
   *  token whatever quotes, `;` or `]` it holds; HR→XML decodes it. An empty value
   *  renders no token, as FileMaker does. Set only where FM quotes the value (verified live). */
  hrQuoted?: boolean;
  /** The delimiter an hrQuoted param is written with: unset (default) is FileMaker's
   *  curly `“…”`; `"straight"` is `"…"`, where FileMaker shows the literal
   *  straight-quoted (Perform AppleScript's text). */
  hrQuoteStyle?: string;
  /** Governing discriminator: the separator FileMaker draws BETWEEN the labeled
   *  companions a value reveals when it is not the step's ` ; ` (Insert from Device:
   *  `Camera: Back; Resolution: Full; Flash: Auto`). Render-only. */
  hrRevealSeparator?: string;
  /** Governing enum branches: enum value → what it reveals / how it renders */
  discriminatorValues?: Record<string, DiscriminatorValue>;
  /** String form: names the sibling element that governs this param's shape */
  discriminator?: string;
  /**
   * Engine-computed governing value for a discriminated param. Not catalog data —
   * the grammar engine (P6.2/P6.3) sets it while resolving a discriminator; declared
   * here so both ports share the field name.
   */
  govDiscrimValue?: string;
  /** Gate controlling whether this param renders (sibling-value dependent) */
  visibleWhen?: VisibleWhen;
  /** Conditional HR-label overrides driven by a sibling param's value */
  hrLabelWhen?: HrLabelRule[];
  /** Suppress emission when the value is empty/default */
  omitWhenEmpty?: boolean;
  /** Emit the empty default element even when the source omits the value */
  emitEmptyDefault?: boolean;
  /** attrGroup / repeatGroup member definitions */
  fields?: AttrGroupField[];
  /** bitmaskGroup: XML attribute carrying the style name */
  bitmaskStyleAttr?: string;
  /** bitmaskGroup: XML attribute carrying the packed value */
  bitmaskValueAttr?: string;
  /** bitmaskGroup: attribute name of the resize flag */
  bitmaskResizeFlag?: string;
  /** bitmaskGroup: base value the flag bits are added to */
  bitmaskBase?: number;
  /** bitmaskGroup: bit that is always set */
  bitmaskFixedBit?: number;
  /** bitmaskGroup: ordered attribute emission list */
  bitmaskAttrOrder?: string[];
  /** bitmaskGroup: individual flag definitions */
  bitmaskFlags?: BitmaskFlag[];
  /** bitmaskGroup: named style presets */
  bitmaskStyles?: unknown;
  /** Whether this parameter is required */
  required: boolean;
  /** Default value from the snippet template */
  defaultValue?: string;
  /** Human-readable description of the parameter */
  description?: string;
  /** Minimum FileMaker version of the element/attribute this param models. Absent = unknown. */
  fmMin?: FmMinVersion;
  /** How `fmMin` is known. Present exactly when `fmMin` is. */
  fmMinSource?: FmMinSource;
  /** Enum params: minimum FileMaker version per XML value (keys are `enumValues` entries). */
  fmMinValues?: Record<string, FmMinValue>;
}

export interface StepBlockPair {
  /** Role of this step in the block */
  role: 'open' | 'close' | 'middle';
  /** Partner step names */
  partners: string[];
}

// ---------------------------------------------------------------------------
// Shared intermediate representation (IR) — the in-memory shape every converter
// reads or writes, so one grammar engine can serve all four directions. Kept
// deliberately parallel to the Python IR in agent/scripts/catalog_grammar.py.
// ---------------------------------------------------------------------------

/**
 * The param-key rule: a `namedCalc` param keys off its `wrapperElement`; every
 * other param keys off its `xmlElement`. Every namedCalc shares
 * `xmlElement === "Calculation"`, so the wrapper is what disambiguates them.
 * Matches the reference converter's param-key rule.
 */
export function paramKey(param: StepParam): string {
  if (param.type === 'namedCalc' && param.wrapperElement) {
    return param.wrapperElement;
  }
  return param.xmlElement;
}

/** A tagged union of the concrete values a parameter can hold in a StepInstance. */
export type Value =
  | { kind: 'absent' }
  | { kind: 'scalar'; text: string }
  | { kind: 'calc'; text: string }
  | { kind: 'field'; table?: string; id?: number; name?: string }
  | { kind: 'ref'; id?: number; name?: string }
  | { kind: 'list'; items: Value[] }
  | { kind: 'group'; attrs: Record<string, string> };

/** Sentinel for a param not present in the source (distinct from an empty scalar). */
export const ABSENT: Value = { kind: 'absent' };

/**
 * The shared in-memory shape every converter reads or writes. `values` is keyed
 * by param key (see `paramKey`); a param absent from the source is either omitted
 * or mapped to `ABSENT`.
 */
export interface StepInstance {
  name: string;
  id: number;
  enable: boolean;
  values: Record<string, Value>;
}
