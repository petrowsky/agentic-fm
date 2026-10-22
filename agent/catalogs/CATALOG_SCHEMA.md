# Step Catalog Schema Contract

This document is the **normative contract** for the structure of `step-catalog-en.json` (and any language variant such as `step-catalog-<lang>.json`).
The catalog is a machine-readable index of every FileMaker script step and the parameters each step accepts.
Multiple independent tools consume this file to convert between human-readable (HR) script text and `fmxmlsnippet` XML, to validate scripts, and to drive editor tooling.
This contract exists so those consumers agree on exactly what each field means.

## Scope and neutrality rule

This document describes **only** the catalog file and the FileMaker `fmxmlsnippet` XML it models.

> It must not reference any consumer's implementation — no source files, modules, functions, endpoints, runtime behavior, or product names of any tool that reads the catalog.

The rule is **symmetric**: it names neither converter, linter, editor, nor any other reader on any side.
Each consumer documents how it uses the catalog in its own repository.
Keeping this file implementation-neutral is what makes it a shared contract rather than one tool's documentation.
The catalog is published from a single source-of-truth repository; downstream consumers sync the file from there and should pin to a specific revision so a schema change is a deliberate, reviewable update.

## Top-level structure

The file is a JSON array of **step entries**.
Each entry is an object with the following top-level keys.

