# Python

Always use `python3` — never bare `python`. macOS does not ship a `python` binary; the system Python is only available as `python3`. Core scripts and the `agent/fmlint/` package use the Python standard library only — no virtual environment is required for them.

## Virtual environment (optional — skill dependencies)

Some skills (e.g. `icon-swap`) require Python packages beyond the standard library. These are installed in an **optional venv** inside the project folder. The venv is gitignored and does not affect stdlib-only scripts.

**When to set up:** Only when a skill reports missing dependencies (e.g. `fm_svg_convert.py --check-deps` fails). Do not proactively create the venv — prompt the developer first.

**Setup (macOS):**
```bash
python3 -m venv agent/.venv
source agent/.venv/bin/activate
pip install cairosvg Pillow
```

**System dependencies (if needed for stroke-to-fill SVG conversion):**
```bash
brew install potrace
```

**Running scripts with the venv:** When the venv exists, use `agent/.venv/bin/python3` instead of bare `python3` for scripts that need the extra packages:
```bash
agent/.venv/bin/python3 agent/scripts/fm_svg_convert.py --check-deps
```

Or activate the venv first: `source agent/.venv/bin/activate`

**Important:** On some macOS configurations, `pip install` to the system Python is blocked by default. The venv approach avoids this entirely. Never suggest `sudo pip install` or `--break-system-packages`.

# Session startup

At the start of each new CLI/IDE session, before responding to the first prompt, run an update check:

```bash
git fetch origin --quiet 2>/dev/null; git rev-list HEAD..origin/main --count 2>/dev/null
```

If the result is greater than `0`, pause and notify the user before proceeding. Before writing the notice, read the top of `UPDATES.md` (the **Recent Changes** section) and pull out any entries flagged **Action required** or **Action:** — these are the migration steps a user must (or may optionally) take after pulling, such as updating a custom function or creating a config file. Include them in your notice so the user learns not just *that* an update exists but *what changed* and what — if anything — they need to do:

> **agentic-fm update available** — your clone is N commit(s) behind `origin/main`. Run `git pull --ff-only` to update before continuing, then restart your agent session.
>
> This update includes: <one line per Recent Changes entry, leading with any Action-required/optional steps — e.g. "Optional: create `agent/config/companion.json` if you run a non-default companion port/host">. See `UPDATES.md` for details.

Do not derive the "what changed" summary from git commit messages — `UPDATES.md`'s Recent Changes section is the canonical, user-facing changelog; commit messages are not. Do this **once per session**, not on every prompt. If the check fails (no network, not a git repo, etc.), skip it silently and continue.

## Environment detection

Also at session start, check if you are running in a sandboxed or non-macOS environment:

```bash
uname -s 2>/dev/null; command -v osascript &>/dev/null && echo "OSASCRIPT" || echo "NO_OSASCRIPT"
```

If `uname` returns `Linux` or `osascript` is not found, read `agent/docs/SANDBOXED_ENVIRONMENT.md` before proceeding. That document covers setup paths, platform limitations, and the filesystem bridge workflow for sandboxed agents.

## Plug-in detection (optional enhancement)

Also at session start, ask the companion server whether the optional AgenticFM plug-in is present and **usable**. The companion is the single detection broker — one call answers it. Derive its address from `agent/config/companion.json` (`companion.advertise_host` + `companion.port`), falling back to `local.hub:8765` when that file is absent, so the probe follows a changed port instead of hardcoding one:

```bash
BASE=$(python3 -c "import json;c=json.load(open('agent/config/companion.json')).get('companion',{});print(f\"{c.get('advertise_host','local.hub')}:{c.get('port',8765)}\")" 2>/dev/null || echo local.hub:8765)
curl -s --max-time 5 "http://$BASE/health"
```

Read the `plugin` block in the response:

- `plugin.usable == true` — the plug-in is installed, reachable, **and** licensed (`status ∈ {active, trial}`). Read `agent/docs/PLUGIN_INTEGRATION.md` and enter **plugin-preferred mode**: prefer the plug-in for understanding/context, authoring, validation, and install, with the OSS path as the fallback (full routing table in that doc).
- `plugin.installed == true` but `usable == false` — installed but the license/trial has lapsed or the server is down. Stay on the **pure OSS path**. At most **once per session**, you may surface the gentle lapsed-license nudge described in `PLUGIN_INTEGRATION.md` (§10.4). Never block or degrade the OSS workflow.
- block absent / `installed == false` / companion unreachable — pure OSS path, no nudge.

