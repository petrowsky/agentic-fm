# Knowledge Base Manifest

Curated behavioral intelligence about FileMaker Pro. These documents capture nuances, gotchas, and practical insights that inform better script composition decisions. They complement — but are distinct from — the vendor reference docs in `agent/docs/filemaker/` and `agent/docs/mbs/` and others.

All paths are relative to `agent/docs/knowledge/`.

---

| File | Description | Keywords |
|------|-------------|----------|
| `CONTRIBUTING.md` | Guide for contributing knowledge base articles: format, review criteria, good and bad topic ideas, submission process | contributing, contribution, knowledge base, article format, pull request, topic ideas, new article |
| `dry-coding.md` | DRY (Don't Repeat Yourself) scripting patterns: hoisting repeated literals into top-of-script variables, configuration blocks, compound value assembly, naming conventions, and when not to apply the pattern | DRY, don't repeat yourself, repeated value, configuration, top of script, variable, URL, base URL, magic number, hoist, wet code, single source of truth |
| `line-endings.md` | FileMaker's CR-only line endings (¶ = Char(13), Code 13), valid and invalid uses of ¶ in concatenation expressions, the ¶¶ unquoted syntax error, literal return in the calculation dialog producing Code 32, converting to LF or CRLF for external systems, normalising incoming text | line ending, paragraph, ¶, carriage return, CR, LF, CRLF, Char 13, Char 10, Code, Substitute, concatenation, calculation dialog, literal return, line break, newline, blank line |
| `error-data-capture.md` | `Get ( LastError )` resets the error state: `Get ( LastErrorLocation )` and `Get ( LastErrorDetail )` return empty if captured in a separate step. All three must be in a single `JSONSetElement` expression. Also covers: `Perform Script` resets error state across script boundaries, forced-error line number technique, `Get ( LastErrorLocation )` format (`ScriptName\rStepName\rLineNumber`) | Get LastError, Get LastErrorLocation, Get LastErrorDetail, error state, reset, single expression, JSONSetElement, error capture, Perform Script, line number, forced error, debug, $errData |
| `error-handling.md` | FileMaker error handling patterns: Get ( LastError ) timing constraint, Set Error Capture, Allow User Abort, server vs Pro differences, common error codes, and the core error-checking pattern | error handling, Get LastError, Set Error Capture, Allow User Abort, error capture, server script, PSOS, error code, 401, 301, 500, no records, record locked, validation, silent error, error dialog |
| `field-references.md` | When and how to use GetFieldName() to make string-based field references rename-safe. Covers Set Field By Name, GetField, List of field names, and dynamic field patterns | GetFieldName, Set Field By Name, GetField, field name, field reference, rename, refactor, dynamic field, string field, FieldNames, ExecuteSQL |
| `found-sets.md` | Behavioral attributes of found sets, actions that operate on a found set, methods for collecting field values across a found set, restoring a found set, and snapshot links | found set, constrain, extend, perform find, loop, replace field contents, export, snapshot, GetRecordIDsFromFoundSet, Go to List of Records, sort order |
| `script-parameters.md` | FileMaker script parameters and results: single parameter and result slot, JSON encoding for multiple values, Get ( ScriptParameter ), Exit Script result, Get ( ScriptResult ) timing constraint, calling scripts by reference | script parameter, script result, Get ScriptParameter, Get ScriptResult, Exit Script, Perform Script, JSON parameter, pass data, return value, sub-script, script reference |
| `single-pass-loop.md` | The single-pass loop as FileMaker's idiomatic try/catch equivalent: pattern structure, guaranteed Exit Loop If [ True ], multiple exit points, passing error state out, when to use and when not to, contrast with nested If/Else anti-pattern | try catch, error handling, single pass loop, Exit Loop If, Loop, exit loop, error recovery, guaranteed exit, early exit, cleanup, anti-pattern, nested if |
| `disambiguation.md` | Commonly confused FileMaker term pairs and non-negotiable structural invariants: table vs TO, stored vs auto-enter calc, commit record vs commit transaction, layout context vs script context, fmxmlsnippet vs SaXML, and key constraints on indexing, found sets, transactions, and security | disambiguation, confused terms, table occurrence, TO, stored calculation, auto-enter, commit, transaction, fmxmlsnippet, layout context, script context, custom function, Get function, List view, Table view, invariants, constraints, indexing, found set, PSOS, privilege set, security |
| `variables.md` | FileMaker variable scoping and naming conventions: $ local, $$ global, ~ calculation-local, $$~ private global, stale global gotcha, when to use globals vs locals, performance, and Let() relationship | variable, scope, local variable, global variable, $$ global, ~ tilde, Let, camelCase, variable naming, stale global, session, script scope, calculation scope, private global |
| `paste-dependency-order.md` | The correct installation order when pasting FileMaker objects into a solution: custom functions → tables → fields → value lists → scripts → layout objects → script steps → custom menus. Explains why relationships are excluded and how to diagnose broken references caused by out-of-order pasting. | paste order, dependency order, install order, custom function, table, field, value list, layout, script, custom menu, broken reference, fmxmlsnippet, clipboard, copy paste, installation sequence |
| `json-functions.md` | JSONGetElementType return constants (JSONString/Number/Object/Array/Boolean/Null = 1–6, 0 = not found), why JSONUndefined and JSONMissing do not exist, positive and negative key-existence patterns, root element type check. Array building: `[+]` appends a new element, `[:]` addresses the last element — use `[+]` for the first property of a new object and `[:]` for all remaining properties. Numeric index keys produce objects not arrays. | JSONGetElementType, JSONGetElement, JSONSetElement, JSON type, JSONUndefined, JSONMissing, JSONString, JSONNumber, JSONObject, JSONArray, JSONBoolean, JSONNull, key exists, missing key, JSON guard, JSON constant, array, append, [+], [:], array element, loop, accumulator |
| `return-delimited-lists.md` | Searching return-delimited lists correctly: the boundary bug in naked Position() calls, the ¶-wrap fix, the `ValueExists()` custom function as the preferred solution, and related Value* custom functions (ValuePosition, ValueToggle, ValuesWalk, ValuesWrap, ValueExtract) | return-delimited list, ScriptNames, LayoutNames, FieldNames, TableNames, ValueListItems, List, Position, FilterValues, ValueExists, ValuePosition, ValueToggle, ValuesWalk, ValuesWrap, ValueExtract, boundary, first value, last value, list membership, list search |
| `file-operations.md` | FileMaker file operation patterns: Delete File step (id 197) for new scripts; legacy Export Field Contents hack found in older scripts (omitting field target raises error 102 but clears the file); Show Custom Dialog InputFields structure for capturing user input | file delete, Delete File, delete file, remove file, Export Field Contents, Create Data File, error 102, field missing, overwrite, legacy, older script, Show Custom Dialog, InputFields, input field, user input, dialog, capture input, password character |
| `script-ids.md` | FileMaker object IDs are file-specific: assigned from a per-file incrementing counter, change when copied between files, not portable or stable across solutions. Never hardcode IDs in documentation — reference by name, resolve from CONTEXT.json or index files | script ID, object ID, internal ID, file-specific, copy script, Perform Script, ID mismatch, different file, CONTEXT.json, portable, incrementing, monotonic |
| `executesql.md` | ExecuteSQL function: SELECT-only, context-independent queries against TOs, case-sensitive comparisons, ISO date format requirement, `?` error return, system tables (FileMaker_Tables/Fields/BaseTableFields), ROWID/ROWMODID system columns, reserved keyword quoting, empty string vs NULL, no TOP/LIMIT, aggregate nesting prohibition, HAVING alias limitation, JOIN restrictions (no RIGHT/FULL OUTER), concatenation operators, available SQL functions, LIKE wildcards, rename-safe coding with GetFieldName, commit before ExecuteSQL to avoid full-table download on open records, avoid ExecuteSQL in auto-evaluated contexts | ExecuteSQL, SQL, SELECT, WHERE, JOIN, INNER JOIN, LEFT OUTER JOIN, aggregate, SUM, AVG, COUNT, GROUP BY, HAVING, CASE WHEN, LIKE, wildcard, ROWID, ROWMODID, FileMaker_Tables, FileMaker_Fields, FileMaker_BaseTableFields, system table, reserved keyword, date format, ISO, NULL, empty string, case sensitive, GetFieldName, COALESCE, CAST, STRVAL, NUMVAL, DISTINCT, UNION, ORDER BY, concatenation, error 8309, error 8310, question mark, commit, open record, performance, slow, PSOS, unstored calculation, tooltip, conditional formatting, hosted, server |
| `custom-menu-corruption.md` | Custom menu `<Unknown>` errors in Recover caused by menu items based on existing commands without all three override checkboxes enabled — not true corruption but a configuration issue that Recover flags | custom menu, menu set, menu item, Unknown, Recover, corruption, consistency check, override, based on existing command, keyboard shortcut, menu action, menu title, file health |
| `theme-layout-css.md` | FileMaker's 3-tier CSS cascade: theme CSS (base styles) → layout part CSS → object LocalCSS (overrides). Covers: theme-to-layout association, displayName as semantic signal, RGBA percentage colour format, FM-specific CSS extensions (-fm-font-family, -fm-icon), anonymous inline overrides, base64 SVG icon encoding in IconData/BinaryData/Stream, object stacking order via key attribute, and practical extraction strategy for building visual profiles | theme, CSS, LocalCSS, displayName, style, layout object, background-color, rgba, font, icon, SVG, base64, IconData, stacking order, key, override, cascade, visual, colour, color, gradient, border-radius, Apex Blue, fm-font-family, fm-icon, inline CSS |
| `record-locking.md` | Multi-user record locking: optimistic write-lock model, implicit lock from Set Field, silent failure when Set Error Capture suppresses error 301/306, retry with backoff for transient locks, Open Transaction for multi-record atomicity, OnWindowTransaction audit trigger, design principles to minimize contention | record lock, locking, error 301, error 306, locked by another user, modification ID, Set Field, Open Record/Request, Commit Records, multi-user, concurrent, retry, backoff, Open Transaction, Commit Transaction, OnWindowTransaction, audit log, silent failure, write lock, contention, PSOS |

---

## Conventions for knowledge files

### Referencing FileMaker steps and functions

Knowledge files frequently mention script steps and calculation functions. Each knowledge file should include a **References** section at the bottom that lists every step and function mentioned in the document with lookup paths.

The local docs in `agent/docs/filemaker/` are generated by `agent/docs/filemaker/fetch_docs.py` and may not be present. Each reference entry should provide both the local path and the Claris help URL so AI can fall back gracefully.

Format for the References section:

```
## References

| Name | Type | Local doc | Claris help |
|------|------|-----------|-------------|
| Perform Find | step | `agent/docs/filemaker/script-steps/perform-find.md` | [perform-find](https://help.claris.com/en/pro-help/content/perform-find.html) |
| GetSummary | function | `agent/docs/filemaker/functions/logical/getsummary.md` | [getsummary](https://help.claris.com/en/pro-help/content/getsummary.html) |
```

Slug rules:
- Lowercase, hyphen-separated for steps: `Replace Field Contents` -> `replace-field-contents`
- Lowercase, no separators for functions: `GetNthRecord` -> `getnthrecord`
- Local step paths: `agent/docs/filemaker/script-steps/<slug>.md`
- Local function paths: `agent/docs/filemaker/functions/<category>/<slug>.md`
- Claris URL: `https://help.claris.com/en/pro-help/content/<slug>.html`

### File naming

Use lowercase-kebab-case filenames (e.g., `found-sets.md`, `record-locking.md`) to stay consistent with the naming in `agent/docs/filemaker/script-steps/`.

### Session terminology

A "session" in FileMaker refers to the period from when a user opens a hosted file until they close it. Global fields and global variables (`$$`) are session-scoped — their values do not persist once the session ends. The initial value of a global field in a hosted file is whatever was stored before the file was hosted.