| Key             | Type           | Required | Meaning                                                                                                                                                                                                 |
| --------------- | -------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`          | string         | yes      | The step's display name, exactly as FileMaker shows it (e.g. `"Set Variable"`, `"# (comment)"`). The primary lookup key.                                                                                |
| `id`            | integer        | yes      | FileMaker's internal step id, emitted as `<Step id="N">`. May be `0` for steps whose id FileMaker reassigns on paste.                                                                                   |
| `category`      | string         | yes      | The step's palette category (e.g. `"control"`, `"navigation"`). Grouping/discovery aid only.                                                                                                            |
| `selfClosing`   | boolean        | yes      | `true` → the step emits as `<Step ... />` with no children; `false` → `<Step ...>...children...</Step>`.                                                                                                |
| `params`        | array          | yes      | Ordered list of parameter objects. See **Parameter objects**. May be empty.                                                                                                                             |
| `hrSignature`   | string \| null | yes      | A human-readable template showing the HR rendering of the step's parameters. A display/authoring hint, **not** a parse grammar. The Monaco editor derives its tab-completion snippet (ordered tab-stops + block scaffold) from this — see `webviewer/src/editor/language/completion.ts` `deriveSnippet`. |
| `blockPair`     | object \| null | yes      | Non-null only for steps that open/close a block (If/End If, Loop/End Loop, etc.). See **Block pairs**.                                                                                                  |
| `status`        | string         | yes      | Maintenance state of the entry: `"complete"`, `"auto"`, or `"unfinished"`. Consumers should treat only `"complete"` entries as fully reliable.                                                          |
| `helpUrl`       | string         | yes      | Link to the step's official documentation. Reference only.                                                                                                                                              |
| `snippetFile`   | string         | no       | Path, relative to the catalog-maintenance corpus, of the reference XML example the entry was derived from. **Maintenance provenance only** — not part of the runtime contract; consumers may ignore it. |
| `notes`         | string         | no       | Free-text behavioral notes scoped to the whole step (as opposed to a single param).                                                                                                                     |
| `fmMin`         | string         | no       | The first FileMaker version that has the step, `"any"` or `"unknown"`. See **Version data**. Absent means unknown.                                                                                      |
| `fmMinSource`   | string         | with `fmMin` | How `fmMin` is known: `"measured"` or `"documented"`. See **Version data**.                                                                                                                         |

### Block pairs

```json
"blockPair": { "role": "close", "partners": ["Open Transaction"] }
```

- `role` — one of `"open"`, `"middle"`, `"close"`.
- `partners` — array of step names that complete the block.

## Parameter objects

`params` is an **ordered** array.
The order is normative.

> **Param ordering is XML serialization order.**
> Parameters MUST appear in the order FileMaker serializes the corresponding child elements/attributes within the step's XML.
> Consumers emit and read parameters positionally against this order, so it is what guarantees HR↔XML round-trip fidelity.
> `hrSignature` separately conveys how parameters are arranged for a human reader; the two orders may differ, and `hrSignature` is never used to determine emission order.

Every parameter object has these base keys:

| Key          | Type           | Required | Meaning                                                                                                                                                                       |
| ------------ | -------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `xmlElement` | string         | yes      | The XML element (or `Element/@attr` path) this parameter maps to. See **Element-path forms**.                                                                                 |
| `type`       | string         | yes      | The parameter's structural classification. See **Parameter types**.                                                                                                           |
| `hrLabel`    | string \| null | yes      | The label shown in HR (e.g. `"Target"`). `null` means the value is positional/inline with no label.                                                                           |
| `required`   | boolean        | usually  | Whether FileMaker always serializes this element, even when empty/default. A required param round-trips even at its default value; an optional one may be omitted when unset. |

### Element-path forms

- `"xmlElement": "Calculation"` — a child element named `Calculation`.
- `"xmlElement": "UniversalPathList/@type"` — the `type` **attribute** on the `UniversalPathList` element (used when one element carries both text content and an attribute that are modeled as separate params).

### Common optional keys

| Key              | Applies to               | Meaning                                                                                                                         |
| ---------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `xmlAttr`        | `boolean`, `enum`, flags | Name of the attribute carrying the value (e.g. `"state"`, `"value"`, `"type"`).                                                 |
| `defaultValue`   | most                     | The value FileMaker writes when the param is at its default. Used to decide omission and to fill required-but-empty elements.   |
| `wrapperElement` | `namedCalc`              | The element that wraps a `<Calculation>` child (e.g. `<Count><Calculation/></Count>`).                                          |
| `wrapperAttr`    | `namedCalc`              | A literal attribute string FileMaker places on the wrapper (e.g. `custom="True"`), emitted verbatim.                            |
| `parentElement`  | any nested param         | The ancestor element this param nests inside (e.g. a calc that lives under `<Source>` or under a step-named container element). |
| `notes`          | any                      | Free-text behavioral notes for this param. **Canonical key is `notes`.** See **Known inconsistencies**.                         |
| `fmMin` / `fmMinSource` | any               | The first FileMaker version that has the element or attribute this param models, and how that is known. Absent: inherits the step's. See **Version data**. |

## Parameter types

Types fall into four groups.
Each is defined by the XML it produces.

### Scalar and calculation types

| `type`        | XML shape                                                        | Notes                                                                                                  |
| ------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `boolean`     | `<El xmlAttr="True\|False"/>`                                    | Two-state attribute. `invertedHr` may flip the HR sense.                                               |
| `enum`        | `<El xmlAttr="value"/>` or `<El>value</El>`                      | Choice value. See **The enum key family**.                                                             |
| `calculation` | `<Calculation><![CDATA[expr]]></Calculation>`                    | A bare calculation child of the step.                                                                  |
| `calc`        | `<Calculation><![CDATA[expr]]></Calculation>`                    | A calculation that nests inside a `parentElement` wrapper rather than sitting directly under the step. |
| `namedCalc`   | `<Wrapper><Calculation><![CDATA[expr]]></Calculation></Wrapper>` | Calculation wrapped by `wrapperElement` (optionally with `wrapperAttr`).                               |
| `text`        | `<El>literal text</El>`                                          | Literal element text content.                                                                          |

```json
{
  "xmlElement": "Count",
  "type": "namedCalc",
  "hrLabel": "Amount (bytes)",
  "wrapperElement": "Count"
}
```

### Reference types

These resolve to FileMaker objects.
Field/script/layout/table identity (ids, names, table-occurrence attributes) is provided by the consumer's resolution context; this contract only specifies the element shape.

| `type`                                          | XML shape                                                                                                                                     |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `field`                                         | `<Field table="TO" id="N" name="FieldName"/>`                                                                                                 |
| `fieldOrVariable`                               | Dual form: a field ref `<Field table="" id="0" name=""/>`, **or** a variable `<Field>$var</Field>` accompanied by a leading `<Text/>` marker. |
| `script`                                        | `<Script id="N" name="ScriptName"/>`                                                                                                          |
| `layout`                                        | `<Layout id="N" name="LayoutName"/>` — shape may be governed by a `discriminator` (see below).                                                |
| `tableOccurrence`, `tableRef`, `tableReference` | A `<Table ...>` reference. (Three spellings exist for historical reasons — see **Known inconsistencies**.)                                    |
| `fileReference`                                 | `<FileReference>...</FileReference>` — a file path/reference element.                                                                         |
| `reference`                                     | A generic name-only object reference FileMaker re-resolves by name on paste (e.g. a custom menu set). Built-in members FileMaker resolves by id instead are listed as `enumValues` (the id FileMaker writes) + `hrEnumValues` (the name it displays) and emit with their id. |

### Structured (container) types

These model a subtree.
Their internal grammar is given by a `fields` array and/or the entry keys below.
See **Nested field grammar**.

| `type`           | Shape                                                                                                                                                                                             | Key extra fields                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `attrGroup`      | One container element carrying a set of attributes and/or child elements.                                                                                                                         | `fields[]`                                                                                     |
| `fieldList`      | An ordered list of field entries (each an entry element or a bare `<Field>`), optionally with a per-entry attribute.                                                                              | `entryElement`, `entryAttr`, `entryAttrDefault`, `fieldWrapper`, `fieldFixedAttrs`, `fields[]` |
| `repeatGroup`    | A bounded, repeated entry element each carrying attributes and/or a calc.                                                                                                                         | `entryElement`, `fields[]`                                                                     |
| `parametersList` | A `Count`-attributed container with one `<P><Calculation/></P>` child per item; `Count` must equal the child count.                                                                               | —                                                                                              |
| `tableList`      | A container holding `<Table id="" name=""/>` children.                                                                                                                                            | `parentElement`                                                                                |
| `findRequests`   | The find-request `<Query>` subtree. Its internal grammar is defined in the sibling `find-requests.md` spec.                                                                                       | —                                                                                              |
| `complex`        | **Legacy/escape hatch.** Structure not modeled in the schema; the consumer must derive it from `notes` or the reference example. Prefer migrating these to `attrGroup`/`fieldList`/`repeatGroup`. |

### Presence-flag types

| `type`        | Meaning                                                                                                                                                 |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `flagElement` | An empty marker element: **present = on, absent = off** (e.g. `<Overwrite/>`).                                                                          |
| `flagBoolean` | A `boolean` whose HR rendering is flag-style — shown only when "on", omitted otherwise — but which still serializes as an attribute (`xmlAttr`) in XML. |

`flagStyle: true` may also appear on an `enum` to mark that the HR shows the value only for the non-default case (and hides the default), while XML always carries the attribute.

`flagStyle: true` on a `boolean` is the machine-readable form of `flagBoolean`, and it is what the converter actually reads — describing the behavior only in `notes` leaves the parser looking for a `Label: value` token that the bare label never matches, so the orphaned label is handed to the next positional param instead.
For such a param the HR **presence is the value**: label present = `True`, label absent = `False`.
`defaultValue` documents FileMaker's default for a freshly added step and must never answer for an absent flag — otherwise a flag defaulting to `True` cannot be switched off from HR at all.
With `invertedHr: true` as well, the flag is drawn when the attribute is `False`: label present = `False`, label absent = `True` (Convert File's `Open File` is drawn when `Option` is `False`).

A param FileMaker renders with **no** HR token needs `hrHidden: true` for the same reason: without it the converter emits a stray bare `On`/`Off` and the param can swallow a positional token belonging to a real one.
Whether a given boolean renders bare, labeled, or not at all is a per-step FileMaker fact — confirm it against FileMaker's own rendering rather than inferring it from `params[]`.

## The enum key family

An `enum` (or `enum`-like) param may carry several value lists with distinct roles.
Define them precisely:

| Key             | Meaning                                                                                                                                                                                      |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enumValues`    | The canonical value set. **These are the literal XML values** unless `xmlEnumValues` is also present.                                                                                        |
| `xmlEnumValues` | The literal XML values, when they differ from the human-facing `enumValues`. When present, this is authoritative for what XML carries.                                                       |
| `hrEnumValues`  | A map from XML value → HR display string (e.g. `{ "Current": "Current Window" }`).                                                                                                           |
| `enumStyle`     | How the value is carried. `"text"` means the value is the element's **text content** (`<El>value</El>`); absence (with an `xmlAttr`) means it is an **attribute** (`<El xmlAttr="value"/>`). |
| `fmMinValues`   | A map from XML value → `{ "fmMin": …, "fmMinSource": … }`: the first FileMaker version that accepts that value. Keys must be members of `enumValues`. See **Version data**.                               |