Gate on `usable`, never on `installed`. Do this **once per session**, not per prompt. If the check fails for any reason, skip it silently and continue — the OSS workflow never depends on the plug-in.

# Local development context

If `PROJECT.md` exists at the project root, read it at session start. It contains local-only context: meta-project notes, toolchain details, and `external_tools/` documentation. Its absence is normal — it is gitignored and will not be present in collaborator environments.

## Documentation Audience

- When writing docs for this project, default audience is END-USERS who download the repo as a tool, NOT collaborative developers/contributors, unless explicitly told otherwise.

# Overview

This project is designed to create FileMaker objects — primarily scripts and calculations — in the clipboard-supported fmxmlsnippet format. Developers reference and use the HR (human-readable) format for scripts. The following folders are used.

- _sandbox/_ is where all newly created or in-progress work is stored.
- _CONTEXT.json_ is the primary source of IDs, names, relationships, and other metadata for the current task. See **Context system** below.
- _context/_ contains pre-extracted index files — a secondary lookup source. See **Context system** below.
- _xml_parsed/_ is the XML output from the FileMaker solution. See **Context system** below.
- _catalogs/_ contains the step catalog (`step-catalog-en.json`) — a structured index of all FileMaker script steps with parameter definitions, types, enums, and HR signatures. This is the primary reference for step XML structure.
- _snippet_examples/_ is an **archival** reference folder. The step catalog is the single source of truth for step structure. Read snippet_examples only when the catalog's `notes` field is insufficient.
- _fmlint/_ is the FMLint linter package. Run via `python3 -m agent.fmlint` to validate fmxmlsnippet XML or human-readable scripts.

# Context system

Multiple sources of context are available about the FileMaker solution. Always start with the most efficient source.

## CONTEXT.json (primary)

`agent/CONTEXT.json` is the primary source when it exists and reflects the current task. It is generated by FileMaker and scoped to the objects relevant to the current layout and task. It contains:

- `task` — a natural-language description of what to create
- `current_layout` — the layout the user is on, with name, ID, and base table occurrence
- `tables` — relevant tables with their fields (name, ID, type, auto-enter, calculations)
- `relationships` — how the relevant tables connect (TOs, join fields, cascade settings)
- `scripts` — scripts that may need to be called, with name and ID
- `layouts` — layouts that may need to be navigated to, with name, ID, and base TO
- `value_lists` — value lists that may be needed, with name, ID, and values

**All IDs in CONTEXT.json are ready to use directly in fmxmlsnippet output.** No lookup is required. See `agent/CONTEXT.example.json` for the full schema.

**CONTEXT.json only exists for established solutions where context has been exported from FileMaker.** Always check for its presence before attempting to read it — if absent, derive context from the index files, xml_parsed, or by asking the developer directly. Never fail or stall because CONTEXT.json is absent.

**Proactively suggest a Push Context refresh when the request doesn't match the current context.** Before generating any code, check whether the developer's request aligns with `current_layout.name` and `task`. If mismatched, suggest running Push Context on the correct layout before proceeding.

**Context must be refreshed when the FM state changes during a build** — e.g., after a new layout is created, the agent needs a fresh CONTEXT.json scoped to that layout.

**When CONTEXT.json does not exist or is stale**, ask the developer to navigate to the relevant layout in FileMaker and run the **Push Context** script. This script prompts for a task description, calls the `Context()` custom function, and writes the result directly to `agent/CONTEXT.json`.

## Index files (secondary)

`agent/context/{solution}/*.index` files are pipe-delimited lookup tables covering the entire solution. Use these when CONTEXT.json does not contain the needed object. Each file has a header comment documenting the column format.

- `fields.index` — every field across all tables
- `relationships.index` — the relationship graph
- `layouts.index` — all layouts with name, ID, base TO, and folder path
- `scripts.index` — all scripts with name, ID, and folder path
- `table_occurrences.index` — TO-to-base-table mapping
- `value_lists.index` — value list names, sources, and values

