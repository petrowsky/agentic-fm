# Updating Step Catalogs

## Objective

Maintain 100% coverage of `agent/catalogs/step-catalog-en.json`, and any other language variation thereof, for all possible FileMaker script steps. The catalog is the **canonical HR (human-readable) reference** while `snippet_examples/` files are the **canonical XML reference**. There should be a 1:1 match for each entry within a catalog file and the files found within snippet examples.

Every entry must conform to the structural contract in [`CATALOG_SCHEMA.md`](./CATALOG_SCHEMA.md) — the normative definition of every `type`, key, and grammar rule the catalog uses. Consult it before adding or changing a parameter shape.

## The verification rule — non-negotiable

> **Every catalog change MUST be verified end-to-end against a live FileMaker, through every converter it feeds.**

The catalog is not a document; it is the program that drives HR→XML, XML→HR, and SaXML→XML at once. A one-word edit reshapes all of them, and each direction can be wrong in a way the others hide.

**Nothing short of live FileMaker counts as verification.** Specifically, none of these are evidence a change is correct:

- A green test suite. The goldens are generated from these same converters, so a golden agrees with whatever the converter happens to emit. A converter that has never been compared to FileMaker has only been compared to itself.
- An unchanged corpus diff. "The output did not change" is not "the output is correct" — it is equally consistent with the change never taking effect, and with the output having been wrong before and after. This has mis-sized two separate defects in this project.
- A reading of `snippet_examples/`. It is FileMaker's own output and is the best **offline** oracle, so use it to *design* a change — but it is a fixed sample, and it cannot show you what FileMaker does with a value you invented.
- Agreement with the existing catalog entry. That is the thing under test.

**What does count:** emit the step, paste it into FileMaker's Script Workspace, and confirm FileMaker accepts it and renders the step you meant — then copy it back out and confirm FileMaker's own XML matches what the catalog produces. Round-trip both directions for every enum value, every boolean state, and every discriminator branch you touched, not just the one you were looking at. FileMaker silently discards a parameter it does not recognise, so a step that pastes without complaint has not passed: read the pasted step back.

An enum is the sharpest case. `enumValues` must hold the value FileMaker **writes into XML**, and `hrEnumValues` maps that value to the label FileMaker **displays**. Getting these backwards produces a step that looks right in every diff and that FileMaker rejects. Eighteen params shipped that way and were corrected on 2026-08-05 — `agent/docs/CONVERTERS.md` § "Closed — the catalog's `enumValues` had the same disease" is the record. `test_converter_conformance.test_catalog_enum_values_are_filemakers` now asserts every `enumValues` against FileMaker's own legal-value comments in `agent/snippet_examples/steps/**`, so this class fails offline; it still cannot tell you the **label**, which only FileMaker can.

## Token Efficiency

**NEVER read `step-catalog-en.json` in full.** It is large (~200KB+) and reading it wastes tokens. Always use Grep to extract only the entry being worked on:

```bash
grep -A 60 '"name": "Step Name Here"' agent/catalogs/step-catalog-en.json
```

Adjust the `-A` line count if the entry is longer. Similarly, when scanning for remaining work, grep for `"auto"` or `"unfinished"` status rather than reading the whole file.

## Workflow Per Step

The user either pastes both the HR format and fmxmlsnippet XML for each step or references a temporary file name which contains both formats. For each step:

1. **Grep** the current catalog entry (see Token Efficiency above) and read the snippet_examples file (including `agent/snippet_examples/steps/CONVENTIONS.md` for snippet authoring rules)
2. **Extract** the `id` from the fmxmlsnippet
3. **Map** HR labels and enum values (which often differ from XML values)
4. **Cross-check** against snippet_examples — always explicitly present the comparison result to the user, even when there are no differences
4b. **Verify against live FileMaker** — round-trip the step through the Script Workspace in both directions, per § "The verification rule" above. This step is mandatory and cannot be satisfied by tests, goldens, or snippet_examples alone.
5. **Update catalog**: set id, add hrLabels, HR enumValues, order params to **XML serialization order** (the order FileMaker writes the child elements/attributes — this is the normative param order per `CATALOG_SCHEMA.md`, used for positional HR↔XML round-tripping; `hrSignature` separately conveys HR display order), set hrSignature, status→"complete"
6. **Update snippet_examples** if needed: fix wrong comments, add missing structure/elements, add HR annotations to comments
7. **Do NOT** include XML comments in code output — they are reference only

## Key Patterns Discovered