> **Authoring requirement:** `enumValues`/`xmlEnumValues` must be the exact literals FileMaker writes to XML (e.g. `LayoutNameByCalc`, not the dialog label `Layout Name by Calc`).
> Human-facing labels belong in `hrEnumValues`/`hrLabel`, never substituted for the XML literal.

```json
{
  "xmlElement": "Window",
  "type": "enum",
  "xmlAttr": "value",
  "defaultValue": "ByName",
  "enumValues": ["ByName", "Current"],
  "hrEnumValues": { "ByName": "ByName", "Current": "Current Window" }
}
```

## Nested field grammar

Structured types (`attrGroup`, `fieldList`, `repeatGroup`) describe their subtree with a `fields` array.
Each field object uses `key` + `kind`:

| `kind`            | Produces                                                   | Relevant keys             |
| ----------------- | ---------------------------------------------------------- | ------------------------- |
| `attr`            | An attribute on the container/entry element.               | `xmlAttr`, `defaultValue` |
| `calc`            | A `<Calculation>` child, optionally inside `childElement`. | `childElement`            |
| `text`            | Literal text content, optionally inside `childElement`.    | `childElement`            |
| `field`           | A `<Field>` reference.                                     | —                         |
| `fieldOrVariable` | The dual field/variable form (see reference types).        | —                         |
| `script`          | A `<Script>` reference.                                    | —                         |
| `group`           | A nested child element with its own `fields[]`.            | `element`, `fields[]`     |