Search with grep: `grep "Invoices Details" "agent/context/SolutionApp/layouts.index"`

## xml_parsed (last resort)

Only fall back to grepping `agent/xml_parsed/` if the needed information is not in CONTEXT.json or the index files.

- ALWAYS use grep to search — never read full file contents
- Prefer `scripts_sanitized/` for understanding script logic over `scripts/` XML
- `scripts/` contains Save As XML (SaXML) format — use `agent/scripts/fm_xml_to_snippet.py` to convert to fmxmlsnippet when needed
- `custom_menus/` and `custom_menu_sets/` contain one XML file per custom menu/set
- Do NOT read `xml_parsed/layouts/` unless analyzing layout objects — layout XML is extremely verbose and rarely needed; layout IDs and names are in CONTEXT.json and `layouts.index`

## Automation

The agentic-fm script collection and OData-based automation are documented in `agent/docs/AUTOMATION.md`. See that file when triggering FM scripts programmatically or working with `agent/config/automation.json`.

# Output format

The developer always works in **human-readable (HR) script format**. The agent's final deliverable is **fmxmlsnippet XML** written to `agent/sandbox/`.

- Output should contain ONLY script steps within the `<fmxmlsnippet type="FMObjectList">` wrapper — **do NOT wrap in `<Script>` tags**.
- Use the simplified fmxmlsnippet syntax from the step catalog, NOT the verbose XML format found in xml_parsed/scripts.
- The `script-preview` skill generates an HR preview before XML generation — use it when the developer wants to review logic before committing to XML.
- Line numbers always reference the human-readable script (`scripts_sanitized/`), never the raw XML.

## fmxmlsnippet rules

> **CRITICAL — No XML comments in fmxmlsnippet output**
>
> XML comments (`<!-- -->`) are **silently discarded** when FileMaker reads fmxmlsnippet content from the clipboard. Never use XML comments to document script intent — use FileMaker script steps instead:
>
> - **Inline comment** → `# (comment)` step (`id="89"`)
>   ```xml
>   <Step enable="True" id="89" name="# (comment)">
>     <Text>This is an inline comment visible in the Script Workspace.</Text>
>   </Step>
>   ```
> - **Blank line** → empty self-closing `# (comment)` with no `<Text>` element
>   ```xml
>   <Step enable="True" id="89" name="# (comment)"/>
>   ```
> - **Doc block comment** → disabled `Insert Text` step targeting `$README`
>   ```xml
>   <Step enable="False" id="61" name="Insert Text">
>     <SelectAll state="False"/>
>     <Text>PARAMETER FORMAT:&#xD;  JSONSetElement ( "{}" ; ...</Text>
>     <Field>$README</Field>
>   </Step>
>   ```

- The id attribute of most tags can be 0 — FileMaker auto-assigns on paste.
- Certain steps require a matching partner (If/End If, Loop/End Loop, etc.). The catalog's `blockPair` field identifies these relationships and each step's role (`open`, `middle`, `close`).
- The `selfClosing` flag in the catalog indicates `<Step ... />` vs `<Step ...>...</Step>`.
- XML comments within snippet_examples are for reference only — never include them in output.

# Core workflow

## Before writing

**MANDATORY: Before writing ANY script or function:**

