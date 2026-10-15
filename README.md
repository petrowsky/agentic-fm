# agentic-fm 🗄️ » 📂 » 🧠

The agentic harness for empowering Claris FileMaker. Analyze solutions, generate/update scripts, design layouts, build schema & more — all through AI that understands your live FileMaker environment.

Visual introduction over at the website [agentic-fm.com](https://agentic-fm.com)

If you're a developer, and wanting to join the conversation, we've got a [Discord server](https://discord.gg/jmghEetMT9) too.

**New here?** Start with [QUICKSTART.md](QUICKSTART.md) — prerequisites, install, and your first working script in one page.

# Background

FileMaker Pro is a closed environment — logic and schema live inside a binary file, not text files. Three XML formats provide the bridge between FileMaker and external tooling:

- **Database Design Report (DDR)** — a full solution export accessed via **Tools > Database Design Report...**. An older format that Claris is moving away from; not used by this project.
- **Save a Copy as XML** — the modern export format accessed via **Tools > Save a Copy as XML...**. Covers scripts, layouts, schema, and more. Can also be triggered programmatically via the Save a Copy as XML script step. This is the format this project uses.
- **fmxmlsnippet** — the clipboard format FileMaker uses to copy and paste individual objects (script steps, fields, layouts, etc.). This is the format AI uses to deliver generated code back into FileMaker.

# 🔧 How to Install

See **[filemaker/README.md](filemaker/README.md)** for the full dependency list and step-by-step setup guide, including how to install `fm-xml-export-exploder` and set up the companion server.

**Dependencies at a glance:**

| Dependency                                                                               | Required By                                            | Notes                                                      |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------ | ---------------------------------------------------------- |
| FileMaker Pro 21.0+                                                                      | Everything                                             | `GetTableDDL`, `While`, and data file steps required       |
| [fm-xml-export-exploder](https://github.com/bc-m/fm-xml-export-exploder/releases/latest) | Explode XML, fmparse.sh                                | Download binary; place at `~/bin/fm-xml-export-exploder`   |
| Python 3                                                                                 | clipboard.py, validate_snippet.py, companion_server.py | stdlib only — no virtualenv required                       |
| xmllint                                                                                  | fmcontext.sh                                           | Ships with macOS; `apt-get install libxml2-utils` on Linux |

**Setup steps:**

1. **Install the Context custom function** — open your solution, go to **File > Manage > Custom Functions**, create a function named `Context` with one `task` parameter, and paste in the contents of `filemaker/Context.fmfn`.

2. **Install the companion scripts** — choose either option:

   **Option A — Open the included .fmp12 file (fastest):**
   Open `filemaker/agentic-fm.fmp12` in FileMaker, then copy and paste the **agentic-fm** script folder directly into your solution's Script Workspace.

   **Option B — Install via clipboard:**

   ```bash
   python3 agent/scripts/clipboard.py write filemaker/agentic-fm.xml
   ```

   Switch to FileMaker, open the Script Workspace, and press **Cmd+V**.

3. **Start the companion server** — the companion server is a lightweight HTTP server that FileMaker calls via `Insert from URL` to run shell commands. Start it before running FileMaker companion scripts:

   ```bash
   python3 agent/scripts/companion_server.py
   ```

   The server listens on port 8765 by default. Keep it running in a terminal while you work.

4. **Configure the repo path** — run the **Get agentic-fm path** script once. It will prompt you to select the agentic-fm folder on disk and store the path in `$$AGENTIC.FM` for use by the other scripts.

5. **Explode the XML** — run the **Explode XML** script to perform your first Save as XML export and populate `agent/xml_parsed/`. Re-run it any time the solution schema changes.

6. **Push context before each session** — navigate to the layout you are working on, run **Push Context**, enter a task description, and the current context will be written to `agent/CONTEXT.json`. You are now ready to work with AI.

# ⚡ Workflow

```
0. Start companion server: python3 agent/scripts/companion_server.py (keep running in background)
1. In FileMaker, run "Explode XML" to export and parse the current solution into agent/xml_parsed/
2. Navigate to the target layout and run "Push Context" with a task description → writes agent/CONTEXT.json
3. AI reads CONTEXT.json + step catalog to generate fmxmlsnippet output in agent/sandbox/
   (snippet_examples are archival reference — the step catalog is now the primary step structure source)
4. validate_snippet.py runs automatically as part of the AI toolchain to check for errors
   (also warns if CONTEXT.json context is older than 60 minutes)
5. clipboard.py writes the validated snippet to the clipboard
6. Paste fmxmlsnippet into FileMaker at the desired insertion point
```

# Agent Skills

Skills are opt-in workflows that extend an agent's default behavior. Invoke them naturally in conversation — no special syntax required.

**Note:** Skill use is available only to CLI/IDE editors. They are not used by the webviewer feature.

| Skill                     | What it does                                                                                                                                            | Example triggers                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
|                           | **Script workflows**                                                                                                                                    |                                                                            |
| **script-lookup**         | Locates a script in the parsed XML export by ID or name, resolving to the paired human-readable and Save-As-XML files                                   | "review script ID 123", "show me the invoice script"                       |
| **script-preview**        | Generates a human-readable step outline for review and iteration before XML is generated                                                                | "preview the script", "outline the steps", "draft the logic"               |
| **script-review**         | Code reviews an existing script and its full call tree — evaluates error handling, structure, naming, performance, and cross-script issues              | "review this script", "check the logic in X script"                        |
| **script-refactor**       | Analyzes an existing script and produces an improved version while preserving observable behavior                                                       | "refactor this script", "clean up script", "optimize script"               |
| **script-debug**          | Systematic debugging — reproduce the issue, isolate the failure, form a hypothesis, verify with runtime data, produce a fix                             | "debug this", "script not working", "wrong output"                         |
| **script-test**           | Generates a companion verification script that exercises a target script with known inputs and asserts expected outputs                                 | "test this script", "write a test", "prove this works"                     |
| **multi-script-scaffold** | Scaffolds interdependent multi-script systems using the Untitled Placeholder Technique                                                                  | "scaffold a multi-script workflow", "build script system"                  |
| **implementation-plan**   | Structured planning before script creation — decomposes requirements, identifies dependencies, surfaces FM-specific constraints                         | "plan this", "decompose requirements", "plan before coding"                |
|                           | **Analysis**                                                                                                                                            |                                                                            |
| **solution-analysis**     | Analyzes an entire solution — data model, scripts, UI, integrations, health — and produces a self-contained HTML report with interactive visualizations | "analyze solution", "solution overview", "what does this solution do?"     |
| **trace**                 | Traces references to a FileMaker object across the entire solution — usage reports, impact analysis, and dead object scans                              | "where is this field used?", "what breaks if I rename X?", "unused fields" |
| **extract-erd**           | Derives a true ERD (Mermaid diagram) from a solution by analyzing table occurrences, relationships, and fields                                          | "extract ERD", "map the schema", "show the database structure"             |
| **fm-debug**              | Captures runtime state by instrumenting scripts with debug output — Tier 1 (manual) or Tier 3 (autonomous)                                              | "debug this script", "analyze the runtime output", "why is this failing?"  |
|                           | **Layout and UI**                                                                                                                                       |                                                                            |
| **layout-spec**           | Conducts a design conversation and produces a written layout specification — object list, field bindings, portal config, button wiring                  | "layout spec", "spec out layout", "what objects should this layout have?"  |
| **layout-design**         | Generates FileMaker layout objects, previews in the webviewer, iterates with the developer, then produces XML2 or HTML output                           | "design layout", "create layout objects", "build layout"                   |
| **webviewer-build**       | Generates a complete web application inside a FileMaker Web Viewer — self-contained HTML/CSS/JS with FM bridge scripts                                  | "web viewer", "webviewer app", "HTML in FileMaker"                         |
| **menu-lookup**           | Locates custom menus and menu sets in xml_parsed, extracts the real UUIDs required for paste operations                                                 | "find the edit menu", "show the custom menu set", "look up menu"           |
|                           | **Schema and data**                                                                                                                                     |                                                                            |
| **schema-plan**           | Designs a data model from a natural-language description — produces a Mermaid ERD and FM-specific model with TOs and relationships                      | "design schema", "plan data model", "create ERD"                           |
| **schema-build**          | Creates and modifies FileMaker schema via OData REST calls — tables, fields, and relationship specifications                                            | "build schema", "create tables", "create fields"                           |
| **data-seed**             | Generates realistic seed/test data and loads it into a live solution via OData                                                                          | "seed data", "test data", "populate solution"                              |
| **data-migrate**          | Moves records from an external source (CSV, JSON, SQL) into a live solution via OData with field mapping and type coercion                              | "migrate data", "import records", "load CSV"                               |
|                           | **Utility**                                                                                                                                             |                                                                            |
| **library-lookup**        | Searches the curated snippet library for reusable fmxmlsnippet code matching the current task                                                           | "use the HTTP request script", "add a timeout loop"                        |

# Objectives

The goals of this project are to provide the guidance and context needed by agentic processes for creating reliable scripts and other FileMaker related code that can be taken from AI back into FileMaker Pro.

## Design Philosophy

The project supports both **whole-script generation** and **step-level editing**, but step-level iteration is the more common workflow. FileMaker has no diff/merge — every paste adds new steps to what is already in the script. Working at the step level is faster and less destructive, especially when modifying existing scripts.

Most of a developer's script work (creation, updates, optimizations, and debugging) happens within `agent/sandbox/`. This is the shared workspace for both the developer and AI. When working on an existing script, reference it by name using the editor's file search; AI will copy it into the sandbox as needed.

**Creating new scripts:** AI generates a sequence of steps as an `fmxmlsnippet` which is pasted directly into FileMaker via the clipboard.

**Modifying existing scripts:** Reference the human-readable script in `agent/xml_parsed/scripts_sanitized/` to understand the logic and identify the lines you want to change. AI uses line numbers from that sanitized version as an unambiguous reference. When the full Save As XML source of a script is needed (e.g. to produce steps for a section of the script), `agent/scripts/fm_xml_to_snippet.py` converts the Save As XML format found in `agent/xml_parsed/scripts/` into valid `fmxmlsnippet` output ready for the clipboard. This conversion is handled automatically by AI as part of its toolchain, not something the developer does manually.

# Architecture

For a detailed view of the data pipeline, the context hierarchy, artifact inventory, and guidelines for adding new features, see [ARCHITECTURE.md](ARCHITECTURE.md).

# Step Catalog

`agent/catalogs/step-catalog-en.json` is a structured JSON reference for all FileMaker script steps. It provides step IDs, parameter definitions (XML element names, types, enums, defaults), HR signatures, and Monaco snippets. The step catalog is the **single source of truth** for step XML structure, including behavioral notes (constraints, gotchas, platform notes) in its `notes` field. Agents grep it first; `snippet_examples/` is now archival reference only. See `agent/docs/SCHEMA_GUIDANCE.md` for a complete param type → XML mapping reference, and [ARCHITECTURE.md](ARCHITECTURE.md) for the full architecture.

# Webviewer

The `webviewer/` directory contains a browser-based visual script editor built with Preact, Monaco, and Vite. Its three-panel layout provides a Monaco script editor (with autocomplete from the step catalog), a live XML preview, and integrated AI chat.

**Quick start:**

```bash
cd webviewer
npm install
npm run dev
# Open http://localhost:8080
```

The webviewer can run as a standalone browser app or embedded inside a FileMaker WebViewer object. When embedded, FileMaker can push context and load scripts via the bridge API. AI providers include Anthropic API, OpenAI API, and Claude Code CLI proxy.

**The webviewer AI and the CLI/IDE agent have different capabilities.** The CLI agent has full filesystem access, reads knowledge docs selectively via the MANIFEST, and writes validated fmxmlsnippet to `agent/sandbox/`. The webviewer AI works from a pre-loaded system prompt — it has access to the same coding conventions and knowledge base, but cannot access index files, `xml_parsed/`, or the snippet library, and cannot run validation or clipboard scripts directly. See [CLI/IDE vs Webviewer AI](webviewer/WEBVIEWER_INTEGRATION.md#clide-vs-webviewer-ai--capability-comparison) in `WEBVIEWER_INTEGRATION.md` for a full comparison and token budget breakdown.

See `webviewer/WEBVIEWER_INTEGRATION.md` for full details.

# Project Structure

```
agentic-fm/
├── fmparse.sh              # CLI tool for parsing XML exports
├── fmcontext.sh            # CLI tool for generating AI-optimized context indexes
├── filemaker/
│   ├── Context.fmfn        # Custom function source — install into your solution
│   ├── agentic-fm.fmp12    # Pre-built FM file — open and copy/paste scripts into your solution
│   ├── agentic-fm.xml      # Companion script group — alternative install via clipboard.py
│   └── README.md           # Full dependency and setup guide
├── agent/
│   ├── CONTEXT.json         # Scoped context for the current task (generated in FileMaker)
│   ├── CONTEXT.example.json # Schema reference and example for CONTEXT.json
│   ├── catalogs/            # Step catalog — structured step definitions
│   ├── context/             # Pre-extracted index files (generated by fmcontext.sh)
│   ├── sandbox/             # Work area for AI-generated scripts (output)
│   ├── config/              # automation.json, removals.json (gitignored credentials)
│   ├── debug/               # Runtime debug output from Agentic-fm Debug script
│   ├── scripts/             # Toolchain scripts (clipboard, validation, deployment, tracing)
│   ├── snippet_examples/    # Archival fmxmlsnippet templates (step catalog is primary)
│   ├── docs/
│   │   ├── filemaker/       # FileMaker help reference (functions, script steps, errors)
│   │   └── knowledge/       # Curated behavioral intelligence about FileMaker
│   ├── library/             # Proven, reusable fmxmlsnippet patterns
│   └── xml_parsed/          # Exploded XML from parsed solutions (reference only)
├── webviewer/               # Visual script editor (Preact + Monaco)
└── xml_exports/             # Versioned XML exports organized by solution (gitignored)
```

- **filemaker/** -- FileMaker artifacts to install into your solution, including a pre-built `.fmp12` file for fast script installation. See [filemaker/README.md](filemaker/README.md).
- **agent/catalogs/** -- Structured JSON reference for all FileMaker script steps. Primary source for step XML structure.
- **webviewer/** -- Browser-based script editor with Monaco, live XML preview, and AI chat. See `webviewer/WEBVIEWER_INTEGRATION.md`.
- **agent/sandbox/** -- The primary working folder. All AI output lands here; paste from here into FileMaker.
- **agent/xml_parsed/** -- Contains the exploded XML for all parsed solution files. Supports the FileMaker data separation model -- each solution file (e.g. UI.fmp12, Data.fmp12) is parsed independently, and only that solution's subdirectories are cleared on re-parse.
- **agent/context/** -- Compact, pipe-delimited index files generated by `fmcontext.sh`. Organized into solution subfolders (`agent/context/{solution}/`) mirroring the xml_parsed hierarchy. Provide fast lookups of all fields, relationships, layouts, scripts, table occurrences, and value lists.
- **agent/CONTEXT.json** -- Generated by FileMaker's `Context()` function before each session. Scoped to the current layout and task so AI has exactly the IDs it needs.
- **xml_exports/** -- Archived XML exports, one subfolder per solution, dated subfolders per run.

# Coding Conventions

All AI-generated FileMaker code (scripts and calculations) follows the conventions defined in `agent/docs/CODING_CONVENTIONS.md`. These are "initially set" based on the community standard at [filemakerstandards.org](https://filemakerstandards.org/code) and cover variable naming prefixes (`$`, `$$`, `~`, `$$~`), `Let()` formatting, operator spacing, boolean values, and control structure style.

**You can, and probably should, customize these conventions to your preferred style.** There are two ways to do this:

- **Team-wide, checked in:** Edit `agent/docs/CODING_CONVENTIONS.md` directly. Changes are committed and shared with everyone who clones the repo.
- **Personal, local-only:** Create `agent/docs/CODING_CONVENTIONS.local.md` (gitignored — never committed). When this file exists, AI reads it *instead of* the checked-in version, so you can keep your own preferences without affecting collaborators or the repo.

AI reads whichever file applies before writing any calculation or script logic and follows whatever rules are defined there. Common customizations include:

- Changing variable naming conventions or casing style
- Adding project-specific prefixes or naming patterns
- Specifying preferred patterns for error handling or transaction structure
- Documenting custom functions that should always be used instead of inline logic

# Knowledge Base

`agent/docs/knowledge/` contains curated behavioral intelligence about FileMaker Pro — nuances, gotchas, and practical insights that go beyond what standard help references cover. While AI is good at logic and control flow, FileMaker has platform-specific behaviors (found set mechanics, context switching, transaction scope, window management) that are easy to get wrong without domain-specific guidance.

Each knowledge document captures what an experienced FileMaker developer knows intuitively but AI would otherwise miss. AI consults these documents before composing scripts, leading to higher-quality output that avoids common pitfalls.

**Current topics:**

| Document                    | Covers                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `found-sets.md`             | Found set attributes, actions on found sets, collecting field values, restoring found sets, snapshot links |
| `single-pass-loop.md`       | Single-pass loop pattern for structured exit control                                                       |
| `variables.md`              | Variable scoping, naming conventions, and lifetime considerations                                          |
| `error-handling.md`         | Error capture patterns, transaction rollback, and server-side compatibility                                |
| `script-parameters.md`      | Passing and parsing script parameters; JSON vs. positional patterns                                        |
| `error-data-capture.md`     | Single-expression error data capture pattern — capturing error state in one step, not many                 |
| `disambiguation.md`         | Commonly confused term pairs and non-negotiable structural rules                                           |
| `dry-coding.md`             | DRY principle in FileMaker scripts — hoisting repeated values into variables                               |
| `field-references.md`       | Field reference patterns — string-based vs. direct references, script steps vs. functions                  |
| `json-functions.md`         | Practical guidance for FileMaker's JSON functions, covering common gotchas and correct patterns            |
| `line-endings.md`           | Line endings and the paragraph character (¶) — CR vs. LF behavior in FileMaker                             |
| `paste-dependency-order.md` | Correct installation order when pasting fmxmlsnippet objects into a solution                               |
| `return-delimited-lists.md` | Searching and manipulating return-delimited (¶-separated) lists                                            |
| `terminology.md`            | FileMaker terminology glossary (redirects to full reference)                                               |
| `executesql.md`             | ExecuteSQL function guidance — SQL syntax differences, quoting, reserved words, and common gotchas         |
| `file-operations.md`        | File operation steps — deleting files, path formats, and related behaviors                                 |
| `script-ids.md`             | Script and object IDs are file-specific — not portable across FileMaker files                              |
| `custom-menu-corruption.md` | Custom menu `<Unknown>` errors in Recover — configuration issue, not true corruption                       |

A keyword-indexed manifest at `agent/docs/knowledge/MANIFEST.md` enables fast lookup. AI scans it for keyword matches against the current task and reads any matching documents before writing script steps.

**Contributing knowledge:** This is one of the most impactful ways to contribute. See `agent/docs/knowledge/CONTRIBUTING.md` for the article format, review criteria, and a list of 15 good topic ideas.

# 📋 FileMaker Companion Scripts

`filemaker/agentic-fm.fmp12` is a pre-built FileMaker file containing the **agentic-fm** script folder group. Open it in FileMaker and copy/paste the script folder into your solution's Script Workspace — this is the fastest installation path. Alternatively, `filemaker/agentic-fm.xml` provides the same scripts in `fmxmlsnippet` format for installation via `clipboard.py`.

| Script                   | Purpose                                                                                                                                                                                                  |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Get agentic-fm path**  | One-time setup. Prompts you to select the agentic-fm repo folder and stores the path in `$$AGENTIC.FM`. All other scripts depend on this global being set.                                               |
| **Push Context**         | Prompts for a task description, calls `Context($task)`, and writes the result directly to `agent/CONTEXT.json`. Run this from whatever layout you are working on before starting an AI session.          |
| **Explode XML**          | Saves a copy of the current solution as XML and calls `fmparse.sh` via the companion server to archive and explode it into `agent/xml_parsed/`. Run this whenever the solution schema or scripts change. |
| **Agentic-fm Debug**     | Posts runtime state JSON to the companion server's `/debug` endpoint, which writes `agent/debug/output.json` for analysis.                                                                               |
| **Agentic-fm Paste**     | Opens a script tab in Script Workspace via MBS `ScriptWorkspace.OpenScript`. Used by Tier 2 deployment.                                                                                                  |
| **Agentic-fm webviewer** | Starts or stops the agentic-fm webviewer from within FileMaker via the companion server.                                                                                                                 |
| **Agentic-fm Menu**      | Handles custom menu calls and passes them through to the agentic-fm web viewer via JavaScript.                                                                                                           |
| **AGFMScriptBridge**     | OData entry point — accepts `{ "script": "...", "parameter": "..." }` JSON and dispatches to any named script. Required because FMS 21.x cannot route OData calls with spaces in script names.           |
| **AGFMGoToLayout**       | Navigates FileMaker to a named layout. Used before calling Push Context to switch solution context to a different layout.                                                                                |
| **AGFMEvaluation**       | Evaluates a FileMaker calculation expression server-side and returns the result; optionally navigates to a layout first.                                                                                 |

**Requirement:** **Explode XML**, **Agentic-fm Debug**, and **Agentic-fm webviewer** communicate with `agent/scripts/companion_server.py` via `Insert from URL`. Start the companion server before running these scripts (`python3 agent/scripts/companion_server.py`, port 8765). **Get agentic-fm path**, **Push Context**, and the OData scripts use only native FileMaker steps.

## Closed-Loop Operation with OData

When a FileMaker file is hosted on FileMaker Server with OData enabled, an AI agent can trigger the companion scripts **programmatically** — without any manual developer action:

```bash
# Refresh context (scoped to a layout) — dispatched through AGFMScriptBridge
curl -X POST "https://{server}/fmi/odata/v4/{database}/Script.AGFMScriptBridge" \
  -H "Authorization: Basic {base64credentials}" \
  -H "Content-Type: application/json" \
  -d '{"scriptParameterValue": "{\"script\": \"Push Context\", \"parameter\": \"{\\\"task\\\": \\\"build invoice workflow\\\"}\"}"}'

# Export and parse the full solution
curl -X POST "https://{server}/fmi/odata/v4/{database}/Script.AGFMScriptBridge" \
  -H "Authorization: Basic {base64credentials}" \
  -H "Content-Type: application/json" \
  -d '{"scriptParameterValue": "{\"script\": \"Explode XML\"}"}'
```

All OData script calls go through `AGFMScriptBridge` because FMS 21.x cannot route OData calls with spaces in script names. The bridge accepts `{ "script": "<name>", "parameter": "<optional>" }` and dispatches to the named script.

This enables a fully autonomous development loop: the agent generates code, reads back what landed in the solution, and refreshes context — all without the developer running a single script manually.

**For full closed-loop operation:**

- Host the FM file on FileMaker Server (local Docker or remote)
- Enable OData on the file with an account that has the `fmodata` extended privilege
- Run the companion server (`python3 agent/scripts/companion_server.py`) — the Explode XML script calls it

The scripts in `filemaker/agentic-fm.xml` are expected to be present in any solution where you want this level of agent autonomy. Think of them as the bridge between the agent and the live FM environment.

### Multi-file solutions

FileMaker solutions often separate UI and data across multiple files. Each file is a distinct FM solution with its own OData endpoint, account, and export paths. `agent/config/automation.json` (gitignored) supports this with a `solutions` object keyed by FM file name:

```json
{
  "solutions": {
    "MyApp UI": {
      "odata": {
        "base_url": "...",
        "database": "MyApp UI",
        "username": "...",
        "password": "...",
        "script_bridge": "AGFMScriptBridge"
      },
      "explode_xml": {
        "repo_path": "...",
        "export_path": "...",
        "companion_url": "http://host.docker.internal:8765"
      }
    },
    "MyApp Data": {
      "odata": {
        "base_url": "...",
        "database": "MyApp Data",
        "username": "...",
        "password": "...",
        "script_bridge": "AGFMScriptBridge"
      },
      "explode_xml": {
        "repo_path": "...",
        "export_path": "...",
        "companion_url": "http://host.docker.internal:8765"
      }
    }
  }
}
```

The agent matches the active solution by comparing the key to `CONTEXT.json["solution"]` (which reflects `Get(FileName)` at the time Push Context was run). Switch between files by running Push Context on the target layout in the target file — the agent picks up the correct OData config automatically.

# fmparse.sh

A command line tool, called from within FileMaker, that archives a FileMaker XML export and parses it into its component parts using [fm-xml-export-exploder](https://github.com/bc-m/fm-xml-export-exploder). Supports the data separation model -- each solution file is parsed independently and only its subdirectories are cleared on re-parse, preserving other solutions' data in `agent/xml_parsed/`.

**Usage:**

```bash
./fmparse.sh -s "<Solution Name>" <path-to-export> [options]
```

**Required:**

- `-s, --solution` -- The solution name. Determines the subfolder under `xml_exports/` where the export is archived.

**Options:**

- `-a, --all-lines` -- Parse all lines (reduces noise filtering)
- `-l, --lossless` -- Retain all information from the XML
- `-t, --output-tree` -- Output tree format: `domain` (default) or `db`

**Examples:**

```bash
# Parse a single XML file
./fmparse.sh -s "Invoice Solution" /path/to/Invoice\ Solution.xml

# Parse a directory of exports with all lines
./fmparse.sh -s "Invoice Solution" /path/to/exports/ --all-lines
```

**Removing sensitive items automatically:**

Some scripts or custom functions may contain passwords, API keys, or other credentials that must not be left on disk where they are readable by AI agents. Create `agent/config/removals.json` (gitignored, never committed) to list items that should be deleted from `agent/xml_parsed/` after every export, before the context indexes are built.

Items can be identified by name or by FileMaker ID. ID matching is preferred because it survives renames.

```json
{
  "Invoice Solution": {
    "scripts": ["Admin Password Reset", 42],
    "custom_functions": ["GetAPIKey", 15]
  }
}
```

Strings match by name; integers match by ID. Each matched item is removed from all parallel directories (`scripts/`, `scripts_sanitized/`, `custom_functions/`, `custom_function_stubs/`). See `agent/config/removals.json.example` for a full template.

# fmcontext.sh

A command line tool that generates AI-optimized index files from the exploded XML in `agent/xml_parsed/`. Uses `xmllint` to extract only the useful data (IDs, names, types, references) and discard noise (UUIDs, hashes, timestamps, visual positioning).

**Usage:**

```bash
./fmcontext.sh                         # regenerate all solutions
./fmcontext.sh -s "Invoice Solution"   # regenerate one solution only
```

This is run at the end of `fmparse.sh`. It reads `agent/xml_parsed/` and writes to `agent/context/{solution}/`.

**Generated files** (per solution under `agent/context/{solution}/`):

| File                      | Contents                                                           |
| ------------------------- | ------------------------------------------------------------------ |
| `fields.index`            | All fields across all tables (name, ID, type, auto-enter, flags)   |
| `relationships.index`     | Relationship graph (TOs, join type, join fields, cascade settings) |
| `layouts.index`           | Layouts with name, ID, base TO, and folder path                    |
| `scripts.index`           | Scripts with name, ID, and folder path                             |
| `table_occurrences.index` | Table occurrence to base table mapping                             |
| `value_lists.index`       | Value list names, sources, and values                              |

Each file is pipe-delimited with a header comment documenting the column format.

# validate_snippet.py

A post-generation validation tool that checks `fmxmlsnippet` output for common errors before pasting into FileMaker. Runs automatically as part of the AI toolchain — rarely needed directly.

**Usage:**

```bash
python3 agent/scripts/validate_snippet.py [file_or_directory] [options]
```

With no arguments it validates all files in `agent/sandbox/`. It auto-detects `agent/CONTEXT.json` when present.

**Checks performed:**

| Check                 | Description                                                                                                    |
| --------------------- | -------------------------------------------------------------------------------------------------------------- |
| Well-formed XML       | File parses as valid XML                                                                                       |
| Root element          | Must be `<fmxmlsnippet type="FMObjectList">`                                                                   |
| No Script wrapper     | Output must not be wrapped in `<Script>` tags                                                                  |
| Step attributes       | Every `<Step>` has `enable`, `id`, and `name`                                                                  |
| Paired steps          | If/End If, Loop/End Loop, Open Transaction/Commit Transaction are balanced                                     |
| Else/Else If ordering | No Else If after Else, no duplicate Else within an If block                                                    |
| Known step names      | All step names exist in snippet_examples                                                                       |
| Reference cross-check | Field, layout, and script IDs match CONTEXT.json                                                               |
| Context staleness     | Warns if CONTEXT.json is older than 60 minutes; shows layout name at push time                                 |
| Coding conventions    | Warns on ASCII comparison operators (`<>` → `≠`, `<=` → `≤`, `>=` → `≥`) and variable naming prefix violations |

**Options:**

- `--context <path>` -- Path to CONTEXT.json (auto-detected by default)
- `--snippets <path>` -- Path to snippet_examples directory
- `--quiet, -q` -- Only show errors and warnings

# Context.fmfn

`filemaker/Context.fmfn` is a FileMaker custom function that generates `CONTEXT.json` at runtime. Install it via **File > Manage > Custom Functions** (see [How to Install](#-how-to-install) above). Once the companion scripts are installed, use **Push Context** rather than evaluating the function manually.

The function introspects the live FileMaker solution using design functions and `ExecuteSQL` queries against system tables. It automatically discovers:

- The current layout, its base table occurrence, and its named objects
- All table occurrences referenced on the layout with complete field lists (name, ID, type)
- Relationship information via `GetTableDDL` (FOREIGN KEY constraints and field comments)
- All scripts, layouts, and value lists in the solution (name + ID)

Because the output is scoped to the current layout's context, the AI receives exactly the information it needs without unnecessary noise. See `docs/Context.fmfn.md` for the full technical reference.

# CONTEXT.json

Generated by the `Context` custom function in FileMaker before each script generation request. Contains scoped context — only the tables, fields, layouts, scripts, relationships, and value lists relevant to the current task. The `generated_at` field (ISO 8601 UTC) is included for staleness detection; `validate_snippet.py` warns if the context is older than 60 minutes.

See `agent/CONTEXT.example.json` for the full schema and a realistic example.

# FileMaker Reference Documentation (Optional)

The `agent/docs/filemaker/` directory contains a script that fetches the official FileMaker Pro reference documentation from the Claris help site and converts it to Markdown. This is useful for giving AI agents accurate, up-to-date information about script step options, function syntax, and error codes without relying solely on training data.

> **Legal notice:** The generated Markdown files are copyrighted by Claris International Inc. They are excluded from this repository via `.gitignore` and may only be generated for personal, non-commercial use in accordance with the [Claris Website Terms of Use](https://claris.com/company/legal/terms). Do not commit, redistribute, or publish the generated files.

**Usage:**

```bash
cd agent/docs/filemaker
python3 fetch_docs.py              # fetch everything
python3 fetch_docs.py --steps      # script steps only
python3 fetch_docs.py --functions  # functions only
python3 fetch_docs.py --errors     # error codes only
python3 fetch_docs.py --force      # re-download cached files
```

**Outputs** (written relative to `agent/docs/filemaker/`):

| Path                             | Contents                                                 |
| -------------------------------- | -------------------------------------------------------- |
| `script-steps/<slug>.md`         | One file per script step (options, compatibility, notes) |
| `functions/<category>/<slug>.md` | One file per calculation function                        |
| `error-codes.md`                 | Full FileMaker error code reference                      |

Dependencies (`requests` and `beautifulsoup4`) are installed automatically on first run if not already present.

# Dependencies

See [filemaker/README.md](filemaker/README.md) for full installation instructions for each dependency.

- **[fm-xml-export-exploder](https://github.com/bc-m/fm-xml-export-exploder/releases/latest)** — required by `fmparse.sh` and the **Explode XML** FileMaker script. Place the binary at `~/bin/fm-xml-export-exploder` or set `FM_XML_EXPLODER_BIN` to the full path.
- **xmllint** — required by `fmcontext.sh`. Ships with macOS via libxml2. On Linux: `apt-get install libxml2-utils`.
- **Python 3** — required by `clipboard.py`, `validate_snippet.py`, and `companion_server.py`. All three use stdlib only; no virtualenv is needed. Run directly with `python3 agent/scripts/...`. macOS ships Python 3 at `/usr/bin/python3`; for a newer version install via [Homebrew](https://brew.sh): `brew install python`.
- **companion_server.py** — lightweight HTTP server on port 8765 that FileMaker calls via `Insert from URL` to run shell commands. Start with `python3 agent/scripts/companion_server.py`.
- **Node.js 18+** — required by the webviewer (`webviewer/`). Optional if you only use the CLI/IDE workflow.

# Project Website

The project website is at [agentic-fm.com](https://agentic-fm.com), built with Astro and Tailwind CSS. Source is in the `website/` folder.

**Local development:**

```bash
cd website
npm install
npm run dev
```

**Deploy:** Automatic via GitHub Actions on push to `main`. See `.github/workflows/deploy.yml`.

# Contributions

Contributions are welcome. This project is intended to grow through collaboration with the FileMaker developer community.

- **Knowledge base articles** -- The more complete the knowledge base is, the higher the quality of AI-generated code. If you know of a FileMaker behavior, nuance, or gotcha that AI commonly gets wrong, write it up as a Markdown file and add it to `agent/docs/knowledge/`. Use lowercase-kebab-case filenames (e.g., `record-locking.md`, `window-management.md`) and add an entry to `agent/docs/knowledge/MANIFEST.md`. Good candidates include context switching, transaction scope, server-side vs. client-side compatibility, sort order persistence, and any platform-specific behavior that isn't obvious from the help files alone.
- **Bug reports and corrections** -- If you find an error, an omission, or a snippet that produces incorrect output, please open an issue.
- **Updated snippet examples** -- Additional and/or updated `fmxmlsnippet` templates for step types not yet covered are among the most valuable contributions.
- **Editor and workflow support** -- The core toolchain should be editor-agnostic. It was developed using Cursor. If you build support for a specific editor, IDE, or automation workflow, a pull request is welcome.
- **Webviewer and HR converter** -- Improvements to the webviewer UI, the HR-to-XML converter, Monaco autocomplete definitions, or AI chat integration. If you add or modify step catalog entries, verify the webviewer's converter handles the changes correctly.
- **Improvements to the companion scripts** -- The FileMaker scripts in `filemaker/agentic-fm.xml` are early versions. Better path handling, error reporting, and cross-platform support are all good targets.

Please follow the standard fork-and-pull-request workflow. For significant changes, open an issue first to discuss the approach.