Field-object keys:

| Key            | Meaning                                                                 |
| -------------- | ----------------------------------------------------------------------- |
| `key`          | Stable identifier for the field (HR token name / internal handle).      |
| `kind`         | One of the kinds above.                                                 |
| `xmlAttr`      | Attribute name when `kind: "attr"`.                                     |
| `defaultValue` | Default for the field. On a `calc` / `text` field, the value the group's default element holds (read only by `hrOmitAtDefault`). |
| `childElement` | Wrapping element for `calc`/`text` kinds (empty string = direct child). |
| `element`      | Element name when `kind: "group"`.                                      |
| `fields`       | Nested field list when `kind: "group"`.                                 |
| `requireAttr`  | Marks an attribute FileMaker always serializes.                         |
| `optional`     | Marks a field that may be absent.                                       |

`fieldList`-specific entry keys:

| Key                | Meaning                                                                                             |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| `entryElement`     | The wrapper element per entry (e.g. `ExportEntry`, `Sort`). Absent → entries are bare `<Field>`.    |
| `entryAttr`        | An attribute placed on each entry (e.g. `map`, `GroupByFieldIsSelected`, `type`).                   |
| `entryAttrDefault` | Default for `entryAttr`.                                                                            |
| `fieldWrapper`     | An element wrapping the `<Field>` inside each entry (e.g. `<PrimaryField><Field/></PrimaryField>`). |
| `fieldFixedAttrs`  | Fixed attributes FileMaker always writes on each entry's `<Field>` (e.g. `FieldOptions="0"`).       |

```json
{
  "xmlElement": "TargetFields",
  "type": "fieldList",
  "hrLabel": "Import fields",
  "entryAttr": "map",
  "entryAttrDefault": "Import",
  "fieldFixedAttrs": [
    {
      "key": "FieldOptions",
      "kind": "attr",
      "xmlAttr": "FieldOptions",
      "defaultValue": "0"
    }
  ]
}
```

## Cross-cutting modifier keys

These keys refine emission/round-trip behavior and may appear on the param types noted.

