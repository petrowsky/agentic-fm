# Context.fmfn -- Technical Reference

## Overview

`Context.fmfn` is a FileMaker custom function that introspects a live FileMaker solution and produces a JSON document (`CONTEXT.json`) containing the schema, relationships, and object IDs that an AI agent needs to generate reliable fmxmlsnippet scripts.

The function is layout-aware. When evaluated, it examines the current layout to determine which table occurrences (TOs) are in scope, then builds a complete picture of those tables, their fields, and how they relate to each other. It also collects solution-wide catalogs of scripts, layouts, and value lists.

**Requires:** FileMaker Pro 21.0+ (for `GetTableDDL` and `While` function support). For 19.x/20.x see [FileMaker 19 variant](#filemaker-19-variant).

**Parameter:** `task` (Text) -- A natural-language description of what script to create.

**Returns:** A JSON string matching the `CONTEXT.json` schema used by the agentic-fm project.

---

## How It Works

The function follows a six-stage pipeline, all within a single `Let` expression:

### Stage 1: Core Variables

Captures the current environment using `Get` functions:

| Variable | Source | Purpose |
|----------|--------|---------|
| `~fn` | `Get ( FileName )` | Solution file name |
| `~layoutName` | `Get ( LayoutName )` | Current layout name |
| `~layoutTO` | `Get ( LayoutTableName )` | Base table occurrence of the current layout |
| `~layoutId` | `GetValue ( LayoutIDs ( ~fn ) ; Get ( LayoutNumber ) )` | Numeric ID of the current layout |
| `~layoutTOid` | `ExecuteSQL` against `FileMaker_Tables` | Numeric ID of the layout's table occurrence |

### Stage 2: Discover Table Occurrences

Uses `FieldNames ( ~fn ; ~layoutName )` to get every field placed on the current layout. Fields from related TOs are returned in the format `TOName::FieldName`. A `While` loop extracts the TO prefix from each related field and builds a deduplicated list of all TOs referenced on the layout.

This is the key scoping mechanism -- only TOs that are actually used on the current layout are included in the output, keeping the context focused and token-efficient.

### Stage 3: Build Tables JSON

For each unique TO discovered in Stage 2:

1. Queries `FileMaker_Tables` for the TO ID and base table name.
2. Queries `FileMaker_BaseTables` for the base table ID.
3. Queries `FileMaker_Fields` for all fields belonging to the TO, retrieving `FieldName`, `FieldId`, `FieldType`, and `FieldClass`.
4. Maps SQL types to FileMaker types using a `Case` statement:

| SQL Type | FileMaker Type |
|----------|---------------|
| `varchar`, `text` | Text |
| `decimal`, `numeric`, `int` | Number |
| `date` | Date |
| `time` | Time |
| `timestamp`, `datetime` | Timestamp |
| `varbinary`, `blob` | Container |

5. Includes `fieldtype` (e.g. `Calculated`, `Summary`) when the field class is not `Normal`.
6. Assembles a nested JSON structure keyed by base table name.

### Stage 4: Generate DDL

Uses `GetTableDDL` to produce SQL-style DDL (CREATE TABLE statements) for all discovered TOs. The DDL output includes:

- **FOREIGN KEY constraints** -- these reveal relationships between tables.
- **Field comments** -- pulled from the field's comment attribute in FileMaker, often containing semantic descriptions useful for AI.

The `True` parameter enables comments in the output. Note that both sides of a relationship must be in the TO list for the `FOREIGN KEY` to appear.

### Stage 5: Collect Solution-Wide Catalogs

Three separate `While` loops iterate over all objects in the solution file:

- **Scripts** -- `ScriptNames` and `ScriptIDs` paired into `{ "name": { "id": N } }` objects.
- **Layouts** -- `LayoutNames` and `LayoutIDs` paired the same way.
- **Value Lists** -- queried from `FileMaker_ValueLists` via `ExecuteSQL`, including name, ID, and source type (Custom, Field, etc.).

These are solution-wide (not scoped to the current layout) because scripts commonly reference objects across the entire solution.

### Stage 6: Layout Objects

Uses `LayoutObjectNames ( ~fn ; ~layoutName )` to discover named objects on the current layout. These are relevant for `Go to Object[]` script steps.

`LayoutObjectNames` returns angle bracket characters (`<`, `>`) to indicate nesting within portals, tab controls, and other container objects. The function filters these out, keeping only actual named objects.

### Final Assembly

All sections are combined into a single JSON document using `JSONSetElement` with the following top-level keys:

| Key | Type | Contents |
|-----|------|----------|
| `solution` | String | File name |
| `task` | String | The provided task description |
| `current_layout` | Object | `name`, `id`, `base_to`, `base_to_id` |
| `tables` | Object | Keyed by base table name, each with `id`, `to`, `to_id`, `fields` |
| `ddl` | String | Full DDL output with FOREIGN KEY and comments |
| `scripts` | Object | All scripts: `{ "name": { "id": N } }` |
| `layouts` | Object | All layouts: `{ "name": { "id": N } }` |
| `value_lists` | Object | All value lists: `{ "name": { "id": N, "source": "..." } }` |
| `layout_objects` | Array | Named objects on the current layout |

---

## FileMaker System Tables

The function relies on FileMaker's built-in system tables, which are accessible via `ExecuteSQL`:

| System Table | Used For | Documentation |
|--------------|----------|---------------|
| `FileMaker_Tables` | TO names, TO IDs, base table names | [FileMaker System Tables](https://help.claris.com/en/sql-reference/content/filemaker-system-tables.html) |
| `FileMaker_BaseTables` | Base table IDs | [FileMaker System Tables](https://help.claris.com/en/sql-reference/content/filemaker-system-tables.html) |
| `FileMaker_Fields` | Field names, IDs, types, classes | [FileMaker System Tables](https://help.claris.com/en/sql-reference/content/filemaker-system-tables.html) |
| `FileMaker_ValueLists` | Value list names, IDs, sources | [FileMaker System Tables](https://help.claris.com/en/sql-reference/content/filemaker-system-tables.html) |

**SQL Reference:** https://help.claris.com/en/sql-reference/content/filemaker-system-tables.html

---

## FileMaker Design Functions Used

These functions retrieve metadata about the structure of the FileMaker solution at runtime:

| Function | Purpose | Documentation |
|----------|---------|---------------|
| `Get ( FileName )` | Current solution file name | [Get(FileName)](https://help.claris.com/en/pro-help/content/get-filename.html) |
| `Get ( LayoutName )` | Current layout name | [Get(LayoutName)](https://help.claris.com/en/pro-help/content/get-layoutname.html) |
| `Get ( LayoutTableName )` | TO assigned to the current layout | [Get(LayoutTableName)](https://help.claris.com/en/pro-help/content/get-layouttablename.html) |
| `Get ( LayoutNumber )` | Ordinal position of the current layout | [Get(LayoutNumber)](https://help.claris.com/en/pro-help/content/get-layoutnumber.html) |
| `FieldNames` | All fields on a layout (includes related TOs) | [FieldNames](https://help.claris.com/en/pro-help/content/fieldnames.html) |
| `FieldIDs` | Field IDs for a given table | [FieldIDs](https://help.claris.com/en/pro-help/content/fieldids.html) |
| `LayoutNames` | All layout names in the file | [LayoutNames](https://help.claris.com/en/pro-help/content/layoutnames.html) |
| `LayoutIDs` | All layout IDs in the file | [LayoutIDs](https://help.claris.com/en/pro-help/content/layoutids.html) |
| `ScriptNames` | All script names in the file | [ScriptNames](https://help.claris.com/en/pro-help/content/scriptnames.html) |
| `ScriptIDs` | All script IDs in the file | [ScriptIDs](https://help.claris.com/en/pro-help/content/scriptids.html) |
| `LayoutObjectNames` | Named objects on a layout | [LayoutObjectNames](https://help.claris.com/en/pro-help/content/layoutobjectnames.html) |
| `BaseTableNames` | All base table names in the file | [BaseTableNames](https://help.claris.com/en/pro-help/content/basetablenames.html) |
| `BaseTableIDs` | All base table IDs in the file | [BaseTableIDs](https://help.claris.com/en/pro-help/content/basetableids.html) |
| `TableNames` | All table occurrence names in the file | [TableNames](https://help.claris.com/en/pro-help/content/tablenames.html) |
| `TableIDs` | All table occurrence IDs in the file | [TableIDs](https://help.claris.com/en/pro-help/content/tableids.html) |
| `GetTableDDL` | SQL DDL with FOREIGN KEY and comments | [GetTableDDL](https://help.claris.com/en/pro-help/content/gettableddl.html) |

**Design Functions Overview:** https://help.claris.com/en/pro-help/content/design-functions.html

---

## FileMaker Calculation Functions Used

These are the general calculation functions used within the custom function:

| Function | Purpose |
|----------|---------|
| `Let` | Defines local variables; the entire function is a single `Let` block |
| `While` | Iterative looping (replaces recursion for building JSON arrays/objects) |
| `ExecuteSQL` | Queries system tables; uses SQL-92 dialect with `?` parameter binding |
| `JSONSetElement` | Builds JSON objects and arrays incrementally |
| `JSONObject` / `JSONString` / `JSONNumber` / `JSONArray` | JSON type constants for `JSONSetElement` |
| `GetValue` / `ValueCount` | Iterate over return-delimited lists |
| `Substitute` | Splits pipe-delimited SQL results into value lists |
| `Position` / `Left` | Parses `TOName::FieldName` to extract the TO prefix |
| `FilterValues` | Deduplicates TO names |
| `IsEmpty` / `If` / `Case` | Conditional logic |

---

## Schema Best Practices for DDL

The `GetTableDDL` function follows Claris schema best practices for SQL generation. For FOREIGN KEY relationships to appear in the DDL output, both sides of the relationship must be included in the TO list passed to the function. Since the function only discovers TOs placed on the current layout, relationships to TOs not on the layout will not appear.

**Reference:** https://help.claris.com/en/pro-help/content/schema-best-practices-for-sql-generation.html

---

## Key Design Decisions

### Layout-scoped TO discovery

Rather than including every TO in the solution (which could be dozens or hundreds), the function only includes TOs that have fields placed on the current layout. This keeps the JSON focused and minimizes token consumption when used as AI context.

### SQL type mapping

FileMaker's system tables expose SQL types (e.g. `varchar(255)`, `int`, `datetime`). The function maps these back to familiar FileMaker types (Text, Number, Date, Time, Timestamp, Container) so the AI can reason about field types without needing SQL type knowledge. When no mapping matches, the raw SQL type is passed through.

### Global and summary field handling

Fields with a `FieldClass` other than `Normal` (such as `Summary` or `Calculated`) get an additional `fieldtype` property in the JSON. Global fields are identified by their SQL type prefix (e.g. `global varchar`, `global decimal`).

### Angle bracket filtering for layout objects

`LayoutObjectNames` returns `<` and `>` as separate values to indicate nesting depth within portals, tab controls, slide panels, and other container objects. These markers are not valid object names and are filtered out, keeping only actual named objects that can be targeted by `Go to Object[]`.

---

## Useful Patterns

### Getting the current layout ID

```
GetValue ( LayoutIDs ( Get ( FileName ) ) ; Get ( LayoutNumber ) )
```

There is no `Get ( LayoutID )` function. The pattern above correlates the ordinal position from `Get ( LayoutNumber )` with the ID list from `LayoutIDs`.

### Getting the TO ID for a table occurrence name

```
ExecuteSQL (
    "SELECT TableId FROM FileMaker_Tables WHERE TableName = ?" ;
    "" ; "" ; "MyTableOccurrence"
)
```

### GetTableDDL with comments enabled

```
GetTableDDL ( "[\"" & Get ( LayoutTableName ) & "\"]" ; True )
```

The first parameter is a JSON array of TO names. The second parameter (`True`) includes field comments in the DDL output, which often contain semantic descriptions useful for AI context.

### Querying all fields for a TO

```
ExecuteSQL (
    "SELECT FieldName, FieldId, FieldType, FieldClass
     FROM FileMaker_Fields
     WHERE TableName = ?" ;
    "|" ; ¶ ; "MyTableOccurrence"
)
```

Returns pipe-delimited columns with return-delimited rows.

---

## FileMaker 19 variant

`filemaker/Context19.fmfn` (clipboard version `filemaker/context19.xml`) is `Context.fmfn` for FileMaker Pro 19.x and 20.x. The function name is still `Context`, so **Push Context** calls it unchanged. Differences:

| Part | `Context.fmfn` (21.0+) | `Context19.fmfn` (19.0+) | Where the agent finds it instead |
|------|------------------------|--------------------------|----------------------------------|
| `ddl` | `GetTableDDL` output | not present | `relationships.index` (relationships); `agent/xml_parsed/` (field comments) |
| `tables.*.id` | from `FileMaker_BaseTables` | not present | `fields.index`, `TableID` column |
| `value_lists` | `FileMaker_ValueLists` (id + source) | `ValueListNames` / `ValueListIDs` (id only) | `value_lists.index`, `SourceType` column |

Everything else (layout, tables, fields, scripts, layouts, layout objects, `generated_at`, `context_version`) is identical. Tested on FileMaker Pro 19.6.3 (Windows).