1. Read `agent/CONTEXT.json` for the task description and all reference IDs (when present)
2. Read coding conventions — check for `agent/docs/CODING_CONVENTIONS.local.md` first (gitignored, developer's personal/local override); if it exists, read it **instead of** `agent/docs/CODING_CONVENTIONS.md` and follow it exclusively. Otherwise read `agent/docs/CODING_CONVENTIONS.md`. All generated FileMaker code must follow whichever file applies.
3. Scan `agent/docs/knowledge/MANIFEST.md` for keyword matches against the current task — read and apply matching documents
4. For scripts: grep the step catalog for each step type used (see **Step catalog** below)
5. Substitute the specific IDs/names/values from CONTEXT.json

## After writing

**MANDATORY: After writing or updating a file within agent/sandbox/:**

6. Run `python3 -m agent.fmlint agent/sandbox/<filename>` to validate. Fix any ERROR-severity diagnostics before presenting to the user; review WARNING-severity.
7. Deploy using `agent/scripts/deploy.py`. Use the tier appropriate for the situation (see `agent/config/automation.json`). When falling back to Tier 1 (manual paste), present instructions in this exact format:

> The script is on your clipboard. To install it:
>
> 1. Open **Script Name** in Script Workspace
> 2. **⌘A** — select all existing steps and delete
> 3. **⌘V** — paste

**Container and non-macOS environments**: `deploy.py` detects the runtime environment automatically. When running inside a Docker container or any non-macOS host, AppleScript execution is delegated to the companion server on the macOS host via `/trigger`. No special handling is needed — just call `deploy.py` the same way.

## Lookup decision tree

Two kinds of lookup are needed: **solution-specific references** (layout, field, script IDs) and **step structure** (XML elements and attributes).

1. Does CONTEXT.json have the reference? → Use it directly. Done.
2. Is it a step structure question? → Grep the step catalog. Done.
3. Reference missing from CONTEXT.json? → Search the appropriate `agent/context/{solution}/*.index` file.
4. Still missing? → Grep `agent/xml_parsed/` as last resort. Never read entire files.

## Step catalog

`agent/catalogs/step-catalog-en.json` is the canonical reference for all FileMaker script steps. **Never read the full file** (~200KB+). Always grep for individual entries:

```bash
grep -A 60 '"name": "Step Name"' "agent/catalogs/step-catalog-en.json"
```

**Priority order: catalog params → catalog notes → snippet_examples (archival)**

- For steps with `"status": "complete"` — construct XML directly from the catalog's `params` array, `selfClosing` flag, and `id`
- For behavioral context — check the catalog's `notes` field (`constraints`, `platform`, `gotchas`, `performance`, `behavioral`)
- Fall back to snippet_examples (path in `snippetFile`) only when notes are insufficient or status is `"auto"`/`"unfinished"`

### Key catalog fields

| Field         | Purpose                                                                                                                                      |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`          | FileMaker internal step ID — use in `<Step id="X">`                                                                                          |
| `selfClosing` | `true` → `<Step ... />`, `false` → `<Step ...>...</Step>`                                                                                    |
| `params[]`    | Full parameter spec: `xmlElement`, `type`, `hrLabel`, `wrapperElement`, `parentElement`, `xmlAttr`, `required`, `defaultValue`, `enumValues` |
| `hrSignature` | Human-readable parameter format for HR output                                                                                                |
| `blockPair`   | Matching step partners and role (`open`/`middle`/`close`)                                                                                    |
| `notes`       | Behavioral context sub-keys                                                                                                                  |
| `snippetFile` | Path to archival snippet_examples file                                                                                                       |
| `status`      | `"complete"` / `"auto"` / `"unfinished"`                                                                                                     |

### Param types → XML emission

| Type           | XML pattern                                                                          |
| -------------- | ------------------------------------------------------------------------------------ |
| `boolean`      | `<Element xmlAttr="True\|False"/>` — check `enumValues` for HR labels                |
| `enum`         | `<Element xmlAttr="value">` or `<Element>value</Element>`                            |
| `calculation`  | `<Calculation><![CDATA[expression]]></Calculation>`                                  |
| `namedCalc`    | `<WrapperElement><Calculation><![CDATA[expression]]></Calculation></WrapperElement>` |
| `text`         | `<Element>literal text</Element>`                                                    |
| `field`        | `<Field table="TO" id="N" name="FieldName"/>` — resolve from CONTEXT.json            |
| `script`       | `<Script id="N" name="ScriptName"/>` — resolve from CONTEXT.json                     |
| `layout`       | Layout reference — resolve from CONTEXT.json                                         |
| `findRequests` | See `agent/catalogs/find-requests.md`                                                |
| `flagElement`  | Empty element presence = on, absence = off                                           |

### HR format generation

- Look up the step by name, use the `hrSignature` field for the parameter format
- If `hrSignature` is null, fall back to reading the archival snippet_examples file

# Clipboard

FileMaker objects are transferred via the macOS clipboard using proprietary binary descriptor classes — **not** plain text. Never use `pbpaste` or `pbcopy`; they corrupt multi-byte UTF-8 characters.

```bash
# Read FM objects from clipboard → save as XML
python3 agent/scripts/clipboard.py read agent/sandbox/output.xml

# Write XML file → clipboard (ready to paste into FileMaker)
python3 agent/scripts/clipboard.py write agent/sandbox/myscript.xml
```

The write command auto-detects the correct clipboard class from the XML content. For full technical details, see `agent/docs/CLIPBOARD.md`.

# Custom functions

FileMaker solutions may contain custom functions. These are referenced by name in calculations (CDATA text) and do **not** require IDs in fmxmlsnippet output. The AI must know which custom functions exist so it uses them rather than inventing alternatives.

Custom functions fall into three categories:

1. **Constants** — return a fixed value (e.g. `CardWindowHeight` returns `600`). Always use the custom function name; do NOT substitute a literal number.
2. **Functional code** — general-purpose utility logic with no field references (e.g. `FormatPhone ( phoneNumber )`). Safe to call from any context.
3. **Solution-specific code** — contain references to fields or table occurrences. Before using one, verify the script will be running on a layout whose base TO supports the referenced fields.

When CONTEXT.json includes a `custom_functions` section, prefer it. Otherwise, check:

- `xml_parsed/custom_functions_sanitized/` — human-readable calculation text
- `xml_parsed/custom_function_calcs/` — XML calculation definitions

# Custom menus

Custom menus are a distinct object type from scripts with a different clipboard format and XML wrapper. **Before creating or modifying any custom menu XML, use the `menu-lookup` skill** to extract the real UUIDs. Without these, FileMaker silently ignores the paste. Full details in `agent/docs/CUSTOM_MENUS.md`.

# Library

The `agent/library` folder is a curated collection of reusable fmxmlsnippet code. Use the `library-lookup` skill to access the manifest.

**Proactively** — before writing significant logic, scan the manifest for keyword matches. If found, adapt the library code rather than writing from scratch.

**Integration rules:**

- Extract inner `<Step>` elements only (not the `<Script>` wrapper) unless specifically requested
- Replace placeholder references with real values from CONTEXT.json
- Do not remove structural or purpose comments embedded in library code

# References

- **Coding conventions**: `agent/docs/CODING_CONVENTIONS.md` — variable naming, Let() formatting, operator spacing, boolean values, control structure style
- **Knowledge base**: `agent/docs/knowledge/MANIFEST.md` — behavioral intelligence about FileMaker nuances and gotchas
- **Debugging**: Use the `fm-debug` skill when a script's behavior cannot be diagnosed from source code alone. Details in `agent/docs/AGENTIC_DEBUG.md`
- **Function reference**: `agent/docs/filemaker/functions/` — official FM function docs (not guaranteed present). Validate function names against this folder when writing calculations. Do not invent function names.
- **Schema guidance**: `agent/docs/SCHEMA_GUIDANCE.md` — complete param type → XML mapping reference
- **Documentation conventions**: When writing docs, use generic placeholder names (`SolutionApp`, `SolutionData`) instead of real solution names. Exception: when the context is explicitly about a specific solution.
- **Sandboxed environments**: `agent/docs/SANDBOXED_ENVIRONMENT.md` — setup and operation guide for agents running in sandboxed, containerized, or virtualized environments (Codex, Claude Code, Docker, etc.). Read this if you detect you are not running natively on macOS.

# Constraints

- XML within _xml_parsed/_ is NEVER modified — only referenced.
- When an existing script in _xml_parsed/_ is referenced for modification, copy it into _sandbox/_.
- XML within _snippet_examples/_ is NEVER modified. Prompt the user if changes seem needed.
- Index files in _context/_ are NEVER manually edited — regenerated by `fmcontext.sh`.
- _CONTEXT.json_ is generated by FileMaker — never manually created or modified by AI.
- _step-catalog-en.json_ is maintained via `agent/catalogs/UPDATING_CATALOGS.md`. See `agent/docs/SCHEMA_GUIDANCE.md` for the param type → XML mapping reference.