- **`invertedHr: true`** — for `NoInteract` → "With dialog" where HR On = XML False
- **`parentElement`** — for params nested inside wrapper elements (e.g., `<FineTuneLLM>`, `<LLMRequestWithTools>`)
- **HR boolean values** — often "On"/"Off" or "Yes"/"No" while XML uses "True"/"False"
- **Flag-style booleans** — some params shown as a word when True, omitted when False (e.g., "Select", "Automatically open", "Stream", "Agentic mode")
- **Canonical XML typos** — `SetLLMAccout`, `AccoutName` (missing 'n'), `RAGPPrompt` (extra P) — these are FileMaker's actual XML
- **Dual-format Field** — `<Field table="" id="" name=""/>` for field refs OR `<Field>$variable</Field>` with `<Text/>` for variables. Both support `repetition` attribute. This is system-wide.
- **Typed Field elements** — inside wrappers like `<Field type="ToolCalls">` or `<Field type="Messages">`
- **`<Text/>` empty element** — appears as sibling when Field holds a variable; not a real HR param
- **`fileReference` type** — for FileReference elements with UniversalPathList children
- **DataType codes** — 4-character classic Mac file type codes (e.g., `"TABS"`, `"XLS "`, `"DBF "`)
- **`findRequests` param type** — used for Query elements; references the shared `find-requests.md` for structure, search operators, and variable rules
- **No-parameter steps** — self-closing steps with no params get `hrSignature: ""` (empty string, not null)
- **Behavioral variants on omission** — some steps change purpose when an optional element is omitted (e.g., Go to Field without a Field element exits the current field and implicitly commits the record). The `required` flag in auto-generated entries may be wrong — always verify against user-provided examples.
- **`flagElement` type** — empty XML elements where presence = on, absence = off (e.g., `<Overwrite/>`, `<ContinueOnError/>`, `<ShowSummary/>`). Different from flag-style booleans which use `state="True"/"False"`.
- **Snippet independence** — snippet_examples must be fully self-contained. Never reference catalog enum files (`animation-enums.md`, `window-enums.md`, etc.) from snippet comments. Those files are only for catalog discussion/modification. Snippet comments must inline all enumerations and attribute values directly.
- **`<Text>` dual role** — In most steps, `<Text/>` (empty, self-closing) is a sibling of `<Field>` signaling a variable target. In Insert Text, `<Text>content</Text>` holds the literal text being inserted (raw content, not Calculation/CDATA). Same element name, different purpose — check the step's snippet carefully.
- **Multi-line raw Text** — `<Text>` content in Insert Text uses `&#xD;` (carriage return) XML character entities for line breaks, not literal newlines.
- **UniversalPathList `type` always explicit** — "Embedded" and "Reference" are always written as explicit attribute values. Never omit the type attribute to mean Embedded. Auto-generated snippets for Insert PDF, Insert Picture, and Insert Audio/Video incorrectly said "omit type attribute to embed" — all corrected.

## Shared Enum Files

Created in `agent/catalogs/` to avoid duplication across steps:

- **`language-enums.md`** — 54 OverrideLanguage values (HR labels + assumed XML values). Simple names confirmed; special variants (Finnish v<>w, German ä=a, Chinese Pinyin/Stroke, Spanish Modern, Swedish v<>w, Serbian Latin, Greek Mixed) marked ⚠️ as needing authoritative XML verification. Used by Sort Records and Sort Records by Field.

- **`shared-enums.md`** — CharacterSet (with HR labels), DataType file source codes, Profile attributes, ImportOptions, ExportOptions, ExportEntries/SummaryFields. Used by Convert File, Export Records, Import Records.
- **`animation-enums.md`** — 12 Animation values (HR→XML), LayoutDestination values. Used by steps with layout transitions (Go to Layout, Go to List of Records, Go to Related Record, etc.).
- **`window-enums.md`** — NewWndStyles attributes, 4 window style types (Document, Floating, Dialog, Card) with supported attribute matrix, new window params. Used by steps that support "New window" mode.
- **`find-requests.md`** — Query XML structure (RequestRow, Criteria, Field, Text), search operators, variable notes, HR parameters. Used by Enter Find Mode, Perform Find, Constrain Found Set, Extend Found Set.

## Status Values

- `"auto"` — auto-generated, not yet reviewed
- `"complete"` — fully reviewed with HR data from user
- `"unfinished"` — partially reviewed, missing some data (e.g., Execute SQL needs ODBC setup)

To find remaining work, scan the catalog for any `"status"` that is not `"complete"`.