| Key                | Applies to                                        | Meaning                                                                                                                                                                                                                                             |
| ------------------ | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invertedHr`       | `boolean`                                         | The HR sense is the opposite of the XML attribute value (HR "on" ↔ XML `False`).                                                                                                                                                                    |
| `flagStyle`        | `enum`, attribute-style flags                     | HR shows the value only for the non-default case; XML always carries the attribute.                                                                                                                                                                 |
| `omitWhenEmpty`    | reference/optional params                         | The element is **not** emitted when unset (presence-driven), rather than emitted empty.                                                                                                                                                             |
| `emitEmptyDefault` | reference/optional params                         | The element **is** emitted even when empty/default, because FileMaker always serializes it.                                                                                                                                                         |
| `discriminator`    | a param whose shape/presence depends on a sibling | Names the sibling param (typically an `enum`) that governs this param's form. Example: a `Layout` param discriminated by `LayoutDestination` (none for current/original, an id/name ref for a selected layout, a calc child for the by-calc forms). |
| `textMarker`       | `fieldOrVariable`                                 | FileMaker prefixes the value with a leading empty `<Text/>` marker in both field-ref and variable forms.                                                                                                                                            |
| `hrLabelNote`      | any                                               | Free-text note explaining a conditional HR label (e.g. the label changes with another setting).                                                                                                                                                     |
| `hrSlot`           | any                                               | The param's position in the step's HR, for a step whose HR order differs from its XML order. When any param of a step sets it, HR renders and parses the params in `hrSlot` order (a param without one sorts by its catalog index) while XML keeps catalog order. Set it on every param of such a step. Show/Hide Toolbars draws `Lock` before `Include Edit Record Toolbar`; Convert File draws `Open File ; Skip Indexes ; With dialog` and the path before `Verify SSL Certificates` (FileMaker 26.0.3). |
| `hrPositionalKey`  | `repeatGroup`                                     | Names the entry `fields` key FileMaker prints bare and positionally, one token per non-empty entry, after the step's own bare params (Show Custom Dialog's input targets: `[ "T" ; "M" ; $a ; Invoices::Number ]`). Render uses that form when it loses nothing and the labeled body form otherwise; parse claims the leftover bare target tokens (a `$variable` or a `Table::Field`). |
| `hrDefault`        | `repeatGroup`                                     | The HR body of FileMaker's default content (Show Custom Dialog's OK / Cancel buttons). Render omits the token when the body equals it; emit writes it when the HR carries no token. |
| `repeatCount`      | `repeatGroup`                                     | FileMaker always serializes this many entries, padding with empty ones; emit pads to match. |
| `groupFields`      | `enum` sharing an `attrGroup`'s `xmlElement`      | The param owns no element: it is a display projection of these top-level fields (`attr` / `text`) of the sibling `attrGroup` with the same `xmlElement`, shown as a token of its own with the param's `hrLabel` / `hrEnumValues` / `hrSlot` / `visibleWhen`. Several fields join with `\|` in field order (`BlankRecord\|WithBoxes`, displayed `Blank record, with boxes`). The group's own token leaves out the fields a visible projection shows; a projection's value sets those fields of the group. A `flagStyle` projection prints its bare `hrLabel` for the non-default value and reads that label back as it (Replace Field Contents' `Skip auto-enter options` is `SerialNumbers#PerformAutoEnter` False, gated on Restore). Its param key is `<xmlElement>#<first field>` (`PDFOptions#PDFSaveType`). |
| `fmLineHidden`     | any                                               | FileMaker stores the content but never draws it in its Script Workspace line (Export Records' `Profile`, Save Records as Excel's `Worksheet`). An HR form that mirrors FileMaker's line leaves it out; a lossless HR form still prints it, labeled. |
| `hrOmitAtDefault`  | `attrGroup`                                       | FileMaker draws nothing for the group (Speak's `SpeechOptions`, Create PDF's `Document` / `Security` / `View`). XML → HR prints no token while the element equals its catalog default: every non-optional attr at its `defaultValue`, every optional attr absent, a `calc` / `text` field holding its `defaultValue` when it has one and absent otherwise, every sub-group present and at default. HR → XML with no token writes that default element. Off its default the group prints its whole body. |
| `hrEmptySlotWith`  | positional params                                 | Names the param key of a sibling whose presence FileMaker draws as this param's empty slot when this param is empty, wherever the slot sits (Export Records with no path but a `<Profile>`: `Create folders: On ; ; Unicode (UTF-8)`). An empty token read at that slot means the sibling is present (an `attrGroup` with its defaults). |
| `hrNoTableBlank`   | `field`, `fieldOrVariable`                        | FileMaker draws a stored reference with no table and no field (`<Field table="" id="0" name=""/>`) as nothing in this slot. Every other single-field param draws it `<Table Missing>`, and HR prints the same; the sentinel reads back to the empty reference. Set on Fine-Tune Model's Completion Field, Perform Semantic Find's Save result and Generate Response from Model's Messages / Tool Calls (FileMaker 26.0.3). An absent `<Field>` draws no field token on any param. |
| `fmLineHiddenWhen` | any                                               | Like `fmLineHidden`, but only while every condition in the list holds; each condition is `{ "param": <param key>, "values": [...] }` (that sibling holds one of these raw values, its default when unset) or `{ "param": <param key>, "empty": true }` (that sibling has no value: no element, or an empty one). With no path, Save Records as PDF and four sibling steps draw no `Automatically open` / `Create email` although they keep them on; with Restore off too, Save Records as PDF draws no records source. A `reference` sibling's raw value is its `id` (Install Menu Set draws no `Use as file default` with [File Default], `<CustomMenuSet id="0"/>`). A `flagElement` sibling's value is `True` when its element is present and `False` otherwise (Set AI Call Logging draws no `Truncate Messages` while Verbose is off). Only an HR form that mirrors FileMaker's line applies it. |
| `rosterKind`       | `reference`                                       | The kind of named object the reference names in the target file (`CustomMenuSet`). Set where FileMaker silently swaps a name the file does not have on paste (a custom menu set name it lacks becomes [File Default]), so a consumer that knows the file's objects of that kind can check the name and report one the file lacks before it is pasted. Built-ins listed in `hrEnumValues` are not checked. |
| `fmLineToken`      | an `hrHidden` `boolean` that a sibling's `visibleWhen` gates | The bare token FileMaker draws for the param when it is on (Sort Records' `Restore`: `Sort Records [ Restore ; With dialog: Off ]`). Its state is derived from whether a gated sibling has a value, so HR→XML reads the token as a no-op when that derived state is on (a `Sort:` is given) and leaves it unplaced otherwise; it is never taken as a positional value. The lossless XML→HR form does not print it; an HR form that mirrors FileMaker's line prints it, at the param's `hrSlot`, whenever the stored state is on (FileMaker draws `Restore` for every `<Restore state="True"/>`, with or without a sort order). |
| `fmLineBare`       | a labeled `namedCalc` or `boolean`                 | FileMaker draws the value unlabeled on its own line. A `namedCalc` is drawn after the step's other tokens and left out when empty (Get Folder Path's Dialog title and Default location: `Get Folder Path [ Allow Folder Creation ; $result ; "t" ; Get ( DocumentsPath ) ]`, FileMaker Pro 26.0.3). HR keeps the label. HR→XML reads the bare tokens left after the step's bare params as these params, in catalog order; an empty token keeps a param's slot. Fewer tokens than such params is FileMaker leaving an empty one out, which its line cannot place: the tokens fill the first params. A `boolean` is drawn in its own slot as its bare value, `On` / `Off` or its `hrEnumValues` label (`Set Layout Object Animation [ Off ]`, `Set Error Logging [ On ; Custom debug info: "dbg" ]`); HR→XML reads a bare token naming one of those values as the param when no `Label: value` token gave it one. |
| `fmLineSubscriptOf` | any param                                         | The param key of a sibling whose token FileMaker draws this value on, as a repetition subscript, nothing for 1 or empty (Get Folder Path's Repetition on its variable: `$result[2]`, `$result[$i]`). HR keeps this param labeled. HR→XML splits a trailing `[…]` off the sibling's token into this param when no labeled token gave it a value. |
| `fmLineOnOff`      | a `flagStyle` `boolean` or `flagElement`          | FileMaker draws the flag labeled with its state on its own line, `<hrLabel>: On` when set and `<hrLabel>: Off` when clear, instead of the bare label only when set (Set AI Call Logging's `Verbose: Off`, FileMaker Pro 26.0.3). HR keeps the bare flag; HR→XML already reads the labeled spelling. Only an HR form that mirrors FileMaker's line applies it. |

## Version data

`fmMin` records the first FileMaker version that has an item, and `fmMinSource` records how that is known.
The two keys always appear together, on four kinds of item:

| Item                         | Where the keys sit                                                                                         |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------- |
| a step                       | the step entry                                                                                             |
| a step option                | the parameter object (the element or attribute the parameter models was added in that version)             |
| one value of an `enum` param | the param's `fmMinValues` map, keyed by the XML value exactly as `enumValues` spells it                   |
| a calculation function       | the entry in `function-catalog.json` (see **The function catalog**); a `Get` selector is its own entry    |

The values below are illustrative, not data about a real step:

```json
{
  "name": "Example Step",
  "fmMin": "any",
  "fmMinSource": "measured",
  "params": [
    {
      "xmlElement": "Mode",
      "type": "enum",
      "hrLabel": "Mode",
      "fmMin": "21.0",
      "fmMinSource": "measured",
      "enumValues": ["Basic", "Extended"],
      "fmMinValues": { "Extended": { "fmMin": "22.0", "fmMinSource": "documented" } }
    }
  ]
}
```

Read: the step exists in every covered version, its `Mode` option from 21.0, and the `Extended` mode from 22.0.

**`fmMin` values.**

- A version: `"MAJOR"`, `"MAJOR.MINOR"` or `"MAJOR.MINOR.PATCH"`, one to three digits each, major not zero (`"21"`, `"21.0"`, `"22.0.2"`). The item exists in that FileMaker version and every later one.
- `"any"`: the item exists in every FileMaker version the version data covers, which is FileMaker Pro 19.6 and later.
  It says nothing about when the item was introduced.
- `"unknown"`: the item was looked at and its minimum is still undecided, or an earlier value was found wrong and withdrawn.
  `fmMinSource` says how that was concluded. A consumer reads it exactly as unknown; it is not malformed.
- Absent, on a step entry or a function entry: unknown. A consumer treats an unknown item as it would without version data.
- Absent, on a parameter object or for a value missing from `fmMinValues`: the option adds no minimum of its own and inherits the one above it (see **Combining**).

`"any"` is a value of its own rather than the oldest covered version (for example `"19.6"`), because a version would state that the item first appeared in that release.
That is false for nearly every item (`Abs` predates FileMaker 19 by decades), and it would refuse the item on any older FileMaker a consumer still meets.
It is a string rather than `null` or `""` so that "known, no minimum" cannot be confused with an absent key by a reader that tests for a value.
A minimum is never written as a negated list ("not in 19"): the data says where an item starts, and every later version is assumed to keep it, because FileMaker does not remove steps or functions.

**Correcting a value.**
Replace a wrong value with the right one, or with `"unknown"` when the right one is not known.
Never just delete the keys: a consumer that layers an updated catalog over an older copy keeps keys the update lacks, so a deleted value would survive there.

**`fmMinSource` values.**

| Value          | Meaning                                                                                                                       |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `"measured"`   | Observed on a running FileMaker: the item was tried on each installed version, and `fmMin` is derived from the oldest version that accepted it (see **Measured minimums**). |
| `"documented"` | Taken from Claris documentation (the "Originated in version" line of the help page, or the release notes), for an item that cannot be exercised directly, or that the documentation dates more finely or later than the measurement. |

**Measured minimums.**
A set of measured versions brackets each item's true first version: it lies after the newest version measured without the item and at or before the oldest version measured with it.
A measured `fmMin` names one value inside that bracket:

- the first shipped release of the earliest major measured to have the item, when no earlier measured version of that major lacked it (FileMaker Pro 20 first shipped as 20.1, so such a minimum is `"20.1"`, not `"20.0"`);
- otherwise the oldest version measured with it, as `MAJOR.MINOR` (or in full when the version measured without it has the same `MAJOR.MINOR`).

A `"documented"` value that falls inside the bracket refines the measurement and is kept when the data is measured again.
One outside the bracket is a disagreement between the documentation and FileMaker, and is recorded with its reason.

A step option (a parameter object) gets a measured minimum only from a read-back: its label is missing from the step FileMaker draws on every version measured below the minimum, and drawn on that version and every later one.
Where the option is an attribute, the copied-out XML corroborates the boundary: the attribute is missing below it and present on the newest version measured.
An option FileMaker draws on no measured version (a block's collapsed state, a hidden flag) has only the copied-out XML, and only a copy-out of a non-default value counts: paste the option set to a value other than its `defaultValue`, copy the step back out, and take the minimum as the one the bracket rules above give for the versions whose copy-out carries the attribute (dropped on every version measured below it, present on every version from the first one that has it, the newest included).
Present at another value than the one sent is still present: FileMaker has the option and normalized the value.
A copy-out of the option at its default value proves nothing, because FileMaker may omit a default.
An option the read-back cannot corroborate inherits the step's minimum.

**How a version check treats an option** (read only by a consumer that checks a script against a FileMaker version, never by the converters):

| Key | On | Meaning |
| --- | --- | --- |
| `fmMinOnOlder` | a param with `fmMin` | `"dropped"`: a FileMaker older than `fmMin` discards the option silently on paste and the step behaves the same (a block's collapsed state). A version check reports it as a warning, not a refusal. Absent: a version check refuses. |
| `fmGateWhen` | a param with version data | `{"param": "<sibling>", "values": ["…"]}`: the option counts as used only when the sibling param (named like `visibleWhen`: its `xmlElement`, or `wrapperElement` for a `namedCalc`) holds one of `values` (Send Mail's `OAuthProvider` only when `SendViaOAuthAuthentication` is `True`). Absent: the option counts whenever it is set. |

**Combining.**
A step option's minimum is the combination of the chain step → parameter → value:

1. The step's own `fmMin` is always part of the chain; absent there means unknown.
2. A parameter or value that declares `fmMin` adds it to the chain; one that declares nothing adds nothing (it inherits).
3. If every part of the chain is known, the option's minimum is the highest of them, with `"any"` lower than every version (all `"any"` means no minimum).
4. If any part is unknown (`"unknown"`, an absent step value, or malformed), the option's minimum is unknown, except that the option is certainly absent below the highest known part.

So a parameter never needs to repeat its step's minimum, and an option is never available on a version that lacks its step.

**Conformance.**

- `fmMinSource` is required whenever `fmMin` is present, and is absent otherwise.
- `fmMinValues` appears only on a parameter object, each entry is an object with both keys, and each key names a value in the param's `enumValues`.
- A malformed pair (wrong type, an unparseable version, a missing or unknown source) is a catalog defect.
  A consumer reads it as unknown and reports it; it never guesses a version.
- Version data never changes how a step converts between HR and XML.
  Converters ignore all three keys.

### The function catalog

`function-catalog.json` lists FileMaker's built-in calculation functions.
It is a JSON object:

| Key         | Type   | Meaning                                                                          |
| ----------- | ------ | -------------------------------------------------------------------------------- |
| `version`   | string | The FileMaker version the list was last reconciled against.                      |
| `functions` | array  | One entry per function, sorted by name without regard to case.                   |

Each entry:

| Key           | Type   | Required     | Meaning                                                                                                                      |
| ------------- | ------ | ------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `name`        | string | yes          | The function name, unique without regard to case. A `Get` selector is its own entry, named `Get(Selector)` with no spaces.   |
| `prototype`   | string | yes          | The call signature as Claris documents it (`Left ( text ; numberOfCharacters )`, `Get ( WindowUUID )`).                      |
| `fmMin`       | string | no           | As in **Version data**: the first FileMaker version that has the function or selector, `"any"` or `"unknown"`. Absent means unknown. |
| `fmMinSource` | string | with `fmMin` | `"measured"` or `"documented"`.                                                                                               |

An illustrative entry (not real data):

```json
{ "name": "ExampleFunction", "prototype": "ExampleFunction ( text )", "fmMin": "22.0", "fmMinSource": "documented" }
```

## Known inconsistencies (non-normative aliases)

The current catalog contains historical variants.
These are documented so consumers tolerate them; new and updated entries should converge on the canonical form.

- **`note` vs `notes`** — both appear as free-text annotation keys. **Canonical: `notes`.** Consumers should read both; authors should write `notes`.
- **`hrValues` vs `hrEnumValues`** — `hrValues` is a rare variant; **canonical: `hrEnumValues`** (XML→HR map) for label mapping, with `enumValues` for the value set.
- **`tableOccurrence` / `tableRef` / `tableReference`** — three type spellings for table references with the same `<Table>` shape. Consumers should treat them equivalently; no new spellings should be introduced.
- **`notesAnimation`** and other one-off step-level note keys — step-scoped free text; treat as `notes`-equivalent prose, not structured data.

A consumer encountering an **unknown** `type` or key MUST degrade gracefully: skip the parameter rather than fail the whole step.
This keeps the contract forward-compatible — new structured types can be introduced and older consumers keep working on the steps they understand.

## Changing this contract

1. The schema and the catalog change together; update this document in the same revision that introduces a new `type`, key, or grammar rule.
2. Keep the **neutrality rule** intact — describe the artifact, never a consumer.
3. Prefer migrating `complex` params to a structured type over adding new escape hatches.
4. Announce schema-affecting changes so downstream consumers can re-pin to the new revision.

## Related specs (same directory)

- `find-requests.md` — grammar for the `findRequests` `<Query>` subtree.
- `shared-enums.md`, `window-enums.md`, `animation-enums.md`, `language-enums.md` — enumerated value references shared across steps.
- `UPDATING_CATALOGS.md` — the maintenance workflow that keeps the catalog conformant to this contract.