## Notes Field

Top-level `notes` object on a catalog entry captures behavioral intelligence migrated from `snippet_examples/` XML comments. Use it as the **first stop** when generating scripts — it avoids a separate file read for the most common gotchas.

### Schema

```json
"notes": {
  "behavioral": ["..."],     // general behavioral details, usage patterns, side effects
  "constraints": ["..."],    // hard rules — FileMaker will not accept or silently breaks these
  "gotchas": ["..."],        // subtle behaviors that cause bugs in real scripts
  "performance": ["..."],    // performance guidance
  "platform": {              // platform-specific notes (only include keys with actual content)
    "pro": "...",
    "server": "...",
    "webdirect": "...",
    "go": "...",
    "cloud": "...",
    "dataapi": "...",
    "cwp": "..."
  }
}
```

Only include sub-keys that have actual content. Do not add empty arrays or empty objects.

### Migration workflow

1. Read the snippet_examples file for the step (path is in the catalog entry's `snippetFile` field).
2. Extract all XML comments from the snippet file (lines starting with `<!--`).
3. Classify each comment into a `notes` sub-key:
   - Platform-qualified notes (`Server:`, `Go:`, `WebDirect:`, etc.) → `platform` sub-key
   - Hard restrictions ("not supported", "returns error", "cannot") → `constraints` or `platform`
   - Subtle or surprising behaviors → `gotchas`
   - General behavioral detail → `behavioral`
4. Add the `notes` object after `helpUrl` in the catalog entry.
5. Validate JSON: `python3 -m json.tool agent/catalogs/step-catalog-en.json > /dev/null`

### Archive rule

Once notes are migrated, the snippet file becomes the **archive** (XML reference only), not the source of behavioral truth. The catalog `notes` field is the live source for agent consumption. Do **not** modify snippet files during this migration — they remain unchanged and read-only.

## Version data (`fmMin`)

Steps, step options, enum values and functions may carry the first FileMaker version that has them (`fmMin` + `fmMinSource`, schema in [`CATALOG_SCHEMA.md`](./CATALOG_SCHEMA.md) § "Version data").
Prefer measuring to reading documentation, and record which one you did.

- **Measured.** Try the item on each installed FileMaker version and take the oldest that accepts it.
  A function is tried without running it, as `If ( False ; <call> ; 1 )` evaluated in a calculation: error 1208 means the function does not exist, error 1215 means the `Get` selector does not exist, and any other result (including argument errors) means it exists.
  A step or option is pasted into the Script Workspace and read back; FileMaker drops what it does not know, so a paste that succeeds proves nothing until the step is read back.
  A step drawn as `<unknown>`, or as `#[OBSOLETE]` followed by another step's name, does not exist in that version.
- **Documented.** When an item cannot be exercised (a platform it does not run on, a step that needs a server), take the version from the "Originated in version" line of its Claris help page.
- An item present in the oldest version measured gets `"any"`, not that version.
- An item first measured present on a later major gets that major's first shipped release (`"20.1"` for FileMaker Pro 20), unless an earlier measured release of the same major lacked it; then it gets the oldest release measured with it (`CATALOG_SCHEMA.md` § "Measured minimums").
- A `"documented"` value that falls between the newest version measured without the item and the oldest measured with it is a refinement: keep it when you measure again.
  A documented value outside that range disagrees with FileMaker; record why next to it before you keep it.
- Leave `fmMin` out when nobody has looked; write `"unknown"` (with the source that was tried) when you looked and could not decide. Never guess.
- A parameter or enum value only gets its own `fmMin` when it is newer than the step; otherwise leave it out and it inherits the step's (`CATALOG_SCHEMA.md` § "Version data", "Combining").
- To retract a wrong value, replace it with the right one or with `"unknown"`.
  Never just delete the keys: a consumer that layers an updated catalog over an older copy keeps keys the update lacks, so the wrong value would survive there.

Version data is not read by the converters, so adding it changes no HR or XML; it still goes through § "The verification rule" like any other catalog edit.

## Additional Reference

Official Claris documentation for each script step may be available at `agent/docs/filemaker/script-steps/`. **Note:** this directory only exists if `agent/docs/filemaker/fetch_docs.py` has been run to download the documentation -- it is not checked into the repo. These can be consulted for parameter details, behavior notes, and platform support. **Important:** the official docs only reference terms in the human-readable format — they contain no fmxmlsnippet/XML values. They are useful for understanding HR option names and step behavior, not for XML element or attribute names.
