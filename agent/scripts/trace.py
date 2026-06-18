#!/usr/bin/env python3
"""
Cross-reference tracer for FileMaker solutions.

Builds a cross-reference index by scanning all solution data sources
(fields, scripts, custom functions, layouts, relationships, value lists)
and supports targeted queries and dead-object detection.

Usage:
  python3 trace.py build  -s "Solution Name"
  python3 trace.py query  -s "Solution Name" -t field -n "Clients::Name"
  python3 trace.py query  -s "Solution Name" -t script -n "Print Invoice"
  python3 trace.py dead   -s "Solution Name" -t fields
  python3 trace.py dead   -s "Solution Name" -t scripts
  python3 trace.py dead   -s "Solution Name" -t custom_functions

Output:
  build  → writes agent/context/{solution}/xref.index
  query  → prints references to/from the named object
  dead   → prints unreferenced objects with confidence levels
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # agent/scripts/ → project root

CONTEXT_DIR = PROJECT_ROOT / "agent" / "context"
XML_PARSED_DIR = PROJECT_ROOT / "agent" / "xml_parsed"
CONFIG_DIR = PROJECT_ROOT / "agent" / "config"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

XRef = namedtuple("XRef", [
    "source_type",      # field_calc, field_auto, script, layout, custom_func, relationship, value_list
    "source_name",      # "Invoices::Client Name", "Print Invoice (ID 158)"
    "source_location",  # "calc:Clients Primary::Name", "line 14: Set Field"
    "ref_type",         # field, script, layout, value_list, custom_func, table_occurrence
    "ref_name",         # canonical: "Clients::Name" (base table, not TO)
    "ref_context",      # "via TO \"Clients Primary\"", "same table", ""
])


# Dead-object candidate computation shared by `dead` and `confirm`. Carries the
# raw inputs (xrefs + the object sets) alongside the four classified buckets so
# `confirm` can enrich candidates without recomputing.
DeadResult = namedtuple("DeadResult", [
    "xrefs",            # all XRef rows from xref.index
    "all_objects",      # set of every object of this type
    "on_layout",        # {obj_name: [layout_names]} — placed on a layout
    "system_excluded",  # set excluded by system heuristics (PKs, FKs, globals, summaries)
    "module_objects",   # {obj_name: module_label} — installed-tool objects (live)
    "high",             # no references found anywhere
    "medium",           # on a layout but not in scripts/calcs
    "low",              # excluded by heuristics
    "module",           # module objects (live, invoked externally)
])


# ---------------------------------------------------------------------------
# Built-in auto-enter type keywords (not field references)
# ---------------------------------------------------------------------------

BUILTIN_AUTO_ENTER = {
    "constantdata", "serialnumber", "creationtimestamp", "creationdate",
    "creationtime", "creationaccountname", "creationname",
    "modificationtimestamp", "modificationdate", "modificationtime",
    "modificationaccountname", "modificationname", "lastvisitedtimestamp",
}

# System fields excluded from dead-object scans
SYSTEM_FIELDS = {
    "PrimaryKey", "CreationTimestamp", "CreatedBy",
    "ModificationTimestamp", "ModifiedBy",
}


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches TO::Field references — allows spaces in both TO and field names
# Requires :: separator. Captures (TO_name, Field_name).
RE_TO_FIELD = re.compile(
    r'(?<![A-Za-z0-9_])'           # not preceded by word char
    r'([A-Za-z][A-Za-z0-9_ ]*?)'   # TO name (lazy, allows spaces)
    r'::'
    r'([A-Za-z][A-Za-z0-9_ ]*)'    # Field name (greedy, allows spaces)
)

# Script name in Perform Script step: "ScriptName"
RE_PERFORM_SCRIPT = re.compile(r'Perform Script\s*\[.*?"([^"]+)"', re.DOTALL)

# Layout name in Go to Layout / New Window: Layout: "Name"
RE_LAYOUT_REF = re.compile(r'Layout:\s*"([^"]+)"')

# Go to Related Record table reference: Show only related records: "TOName"
RE_GTRR_TABLE = re.compile(
    r'Go to Related Record\s*\[.*?From table:\s*"([^"]+)"', re.DOTALL
)


# ---------------------------------------------------------------------------
# Index loaders
# ---------------------------------------------------------------------------

def _parse_index(path, columns):
    """Parse a pipe-delimited index file into a list of dicts."""
    rows = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            row = {}
            for i, col in enumerate(columns):
                row[col] = parts[i] if i < len(parts) else ""
            rows.append(row)
    return rows


def load_fields_index(solution_dir):
    return _parse_index(
        solution_dir / "fields.index",
        ["table", "table_id", "field", "field_id", "datatype",
         "fieldtype", "auto_enter", "flags"],
    )


def load_relationships_index(solution_dir):
    return _parse_index(
        solution_dir / "relationships.index",
        ["left_to", "left_to_id", "right_to", "right_to_id",
         "join_type", "join_fields", "cascade_create", "cascade_delete"],
    )


def load_table_occurrences_index(solution_dir):
    return _parse_index(
        solution_dir / "table_occurrences.index",
        ["to_name", "to_id", "base_table", "base_table_id"],
    )


def load_scripts_index(solution_dir):
    return _parse_index(
        solution_dir / "scripts.index",
        ["name", "id", "folder"],
    )


def load_layouts_index(solution_dir):
    return _parse_index(
        solution_dir / "layouts.index",
        ["name", "id", "base_to", "base_to_id", "folder"],
    )


def load_value_lists_index(solution_dir):
    return _parse_index(
        solution_dir / "value_lists.index",
        ["name", "id", "source_type", "values"],
    )


# ---------------------------------------------------------------------------
# TO resolution
# ---------------------------------------------------------------------------

def build_to_map(to_index):
    """Build {TOName: BaseTableName} mapping."""
    return {row["to_name"]: row["base_table"] for row in to_index}


def resolve_to_field(to_name, field_name, to_map):
    """Resolve TO::Field to BaseTable::Field. Returns (canonical, context)."""
    base_table = to_map.get(to_name)
    if base_table:
        canonical = f"{base_table}::{field_name}"
        if base_table != to_name:
            context = f'via TO "{to_name}"'
        else:
            context = ""
        return canonical, context
    # TO not found in map — use as-is
    return f"{to_name}::{field_name}", f'unknown TO "{to_name}"'


# ---------------------------------------------------------------------------
# Build table of fields per base table (for unqualified field matching)
# ---------------------------------------------------------------------------

def build_fields_by_table(fields_index):
    """Build {BaseTable: [field_name, ...]} sorted by name length desc."""
    table_fields = {}
    for row in fields_index:
        table_fields.setdefault(row["table"], []).append(row["field"])
    # Sort each list by length descending for longest-match-first
    for table in table_fields:
        table_fields[table].sort(key=len, reverse=True)
    return table_fields


# ---------------------------------------------------------------------------
# Build custom function name list
# ---------------------------------------------------------------------------

def build_cf_names(solution_name):
    """Get list of custom function names and IDs from directory listing."""
    cf_dir = XML_PARSED_DIR / "custom_functions_sanitized" / solution_name
    cfs = []
    if not cf_dir.exists():
        return cfs
    for f in cf_dir.rglob("*.txt"):
        # Parse "FuncName - ID NNN.txt"
        m = re.match(r'^(.+?)\s*-\s*ID\s+(\d+)\.txt$', f.name)
        if m:
            cfs.append({"name": m.group(1), "id": m.group(2), "path": f})
    return cfs


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_field_calcs(fields_index, to_map, fields_by_table, cf_names):
    """Parse field calculations and auto-enter calcs for references."""
    refs = []
    cf_name_set = {cf["name"] for cf in cf_names}

    for row in fields_index:
        auto = row["auto_enter"]
        if not auto:
            continue

        # Determine source type and calc text
        if auto.startswith("auto:"):
            calc_text = auto[5:]
            source_type = "field_auto"
        elif auto.startswith("calc:"):
            calc_text = auto[5:]
            source_type = "field_calc"
        else:
            continue

        # Skip built-in auto-enter types
        if calc_text.strip().lower() in BUILTIN_AUTO_ENTER:
            continue

        source_name = f"{row['table']}::{row['field']}"
        source_location = auto

        # Extract TO::Field references
        for m in RE_TO_FIELD.finditer(calc_text):
            to_name, field_name = m.group(1).strip(), m.group(2).strip()
            canonical, context = resolve_to_field(to_name, field_name, to_map)
            refs.append(XRef(
                source_type, source_name, source_location,
                "field", canonical, context,
            ))

        # Extract unqualified field names (same-table references)
        # Only if no :: found — calcs with :: are cross-table
        if "::" not in calc_text:
            table_name = row["table"]
            field_list = fields_by_table.get(table_name, [])
            # Remove Self references
            calc_clean = re.sub(r'\bSelf\b', '', calc_text)
            # Match longest field names first, masking them to prevent
            # shorter names from matching as substrings
            matched_fields = []
            masked = calc_clean
            for fname in field_list:  # already sorted by length desc
                if fname == row["field"]:
                    continue  # skip self
                pattern = re.compile(
                    r'(?<![A-Za-z0-9_])'
                    + re.escape(fname)
                    + r'(?![A-Za-z0-9_])'
                )
                if pattern.search(masked):
                    matched_fields.append(fname)
                    # Mask matched text to prevent substring matches
                    masked = pattern.sub("\x00" * len(fname), masked)
            for fname in matched_fields:
                refs.append(XRef(
                    source_type, source_name, source_location,
                    "field", f"{table_name}::{fname}", "same table",
                ))

        # Extract custom function references
        for cf in cf_name_set:
            # Match CF name followed by ( or as standalone for zero-param
            pattern = re.compile(
                r'(?<![A-Za-z0-9_])'
                + re.escape(cf)
                + r'(?:\s*\(|(?![A-Za-z0-9_(]))'
            )
            if pattern.search(calc_text):
                refs.append(XRef(
                    source_type, source_name, source_location,
                    "custom_func", cf, "",
                ))

    return refs


def parse_scripts(solution_name, scripts_index, to_map, cf_names):
    """Parse sanitized script files for references."""
    refs = []
    cf_name_set = {cf["name"] for cf in cf_names}
    scripts_dir = XML_PARSED_DIR / "scripts_sanitized" / solution_name

    if not scripts_dir.exists():
        return refs

    # Build script name set for validating Perform Script targets
    script_name_set = {row["name"] for row in scripts_index}

    # Walk all .txt files
    for txt_path in sorted(scripts_dir.rglob("*.txt")):
        # Extract script name and ID from filename
        m = re.match(r'^(.+?)\s*-\s*ID\s+(\d+)\.txt$', txt_path.name)
        if not m:
            continue
        script_name = m.group(1)
        script_id = m.group(2)
        source_name = f"{script_name} (ID {script_id})"

        with open(txt_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
            line = line.rstrip("\n")
            stripped = line.strip()

            # Skip blank lines and pure comments
            if not stripped or stripped.startswith("# =") or stripped.startswith("# \t"):
                continue

            # --- TO::Field references anywhere in the line ---
            for fm in RE_TO_FIELD.finditer(line):
                to_name, field_name = fm.group(1).strip(), fm.group(2).strip()
                canonical, context = resolve_to_field(to_name, field_name, to_map)
                # Determine step type from line content
                step_type = _extract_step_type(stripped)
                refs.append(XRef(
                    "script", source_name,
                    f"line {line_num}: {step_type}",
                    "field", canonical, context,
                ))

            # --- Layout references ---
            for lm in RE_LAYOUT_REF.finditer(line):
                layout_name = lm.group(1)
                if layout_name == "<original layout>":
                    continue
                step_type = _extract_step_type(stripped)
                refs.append(XRef(
                    "script", source_name,
                    f"line {line_num}: {step_type}",
                    "layout", layout_name, "",
                ))

            # --- Perform Script references ---
            for pm in RE_PERFORM_SCRIPT.finditer(line):
                target_script = pm.group(1)
                refs.append(XRef(
                    "script", source_name,
                    f"line {line_num}: Perform Script",
                    "script", target_script, "",
                ))

            # --- Go to Related Record table ref ---
            for gm in RE_GTRR_TABLE.finditer(line):
                to_name = gm.group(1)
                refs.append(XRef(
                    "script", source_name,
                    f"line {line_num}: Go to Related Record",
                    "table_occurrence", to_name, "",
                ))

            # --- Custom function references in expressions ---
            if "[" in line or "]" in line:  # Catch both opening and continuation lines
                for cf in cf_name_set:
                    # Match CF name with parens (function call) or standalone (zero-param)
                    pattern = re.compile(
                        r'(?<![A-Za-z0-9_])'
                        + re.escape(cf)
                        + r'(?:\s*\(|(?![A-Za-z0-9_(]))'
                    )
                    if pattern.search(line):
                        step_type = _extract_step_type(stripped)
                        refs.append(XRef(
                            "script", source_name,
                            f"line {line_num}: {step_type}",
                            "custom_func", cf, "",
                        ))

    return refs


def _extract_step_type(line):
    """Extract the FM script step type from the beginning of a line."""
    # Strip leading comment markers and whitespace
    line = line.lstrip()
    if line.startswith("#"):
        return "Comment"
    # Step type is everything before the first [
    bracket = line.find("[")
    if bracket > 0:
        return line[:bracket].strip()
    return line.split()[0] if line.split() else "Unknown"


def parse_custom_functions(solution_name, to_map, cf_names):
    """Parse custom function bodies for references."""
    refs = []
    cf_name_set = {cf["name"] for cf in cf_names}

    for cf in cf_names:
        if not cf["path"].exists():
            continue

        with open(cf["path"], "r", encoding="utf-8") as f:
            body = f.read()

        source_name = f"{cf['name']} (ID {cf['id']})"

        # TO::Field references
        for m in RE_TO_FIELD.finditer(body):
            to_name, field_name = m.group(1).strip(), m.group(2).strip()
            canonical, context = resolve_to_field(to_name, field_name, to_map)
            refs.append(XRef(
                "custom_func", source_name, "calc body",
                "field", canonical, context,
            ))

        # CF-to-CF references
        for other_cf in cf_name_set:
            if other_cf == cf["name"]:
                continue
            # Match with parens (function call) or standalone (zero-param)
            pattern = re.compile(
                r'(?<![A-Za-z0-9_])'
                + re.escape(other_cf)
                + r'(?:\s*\(|(?![A-Za-z0-9_(]))'
            )
            if pattern.search(body):
                refs.append(XRef(
                    "custom_func", source_name, "calc body",
                    "custom_func", other_cf, "",
                ))

    return refs


def parse_layouts(solution_dir, solution_name, to_map):
    """Parse layout summary JSON files for field and script references."""
    refs = []
    layouts_dir = solution_dir / "layouts"

    if not layouts_dir.exists():
        return refs

    for json_path in sorted(layouts_dir.glob("*.json")):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        layout_name = data.get("layout", json_path.stem)
        layout_id = data.get("id", "?")
        source_name = f"{layout_name} (ID {layout_id})"

        # Recursively walk the JSON for field and script keys
        _walk_layout_json(data, source_name, to_map, refs)

    return refs


def _walk_layout_json(obj, source_name, to_map, refs):
    """Recursively walk layout JSON for field/script references."""
    if isinstance(obj, dict):
        # Field reference
        if "field" in obj and isinstance(obj["field"], str):
            field_ref = obj["field"]
            m = RE_TO_FIELD.match(field_ref)
            if m:
                to_name, field_name = m.group(1).strip(), m.group(2).strip()
                canonical, context = resolve_to_field(to_name, field_name, to_map)
                refs.append(XRef(
                    "layout", source_name, "field placement",
                    "field", canonical, context,
                ))

        # Script reference (button action or script trigger). Trigger dicts
        # carry an "event" key (OnObjectSave, OnLayoutKeystroke, …); buttons do
        # not. A trigger is a live caller — recording it stops trigger-only
        # scripts from being false-flagged as dead.
        if "script" in obj and isinstance(obj["script"], str) and obj["script"]:
            location = f"trigger: {obj['event']}" if obj.get("event") else "button script"
            refs.append(XRef(
                "layout", source_name, location,
                "script", obj["script"], "",
            ))

        # Recurse into all values
        for v in obj.values():
            _walk_layout_json(v, source_name, to_map, refs)

    elif isinstance(obj, list):
        for item in obj:
            _walk_layout_json(item, source_name, to_map, refs)


def parse_file_triggers(solution_name):
    """Parse file-level script triggers from metadata.xml.

    File triggers (OnFirstWindowOpen, OnWindowOpen, OnLastWindowClose, …) bind a
    script to a file-level event. Such a script has no caller in any script,
    button or layout — it is invoked by the file itself — so without this it is
    false-flagged as dead. Emits file → script references.
    """
    refs = []
    meta_path = XML_PARSED_DIR / "_" / solution_name / "metadata.xml"
    if not meta_path.exists():
        return refs
    try:
        root = ET.parse(meta_path).getroot()
    except (ET.ParseError, OSError):
        return refs

    for trig in root.iter("ScriptTrigger"):
        script_ref = trig.find("ScriptReference")
        if script_ref is None:
            continue
        name = script_ref.get("name", "")
        if not name:
            continue
        event = trig.get("action", "")
        refs.append(XRef(
            "file", "File", f"trigger: {event}",
            "script", name, "",
        ))
    return refs


def parse_relationships(relationships_index, to_map):
    """Parse relationship join fields as references."""
    refs = []

    for row in relationships_index:
        left_to = row["left_to"]
        right_to = row["right_to"]
        join_fields = row["join_fields"]
        source_name = f"{left_to}\u2192{right_to}"

        if not join_fields:
            continue

        # Handle multi-predicate joins (joined with +)
        predicates = join_fields.split("+")
        for pred in predicates:
            parts = pred.split("=", 1)
            if len(parts) != 2:
                continue
            left_field = parts[0].strip()
            right_field = parts[1].strip()

            # Left field
            left_base = to_map.get(left_to, left_to)
            refs.append(XRef(
                "relationship", source_name, "join field",
                "field", f"{left_base}::{left_field}", "left side",
            ))

            # Right field
            right_base = to_map.get(right_to, right_to)
            refs.append(XRef(
                "relationship", source_name, "join field",
                "field", f"{right_base}::{right_field}", "right side",
            ))

    return refs


def parse_value_lists(solution_name, to_map):
    """Parse value list XML files for field-based VL references."""
    refs = []
    vl_dir = XML_PARSED_DIR / "value_lists" / solution_name

    if not vl_dir.exists():
        return refs

    for xml_path in sorted(vl_dir.glob("*.xml")):
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except (ET.ParseError, OSError):
            continue

        # Get VL name and ID
        vl_ref = root.find(".//ValueListReference")
        if vl_ref is None:
            continue
        vl_name = vl_ref.get("name", "")
        vl_id = vl_ref.get("id", "?")
        source_name = f"{vl_name} (ID {vl_id})"

        # Check source type
        source_el = root.find(".//Source")
        if source_el is None or source_el.get("value") != "FromField":
            continue

        # Primary field
        pf = root.find(".//PrimaryField/FieldReference")
        if pf is not None:
            fname = pf.get("name", "")
            to_el = pf.find("TableOccurrenceReference")
            to_name = to_el.get("name", "") if to_el is not None else ""
            if to_name and fname:
                canonical, context = resolve_to_field(to_name, fname, to_map)
                refs.append(XRef(
                    "value_list", source_name, "primary field",
                    "field", canonical, context,
                ))

        # Secondary field
        sf = root.find(".//SecondaryField/FieldReference")
        if sf is not None:
            fname = sf.get("name", "")
            to_el = sf.find("TableOccurrenceReference")
            to_name = to_el.get("name", "") if to_el is not None else ""
            if to_name and fname:
                canonical, context = resolve_to_field(to_name, fname, to_map)
                refs.append(XRef(
                    "value_list", source_name, "secondary field",
                    "field", canonical, context,
                ))

    return refs


# ---------------------------------------------------------------------------
# Build command
# ---------------------------------------------------------------------------

def cmd_build(solution_name):
    """Build xref.index for the given solution."""
    solution_dir = CONTEXT_DIR / solution_name

    if not solution_dir.exists():
        print(f"ERROR: No context directory for '{solution_name}'", file=sys.stderr)
        print(f"  Expected: {solution_dir}", file=sys.stderr)
        sys.exit(1)

    # Load index files
    fields_index = load_fields_index(solution_dir)
    relationships_index = load_relationships_index(solution_dir)
    to_index = load_table_occurrences_index(solution_dir)
    scripts_index = load_scripts_index(solution_dir)

    # Build helpers
    to_map = build_to_map(to_index)
    fields_by_table = build_fields_by_table(fields_index)
    cf_names = build_cf_names(solution_name)

    print(f"==> Building xref.index for: {solution_name}")
    print(f"  Fields: {len(fields_index)}, TOs: {len(to_index)}, "
          f"Scripts: {len(scripts_index)}, CFs: {len(cf_names)}")

    all_refs = []

    # 1. Field calculations
    print("  Parsing field calculations...")
    field_refs = parse_field_calcs(fields_index, to_map, fields_by_table, cf_names)
    all_refs.extend(field_refs)
    print(f"    {len(field_refs)} references found")

    # 2. Relationships
    print("  Parsing relationships...")
    rel_refs = parse_relationships(relationships_index, to_map)
    all_refs.extend(rel_refs)
    print(f"    {len(rel_refs)} references found")

    # 3. Scripts
    print("  Parsing scripts...")
    script_refs = parse_scripts(solution_name, scripts_index, to_map, cf_names)
    all_refs.extend(script_refs)
    print(f"    {len(script_refs)} references found")

    # 4. Layouts
    print("  Parsing layout summaries...")
    layouts_dir = solution_dir / "layouts"
    layout_summaries_missing = not layouts_dir.exists() or not any(layouts_dir.glob("*.json"))
    layout_refs = parse_layouts(solution_dir, solution_name, to_map)
    all_refs.extend(layout_refs)
    print(f"    {len(layout_refs)} references found")
    if layout_summaries_missing:
        print(
            "  ⚠️  WARNING: no layout summaries found at "
            f"context/{solution_name}/layouts/.\n"
            "      Layout placements, button scripts and script TRIGGERS are "
            "therefore MISSING from the xref index.\n"
            "      Dead-object results will contain false positives "
            "(trigger-only / layout-only objects look orphaned).\n"
            "      Generate them first:\n"
            f"        python3 agent/scripts/layout_to_summary.py --solution \"{solution_name}\"\n"
            "      then rebuild the xref index.",
            file=sys.stderr,
        )

    # 4b. File-level script triggers (metadata.xml)
    print("  Parsing file-level triggers...")
    file_trig_refs = parse_file_triggers(solution_name)
    all_refs.extend(file_trig_refs)
    print(f"    {len(file_trig_refs)} references found")

    # 5. Custom functions
    print("  Parsing custom functions...")
    cf_refs = parse_custom_functions(solution_name, to_map, cf_names)
    all_refs.extend(cf_refs)
    print(f"    {len(cf_refs)} references found")

    # 6. Value lists
    print("  Parsing value lists...")
    vl_refs = parse_value_lists(solution_name, to_map)
    all_refs.extend(vl_refs)
    print(f"    {len(vl_refs)} references found")

    # Write xref.index
    xref_path = solution_dir / "xref.index"
    with open(xref_path, "w", encoding="utf-8") as f:
        f.write("# SourceType|SourceName|SourceLocation|RefType|RefName|RefContext\n")
        for ref in all_refs:
            # Escape pipes in fields
            row = [
                ref.source_type,
                _escape_pipe(ref.source_name),
                _escape_pipe(ref.source_location),
                ref.ref_type,
                _escape_pipe(ref.ref_name),
                _escape_pipe(ref.ref_context),
            ]
            f.write("|".join(row) + "\n")

    print(f"\n==> Done! {len(all_refs)} total references")
    print(f"  Output: {xref_path}")


def _escape_pipe(s):
    """Escape pipe characters in index values."""
    return s.replace("|", "\\|")


def _unescape_pipe(s):
    """Unescape pipe characters in index values."""
    return s.replace("\\|", "|")


# ---------------------------------------------------------------------------
# Query command
# ---------------------------------------------------------------------------

def load_xref(solution_dir):
    """Load xref.index into list of XRef tuples."""
    xref_path = solution_dir / "xref.index"
    if not xref_path.exists():
        print(f"ERROR: xref.index not found. Run 'build' first.", file=sys.stderr)
        sys.exit(1)

    refs = []
    with open(xref_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            # Split on unescaped pipes
            parts = re.split(r'(?<!\\)\|', line)
            if len(parts) < 6:
                continue
            refs.append(XRef(
                _unescape_pipe(parts[0]),
                _unescape_pipe(parts[1]),
                _unescape_pipe(parts[2]),
                _unescape_pipe(parts[3]),
                _unescape_pipe(parts[4]),
                _unescape_pipe(parts[5]),
            ))
    return refs


def cmd_query(solution_name, ref_type, ref_name, direction):
    """Query references to/from an object."""
    solution_dir = CONTEXT_DIR / solution_name
    to_index = load_table_occurrences_index(solution_dir)
    to_map = build_to_map(to_index)

    # Resolve TO-qualified input to canonical form
    canonical_name = ref_name
    if ref_type == "field" and "::" in ref_name:
        parts = ref_name.split("::", 1)
        to_name, field_name = parts[0], parts[1]
        base = to_map.get(to_name, to_name)
        canonical_name = f"{base}::{field_name}"

    xrefs = load_xref(solution_dir)

    if direction == "inbound":
        # Who references this object?
        matches = [x for x in xrefs
                   if x.ref_type == ref_type and x.ref_name == canonical_name]
    else:
        # What does this object reference?
        matches = [x for x in xrefs
                   if x.source_name == ref_name or x.source_name == canonical_name]

    if not matches:
        print(f"No {'inbound' if direction == 'inbound' else 'outbound'} "
              f"references found for {ref_type}: {ref_name}")
        if canonical_name != ref_name:
            print(f"  (resolved to canonical: {canonical_name})")
        return

    # Group by source type
    label = "References to" if direction == "inbound" else "References from"
    print(f"=== {label} {ref_type}: {canonical_name} ===\n")

    groups = {}
    for ref in matches:
        key = ref.source_type if direction == "inbound" else ref.ref_type
        groups.setdefault(key, []).append(ref)

    # Display order
    type_labels = {
        "field_calc": "FIELD CALCULATIONS",
        "field_auto": "FIELD AUTO-ENTER",
        "script": "SCRIPTS",
        "layout": "LAYOUTS",
        "custom_func": "CUSTOM FUNCTIONS",
        "relationship": "RELATIONSHIPS",
        "value_list": "VALUE LISTS",
        "field": "FIELDS",
        "table_occurrence": "TABLE OCCURRENCES",
    }

    for group_key in type_labels:
        if group_key not in groups:
            continue
        items = groups[group_key]
        print(f"{type_labels[group_key]} ({len(items)}):")
        for ref in items:
            if direction == "inbound":
                ctx = f" \u2014 {ref.ref_context}" if ref.ref_context else ""
                print(f"  {ref.source_name}, {ref.source_location}{ctx}")
            else:
                ctx = f" \u2014 {ref.ref_context}" if ref.ref_context else ""
                print(f"  {ref.ref_type}: {ref.ref_name} ({ref.source_location}){ctx}")
        print()

    print(f"Summary: {len(matches)} references across {len(groups)} source type(s)")


# ---------------------------------------------------------------------------
# Dead object scan
# ---------------------------------------------------------------------------

def _dead_reliability_warning(solution_name, obj_type, xrefs):
    """Emit the layout-refs reliability warning when those edges are missing.

    Dead-object detection for scripts/fields/value_lists leans on layout
    references (placements, button scripts, triggers). If the xref has no
    layout-sourced refs, those edges are missing and the results will
    over-report "dead" objects. Warn loudly rather than mislead a human about to
    delete things. Shared by `dead` and `confirm`.
    """
    if obj_type not in ("scripts", "fields", "value_lists"):
        return
    has_layout_refs = any(ref.source_type == "layout" for ref in xrefs)
    if has_layout_refs:
        return
    print(
        "⚠️  WARNING: xref.index contains NO layout references — "
        f"'{obj_type}' dead results are UNRELIABLE.\n"
        "    Layout placements, button scripts and script triggers are "
        "missing, so trigger-only / layout-only objects will be "
        "falsely flagged as dead.\n"
        "    Regenerate layout summaries and rebuild before trusting "
        "this output:\n"
        f"      python3 agent/scripts/layout_to_summary.py --solution \"{solution_name}\"\n"
        f"      python3 agent/scripts/trace.py build -s \"{solution_name}\"\n",
        file=sys.stderr,
    )


def compute_dead_candidates(solution_name, obj_type):
    """Compute unreferenced objects classified into confidence buckets.

    Pure computation (no printing) shared by `dead` and `confirm`. Returns a
    DeadResult with the four buckets plus the raw inputs the caller may need.
    """
    solution_dir = CONTEXT_DIR / solution_name
    xrefs = load_xref(solution_dir)

    # Build set of all referenced objects by type
    referenced = set()
    for ref in xrefs:
        if ref.ref_type == _dead_ref_type(obj_type):
            referenced.add(ref.ref_name)

    # Build set of all objects of this type
    all_objects, on_layout, system_excluded, module_objects = _get_all_objects(
        solution_dir, solution_name, obj_type, xrefs,
    )

    # Compute dead = all - referenced
    unreferenced = all_objects - referenced

    # Classify confidence
    high = []
    medium = []
    low = []
    module = []

    for obj in sorted(unreferenced):
        if obj in module_objects:
            module.append(obj)
        elif obj in system_excluded:
            low.append(obj)
        elif obj in on_layout:
            medium.append(obj)
        else:
            high.append(obj)

    return DeadResult(
        xrefs, all_objects, on_layout, system_excluded, module_objects,
        high, medium, low, module,
    )


def cmd_dead(solution_name, obj_type, verbose):
    """Find unreferenced objects."""
    res = compute_dead_candidates(solution_name, obj_type)
    xrefs = res.xrefs
    on_layout = res.on_layout
    module_objects = res.module_objects
    high, medium, low, module = res.high, res.medium, res.low, res.module
    all_objects = res.all_objects

    # Reliability guard (shared with confirm)
    _dead_reliability_warning(solution_name, obj_type, xrefs)

    # Display
    print(f"=== Potentially unused {obj_type} ({solution_name}) ===\n")

    if high:
        print(f"HIGH CONFIDENCE — no references found anywhere ({len(high)}):")
        for obj in high:
            print(f"  {obj}")
        print()

    if medium:
        print(f"MEDIUM CONFIDENCE — on a layout but not in scripts/calcs ({len(medium)}):")
        for obj in medium:
            layouts = on_layout.get(obj, [])
            layout_str = ", ".join(layouts[:3])
            if len(layouts) > 3:
                layout_str += f" (+{len(layouts) - 3} more)"
            print(f"  {obj} \u2014 on layout: {layout_str}")
        print()

    if module:
        print(f"MODULE — installed tool objects, invoked externally — NOT dead ({len(module)}):")
        for obj in module:
            print(f"  {obj} — {module_objects[obj]}")
        print()

    if verbose and low:
        print(f"LOW CONFIDENCE — excluded by heuristics ({len(low)}):")
        for obj in low:
            print(f"  {obj}")
        print()

    total = len(all_objects)
    parts = [f"{len(high)} high", f"{len(medium)} medium"]
    if verbose:
        parts.append(f"{len(low)} low")
    tail = f" + {len(module)} module (live)" if module else ""
    print(f"Summary: {', '.join(parts)} unused{tail} "
          f"out of {total} total {obj_type}")


def _dead_ref_type(obj_type):
    """Map dead scan object type to xref ref_type."""
    mapping = {
        "fields": "field",
        "scripts": "script",
        "custom_functions": "custom_func",
        "layouts": "layout",
        "value_lists": "value_list",
    }
    return mapping.get(obj_type, obj_type)


def load_modules():
    """Load installed-module definitions.

    Modules are third-party tools (agentic-fm, InspectorPro, OttoFMS, …) whose
    objects are live but have no inbound references *inside* the solution — they
    are invoked externally (OData / fmurlscript / a companion app) or managed by
    the module itself. We surface them separately so they are never mistaken for
    the solution's own dead code.

    Definitions come from the shipped defaults (``modules.json.example``),
    overlaid by the developer's optional ``modules.json`` (merged by ``label``,
    so a user file does not need to re-declare the agentic-fm default).
    """
    by_label = {}
    for filename in ("modules.json.example", "modules.json"):
        path = CONFIG_DIR / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("modules", []):
            label = entry.get("label")
            if label:
                by_label[label] = entry  # later file (user) overrides by label
    return list(by_label.values())


def match_module(name, folder, modules):
    """Return the matching module's label for (name, folder), else None.

    Matching is by object NAME first (folder-independent): an exact name match
    or a registered name prefix. ``folder_contains`` is a secondary hint for
    tools that live in a known folder. A match on ANY signal tags the object.
    """
    folder_l = (folder or "").lower()
    for mod in modules:
        if name in mod.get("name_exact", []):
            return mod["label"]
        for prefix in mod.get("name_prefixes", []):
            if prefix and name.startswith(prefix):
                return mod["label"]
        for token in mod.get("folder_contains", []):
            if token and token.lower() in folder_l:
                return mod["label"]
    return None


def _get_all_objects(solution_dir, solution_name, obj_type, xrefs):
    """Get all objects, plus layout-only, system-excluded and module objects."""
    system_excluded = set()
    on_layout = {}  # {obj_name: [layout_names]}
    module_objects = {}  # {obj_name: module_label}
    modules = load_modules()

    if obj_type == "fields":
        fields_index = load_fields_index(solution_dir)
        all_objects = set()
        for row in fields_index:
            canonical = f"{row['table']}::{row['field']}"
            all_objects.add(canonical)

            label = match_module(row["field"], "", modules) or match_module(row["table"], "", modules)
            if label:
                module_objects[canonical] = label

            # System exclusions
            if row["field"] in SYSTEM_FIELDS:
                system_excluded.add(canonical)
            elif row["field"].startswith("ForeignKey") or row["field"].startswith("FK"):
                system_excluded.add(canonical)
            elif "global" in row.get("flags", ""):
                system_excluded.add(canonical)
            elif row["fieldtype"] == "Summary":
                system_excluded.add(canonical)

        # Find fields that are only on layouts
        for ref in xrefs:
            if ref.ref_type == "field" and ref.source_type == "layout":
                on_layout.setdefault(ref.ref_name, []).append(
                    ref.source_name.split(" (ID")[0]
                )

    elif obj_type == "scripts":
        scripts_index = load_scripts_index(solution_dir)
        all_objects = set()
        for row in scripts_index:
            all_objects.add(row["name"])
            label = match_module(row["name"], row.get("folder", ""), modules)
            if label:
                module_objects[row["name"]] = label

        # Find scripts only on layouts
        for ref in xrefs:
            if ref.ref_type == "script" and ref.source_type == "layout":
                on_layout.setdefault(ref.ref_name, []).append(
                    ref.source_name.split(" (ID")[0]
                )

    elif obj_type == "custom_functions":
        cf_names = build_cf_names(solution_name)
        all_objects = set()
        for cf in cf_names:
            all_objects.add(cf["name"])
            label = match_module(cf["name"], "", modules)
            if label:
                module_objects[cf["name"]] = label

    elif obj_type == "layouts":
        layouts_index = load_layouts_index(solution_dir)
        all_objects = set()
        for row in layouts_index:
            all_objects.add(row["name"])
            label = match_module(row["name"], row.get("folder", ""), modules)
            if label:
                module_objects[row["name"]] = label

    elif obj_type == "value_lists":
        vl_index = load_value_lists_index(solution_dir)
        all_objects = set()
        for row in vl_index:
            all_objects.add(row["name"])
            label = match_module(row["name"], "", modules)
            if label:
                module_objects[row["name"]] = label

    else:
        all_objects = set()

    return all_objects, on_layout, system_excluded, module_objects


# ---------------------------------------------------------------------------
# Confirm — batch the deterministic judgment layer over dead candidates
# ---------------------------------------------------------------------------

# Names matching these patterns are external entry points (called by OData /
# fmurlscript / a scheduler / an import pipeline), so a zero-inbound count does
# NOT mean dead — flag for human review rather than auto-judging.
ENTRY_POINT_RE = re.compile(
    r'(^(Import|Populate|Export|Sync|Schedule|Cron|Webhook|API|Generate)\b)'
    r'|(JSON|SearchIndex|Release_?Notes|Batch|Nightly)',
    re.IGNORECASE,
)


# Name-string hit KINDS, by how live a literal mention is. A mention in cosmetic
# text (a placeholder/label) cannot invoke anything; a mention in a calc/hideWhen
# is a live reference; a mention in a Perform-Script/Go-to-Layout step is a caller.
# Surfaced so the operator can accept/dismiss a `review` row at a glance instead of
# re-opening it. Labeling only — it does NOT change disposition.
HIT_KIND_RANK = {"benign": 0, "calc": 1, "caller": 2}

# Layout-summary JSON keys whose string values are purely cosmetic (cannot
# reference/invoke the candidate). Everything else defaults to "calc" (rescue).
_LAYOUT_BENIGN_KEYS = {
    "placeholder", "tooltip", "label", "text", "styleName", "displayName",
    "theme", "iconDesc", "style", "class",
}
_LAYOUT_CALLER_KEYS = {"script"}


# Merge-field syntax inside any text/label — `<<Table::Field>>` or `<<Field>>`.
# A merge field is a LIVE data reference (it renders the field's value on the
# layout), NOT cosmetic text — so its hit must be classified `calc`, never benign,
# regardless of which key the surrounding text sits under.
RE_MERGE_FIELD = re.compile(r'<<[^<>]+>>')


def _layout_segments(obj, out):
    """Flatten a layout summary into (kind, text) segments by JSON key context."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if k in _LAYOUT_BENIGN_KEYS:
                    kind = "benign"
                elif k in _LAYOUT_CALLER_KEYS:
                    kind = "caller"
                else:
                    kind = "calc"
                out.append((kind, v))
                # Any embedded merge field is a live reference even when the
                # surrounding value is cosmetic (e.g. a static-text block).
                if kind != "calc" and "<<" in v:
                    for mf in RE_MERGE_FIELD.findall(v):
                        out.append(("calc", mf))
            else:
                _layout_segments(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _layout_segments(item, out)


def _script_segments(text):
    """Split a sanitized script into (kind, line) segments by step context."""
    segs = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            kind = "benign"  # comment
        elif "Perform Script" in s or "Go to Layout" in s:
            kind = "caller"  # a name here is (or could be) a live call target
        else:
            kind = "calc"
        segs.append((kind, line))
    return segs


def build_name_corpus(solution_name, solution_dir, fields_index, cf_names):
    """Collect every place an object NAME could appear as a literal string.

    Returns a list of {kind, origin, name, segments, text} entries spanning
    sanitized scripts, layout summaries, field calcs and custom-function bodies.
    `segments` is a list of (hit_kind, text) so a hit can be labelled benign /
    calc / caller; `text` is the joined body (for dynamic-construct scanning).
    This is the dynamic-dispatch blind-spot catch: a name appearing as a literal
    string (Perform Script by name, a calculated layout name, ExecuteSQL) is
    invisible to the structured xref parser but visible here.
    """
    corpus = []

    def _add(kind, origin, name, segments):
        corpus.append({
            "kind": kind,
            "origin": origin,
            "name": name,
            "segments": segments,
            "text": "\n".join(t for _, t in segments),
        })

    # Scripts (reuse parse_scripts' filename convention)
    scripts_dir = XML_PARSED_DIR / "scripts_sanitized" / solution_name
    if scripts_dir.exists():
        for txt_path in sorted(scripts_dir.rglob("*.txt")):
            m = re.match(r'^(.+?)\s*-\s*ID\s+(\d+)\.txt$', txt_path.name)
            if not m:
                continue
            try:
                text = txt_path.read_text(encoding="utf-8")
            except OSError:
                continue
            _add("script", f"{m.group(1)} (ID {m.group(2)})", m.group(1),
                 _script_segments(text))

    # Layout summaries (segmented by key — placeholder/label vs hideWhen/param)
    layouts_dir = solution_dir / "layouts"
    if layouts_dir.exists():
        for jp in sorted(layouts_dir.glob("*.json")):
            try:
                data = json.loads(jp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            base = jp.stem  # "Layout Name - ID nn"
            segs = []
            _layout_segments(data, segs)
            _add("layout", base, base.rsplit(" - ID", 1)[0], segs)

    # Field calcs / auto-enter (reuse the auto:/calc: prefix convention)
    for row in fields_index:
        auto = row.get("auto_enter", "")
        if auto.startswith("auto:") or auto.startswith("calc:"):
            canonical = f"{row['table']}::{row['field']}"
            _add("field_calc", canonical, canonical, [("calc", auto[5:])])

    # Custom function bodies
    for cf in cf_names:
        if not cf["path"].exists():
            continue
        try:
            text = cf["path"].read_text(encoding="utf-8")
        except OSError:
            continue
        _add("custom_func", f"{cf['name']} (ID {cf['id']})", cf["name"],
             [("calc", text)])

    return corpus


def scan_dynamic_constructs(corpus):
    """Solution-level presence of dynamic-dispatch constructs (booleans).

    These let a name reach an object without a structured reference. Combined
    with a name-string hit (see cmd_confirm) they raise dynamic-reachable.
    """
    all_text = "\n".join(e["text"] for e in corpus)
    return {
        "ExecuteSQL": bool(re.search(r'ExecuteSQL\s*\(', all_text)),
        "SetFieldByName": bool(re.search(r'Set Field By Name\s*\[', all_text)),
        "GoToLayoutByVar": bool(re.search(r'Go to Layout\s*\[\s*Layoutname:', all_text)),
        "Evaluate": bool(re.search(r'\bEvaluate\s*\(', all_text)),
        "PerformByName": bool(re.search(r'Perform Script\s*\[\s*Specified:\s*By name', all_text)),
    }


def canvas_verdict(bounds, width, canvas_height, nested):
    """Classify a single placement's position relative to the canvas.

    off-canvas only when an object is FULLY outside the canvas on one axis.
    Nested objects (inside a Portal/Group/Button Bar) report bounds in a parent
    coordinate space in some FM versions, so their off-canvas reading is marked
    low-confidence ('off-canvas?') and must never drive a delete on its own.
    """
    top, left, bottom, right = bounds
    if width <= 0:
        return "unknown"
    if right <= left or bottom <= top:
        return "zero-size"
    off = (
        left >= width
        or right <= 0
        or (canvas_height > 0 and top >= canvas_height)
        or bottom <= 0
    )
    if off:
        return "off-canvas?" if nested else "off-canvas"
    return "on-canvas"


def build_layout_object_index(solution_dir, to_map):
    """Map canonical field -> [placement verdicts] from layout summaries.

    Reuses the bounds/width data layout_to_summary.py already emits, so no raw
    layout XML is re-read. Tracks nesting depth so child-object bounds are
    flagged low-confidence (parent coordinate space).
    """
    index = {}
    layouts_dir = solution_dir / "layouts"
    if not layouts_dir.exists():
        return index

    def _walk(obj, nested, width, canvas_height, layout_name):
        if isinstance(obj, dict):
            bounds = obj.get("bounds")
            field = obj.get("field")
            if bounds and isinstance(field, str) and "::" in field:
                to_name, fname = field.split("::", 1)
                canonical, _ = resolve_to_field(to_name.strip(), fname.strip(), to_map)
                index.setdefault(canonical, []).append({
                    "layout": layout_name,
                    "verdict": canvas_verdict(bounds, width, canvas_height, nested),
                })
            # Recurse into container children (Group/Portal objects, Button Bar buttons)
            for key in ("objects", "buttons"):
                for child in obj.get(key, []) or []:
                    _walk(child, True, width, canvas_height, layout_name)

    for jp in sorted(layouts_dir.glob("*.json")):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        width = int(data.get("width", 0) or 0)
        canvas_height = sum(int(p.get("height", 0) or 0) for p in data.get("parts", []))
        layout_name = data.get("layout", jp.stem)
        for part in data.get("parts", []):
            for obj in part.get("objects", []) or []:
                _walk(obj, False, width, canvas_height, layout_name)

    return index


def aggregate_canvas(canonical_field, layout_index):
    """Aggregate a field's placements into one verdict + placement count.

    Any on-canvas (or unknown — assumed visible) placement ⇒ on-canvas (the
    field is reachable by a user). Only when EVERY placement is parked do we
    report off-canvas. Returns (verdict, n_placements) or (None, 0).
    """
    places = layout_index.get(canonical_field, [])
    if not places:
        return None, 0
    verdicts = [p["verdict"] for p in places]
    n = len(places)
    if any(v in ("on-canvas", "unknown") for v in verdicts):
        return "on-canvas", n
    if any(v == "off-canvas" for v in verdicts):
        return "off-canvas", n
    if any(v == "off-canvas?" for v in verdicts):
        return "off-canvas?", n
    return "zero-size", n


def name_string_hits(probes, is_self, corpus, is_live_referrer=None):
    """Count literal name-string hits across the corpus, SELF vs EXTERNAL.

    probes: list of literal strings to search (word-bounded). is_self(entry):
    True when the corpus entry is the candidate's own definition (so a script
    mentioning its own name in a header comment doesn't rescue itself).

    Counting is per-origin (one entry = at most one hit) — identical to before,
    so dispositions are unchanged. Each matching origin is additionally labelled
    by the most-live segment it matched (benign < calc < caller); the aggregate
    external `hit_kind` lets the operator triage the `review` bucket at a glance.
    """
    pats = [
        re.compile(r'(?<![A-Za-z0-9_])' + re.escape(p) + r'(?![A-Za-z0-9_])')
        for p in probes if p
    ]
    external = 0
    self_hits = 0
    samples = []
    ext_kind = None
    has_live_referrer = False  # a calc/caller hit whose referrer is itself live (kept)
    live_referrer = None
    held_referrer = None       # a calc/caller hit whose referrer is itself a candidate
    for entry in corpus:
        best = None
        for skind, stext in entry["segments"]:
            if any(p.search(stext) for p in pats):
                if best is None or HIT_KIND_RANK[skind] > HIT_KIND_RANK[best]:
                    best = skind
        if best is None:
            continue
        if is_self(entry):
            self_hits += 1
        else:
            external += 1
            if ext_kind is None or HIT_KIND_RANK[best] > HIT_KIND_RANK[ext_kind]:
                ext_kind = best
            # Referrer-liveness: a calc/caller reference is only a confident "keep"
            # if the referring object is itself live (not a removal candidate).
            if best in ("calc", "caller") and is_live_referrer is not None:
                if is_live_referrer(entry):
                    has_live_referrer = True
                    if live_referrer is None:
                        live_referrer = entry["origin"]
                elif held_referrer is None:
                    held_referrer = entry["origin"]
            if len(samples) < 2:
                samples.append((entry["kind"], entry["origin"], best))
    return {"external": external, "self": self_hits,
            "samples": samples, "hit_kind": ext_kind,
            "has_live_referrer": has_live_referrer,
            "live_referrer": live_referrer, "held_referrer": held_referrer}


def external_entry_hint(display_name):
    """Return the matched entry-point token if name looks like an external entry point."""
    target = display_name.split("::", 1)[1] if "::" in display_name else display_name
    m = ENTRY_POINT_RE.search(target)
    return m.group(0) if m else None


# Which dynamic constructs can reach each object type
_DYNAMIC_FOR_TYPE = {
    "scripts": ("PerformByName", "Evaluate"),
    "fields": ("SetFieldByName", "ExecuteSQL", "Evaluate"),
    "layouts": ("GoToLayoutByVar",),
    "value_lists": ("Evaluate",),
    "custom_functions": ("Evaluate",),
}


def _candidate_probes(obj_type, name, unique_field_names=None):
    """Literal name strings to search for this candidate.

    For fields, always probe the qualified `Table::Field`. The bare field name is
    added ONLY when it is unique across tables — otherwise it collides with a
    same-named field on another table, falsely attributing the other field's
    references to this candidate. The bare probe is still needed for unqualified
    merge fields like `<<FieldName>>`, which is exactly why it is kept for unique
    names.
    """
    if obj_type == "fields" and "::" in name:
        field = name.split("::", 1)[1]
        probes = [name]
        if unique_field_names is None or field in unique_field_names:
            probes.append(field)
        return probes
    return [name]


def _candidate_self_test(obj_type, name):
    """Predicate: is a corpus entry this candidate's OWN definition?"""
    kind = {
        "scripts": "script", "fields": "field_calc",
        "custom_functions": "custom_func", "layouts": "layout",
    }.get(obj_type)
    if kind is None:  # value_lists have no body text of their own
        return lambda e: False
    return lambda e: e["kind"] == kind and e["name"] == name


def _enrich_candidate(name, bucket, obj_type, corpus, dyn_flags,
                      layout_index, on_layout, canvas_strict,
                      unique_field_names=None, is_live_referrer=None):
    """Compute all deterministic signals + a conservative disposition."""
    hits = name_string_hits(_candidate_probes(obj_type, name, unique_field_names),
                            _candidate_self_test(obj_type, name), corpus,
                            is_live_referrer)

    relevant = _DYNAMIC_FOR_TYPE.get(obj_type, ())
    construct_present = any(dyn_flags.get(c) for c in relevant)
    # A benign-only hit (placeholder/label/comment) can't be a dynamic call target,
    # so require a calc/caller-kind external hit. Disposition-neutral: dynamic
    # "possible" only ever co-occurs with external>0, which already rescues.
    live_hit = hits.get("hit_kind") in ("calc", "caller")
    dynamic = "possible" if (live_hit and construct_present) else "none"

    canvas, canvas_n = (None, 0)
    if obj_type == "fields":
        canvas, canvas_n = aggregate_canvas(name, layout_index)

    entry = external_entry_hint(name)

    # Conservative disposition — never auto-delete. likely-dead only when every
    # structural/name/dynamic/entry signal is silent (and, under --canvas-strict,
    # the field is not on-canvas).
    # Referrer-liveness promotion is scoped to FIELDS: field probes are
    # table-qualified (or unique-bare), so a calc/caller hit precisely identifies
    # the field. Layout/VL/script names are unqualified and can collide with table
    # names, keywords and literals (a layout whose name matches a table name; a
    # value list named after a boolean/keyword), so promoting those on a
    # name-string hit would falsely mark a dead object live. Those types stay on
    # conservative review.
    promote_live = obj_type == "fields" and hits.get("has_live_referrer")
    if promote_live:
        # Referenced (calc/caller) by an object that is itself live/kept — the
        # candidate is therefore kept too. Confident enough to skip at a glance.
        disposition = "likely-live"
    elif entry:
        disposition = "review"
    elif dynamic == "possible":
        disposition = "review"
    elif hits["external"] > 0:
        # External hit(s), but the referrer is itself a removal candidate (held) or
        # the hit is cosmetic (benign) — genuinely ambiguous, needs a human.
        disposition = "review"
    elif canvas == "on-canvas":
        # Visibly placed on a layout (even if the structured xref missed it via a
        # TO-resolution mismatch) — present to users, never auto-judge as dead.
        disposition = "review"
    elif bucket == "medium":
        if canvas_strict and obj_type == "fields" and canvas in ("off-canvas", "off-canvas?", "zero-size"):
            disposition = "likely-dead"
        else:
            disposition = "review"
    else:
        disposition = "likely-dead"

    return {
        "name": name,
        "bucket": bucket,
        "name_string_hits": hits,
        "hit_kind": hits.get("hit_kind"),  # benign / calc / caller (external hits)
        # Referrer-liveness is only reliable for fields (precise probes) — see above.
        "live_referrer": hits.get("live_referrer") if obj_type == "fields" else None,
        "held_referrer": hits.get("held_referrer") if obj_type == "fields" else None,
        "dynamic_reachable": dynamic,
        "canvas": canvas,
        "canvas_placements": canvas_n,
        "on_layout": on_layout.get(name, []),
        "external_entry_point": entry,
        "disposition": disposition,
    }


def cmd_confirm(solution_name, obj_type, as_json, verbose, canvas_strict):
    """Enrich dead candidates with batched deterministic judgment signals."""
    solution_dir = CONTEXT_DIR / solution_name
    res = compute_dead_candidates(solution_name, obj_type)

    # Same reliability guard as `dead` — confirm inherits the same blind spot.
    _dead_reliability_warning(solution_name, obj_type, res.xrefs)

    # Global pre-scans, built once
    fields_index = load_fields_index(solution_dir)
    cf_names = build_cf_names(solution_name)
    to_index = load_table_occurrences_index(solution_dir)
    to_map = build_to_map(to_index)

    corpus = build_name_corpus(solution_name, solution_dir, fields_index, cf_names)
    dyn_flags = scan_dynamic_constructs(corpus)
    layout_index = build_layout_object_index(solution_dir, to_map) if obj_type == "fields" else {}

    # Field names that are unique across tables — the bare field-name probe is only
    # safe for these (otherwise it collides with a same-named field on another table).
    name_tables = {}
    for row in fields_index:
        name_tables.setdefault(row["field"], set()).add(row["table"])
    unique_field_names = {f for f, tabs in name_tables.items() if len(tabs) == 1}

    # Removal-candidate (high∪medium) sets per type, for referrer-liveness. A
    # referrer is "live" when it is NOT itself a removal candidate.
    dead_sets = {obj_type: set(res.high) | set(res.medium)}
    for ty in ("scripts", "layouts", "fields", "custom_functions", "value_lists"):
        if ty in dead_sets:
            continue
        r = compute_dead_candidates(solution_name, ty)
        dead_sets[ty] = set(r.high) | set(r.medium)
    _corpus_kind_to_type = {
        "script": "scripts", "layout": "layouts",
        "field_calc": "fields", "custom_func": "custom_functions",
    }

    def is_live_referrer(entry):
        ty = _corpus_kind_to_type.get(entry["kind"])
        if ty is None:
            return False
        return entry["name"] not in dead_sets.get(ty, set())

    def enrich(name, bucket):
        return _enrich_candidate(name, bucket, obj_type, corpus, dyn_flags,
                                 layout_index, res.on_layout, canvas_strict,
                                 unique_field_names, is_live_referrer)

    enriched = []
    for name in res.high:
        enriched.append(enrich(name, "high"))
    for name in res.medium:
        enriched.append(enrich(name, "medium"))
    if verbose:
        for name in res.low:
            row = enrich(name, "low")
            row["disposition"] = "likely-live"  # heuristic-excluded (PK/FK/global/summary)
            enriched.append(row)

    tally = {"likely-dead": 0, "review": 0, "likely-live": 0}
    for row in enriched:
        tally[row["disposition"]] = tally.get(row["disposition"], 0) + 1

    heuristic_notes = [
        "likely-live = referenced (calc/caller) by an object that is itself LIVE (not a removal "
        "candidate) — keep at a glance, no source-open needed. review is reserved for the genuinely "
        "ambiguous: an entry-point name, a dynamic-reachable hit, OR a referrer that is itself a "
        "held/dead candidate (held-ref — its fate decides this one).",
        "HIT-KIND labels an external name-string hit: benign (placeholder/label/tooltip — "
        "cannot invoke) < calc (hideWhen/merge/calc — live reference) < caller "
        "(Perform-by-name/Go-to-Layout-$var).",
        "Field probes are table-qualified: the bare field name is only matched when unique across "
        "tables, so a same-named field on another table can't cross-rescue.",
        "off-canvas is informational by default and never the sole basis for likely-dead"
        + (" (--canvas-strict ON: off-canvas demotes a medium field)" if canvas_strict else ""),
        "likely-dead = every structural/name-string/dynamic/entry-point signal silent",
        "nested-object off-canvas is low-confidence (off-canvas?) — parent coordinate space",
        "VERIFY before deleting: a wrong delete removes live schema",
    ]

    if as_json:
        print(json.dumps({
            "solution": solution_name,
            "type": obj_type,
            "canvas_strict": canvas_strict,
            "dynamic_constructs_present": dyn_flags,
            "heuristic_notes": heuristic_notes,
            "candidates": enriched,
            "tally": tally,
        }, indent=2, ensure_ascii=False))
        return

    _render_confirm_table(solution_name, obj_type, res, enriched, tally,
                          dyn_flags, heuristic_notes, canvas_strict)


def _render_confirm_table(solution_name, obj_type, res, enriched, tally,
                          dyn_flags, heuristic_notes, canvas_strict):
    """Print the one-read enriched candidate table."""
    print(f"=== Dead-object confirmation: {obj_type} ({solution_name}) ===")
    strict = "ON" if canvas_strict else "OFF (informational)"
    print(f"Candidates enriched: {len(enriched)} "
          f"(from {len(res.high)} high + {len(res.medium)} medium"
          + (f" + {len(res.low)} low" if any(r['bucket'] == 'low' for r in enriched) else "")
          + f")   |   --canvas-strict: {strict}")
    present = [k for k, v in dyn_flags.items() if v]
    print(f"Dynamic constructs in solution: {', '.join(present) if present else 'none'}\n")

    if not enriched:
        print("No dead candidates to confirm.\n")
        return

    rows = []
    for r in enriched:
        h = r["name_string_hits"]
        if obj_type == "fields" and r["canvas"]:
            layout_col = f"{r['canvas']}({r['canvas_placements']})"
        elif r["on_layout"]:
            ls = ", ".join(r["on_layout"][:2])
            layout_col = ls + (f" +{len(r['on_layout']) - 2}" if len(r["on_layout"]) > 2 else "")
        else:
            layout_col = "-"
        notes = []
        if r.get("live_referrer"):
            notes.append(f"live-ref: {r['live_referrer']}")
        elif r.get("held_referrer"):
            notes.append(f"held-ref: {r['held_referrer']} (its fate decides this)")
        if r["external_entry_point"]:
            notes.append(f"name: {r['external_entry_point']}")
        if h["samples"] and not r.get("live_referrer"):
            notes.append("hit " + ",".join(f"{fk}@{o}" for _k, o, fk in h["samples"])[:40])
        rows.append([
            r["disposition"],
            r["name"][:42],
            f"{h['external']}/{h['self']}",
            r["hit_kind"] or "-",
            r["dynamic_reachable"],
            layout_col[:24],
            r["external_entry_point"] or "-",
            "; ".join(notes)[:48],
        ])

    headers = ["DISPOSITION", "CANDIDATE", "HITS e/s", "HIT-KIND", "DYNAMIC", "ON-LAYOUT/CANVAS", "ENTRY", "NOTES"]
    widths = [max(len(headers[i]), max((len(r[i]) for r in rows), default=0)) for i in range(len(headers))]
    # Sort: likely-dead first (most actionable), then review, then likely-live
    order = {"likely-dead": 0, "review": 1, "likely-live": 2}
    rows.sort(key=lambda r: (order.get(r[0], 9), r[1].lower()))

    def fmt(cells):
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))
    print("-+-".join("-" * w for w in widths))

    print(f"\nDisposition tally: {tally['likely-dead']} likely-dead, "
          f"{tally['review']} review, {tally['likely-live']} likely-live")
    print("CONSERVATIVE — verify before deleting. Notes:")
    for n in heuristic_notes:
        print(f"  • {n}")


# ---------------------------------------------------------------------------
# Solution discovery
# ---------------------------------------------------------------------------

def discover_solutions():
    """List available solutions in agent/context/."""
    if not CONTEXT_DIR.exists():
        return []
    return [d.name for d in CONTEXT_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")]


def resolve_solution(args_solution):
    """Resolve the solution name, auto-selecting if only one exists."""
    if args_solution:
        return args_solution

    solutions = discover_solutions()
    if len(solutions) == 0:
        print("ERROR: No solutions found in agent/context/", file=sys.stderr)
        print("  Run fmcontext.sh first to generate index files.", file=sys.stderr)
        sys.exit(1)
    elif len(solutions) == 1:
        return solutions[0]
    else:
        print("Multiple solutions found. Specify one with -s:", file=sys.stderr)
        for s in sorted(solutions):
            print(f"  {s}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Cross-reference tracer for FileMaker solutions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # build
    build_parser = subparsers.add_parser("build", help="Build xref.index")
    build_parser.add_argument("-s", "--solution", help="Solution name")

    # query
    query_parser = subparsers.add_parser("query", help="Query references")
    query_parser.add_argument("-s", "--solution", help="Solution name")
    query_parser.add_argument(
        "-t", "--type", required=True,
        choices=["field", "script", "layout", "value_list", "custom_func",
                 "table_occurrence"],
        help="Object type to query",
    )
    query_parser.add_argument("-n", "--name", required=True,
                              help="Object name to query")
    query_parser.add_argument(
        "--direction", default="inbound",
        choices=["inbound", "outbound"],
        help="inbound = who references X? (default), outbound = what does X reference?",
    )

    # dead
    dead_parser = subparsers.add_parser("dead", help="Find unreferenced objects")
    dead_parser.add_argument("-s", "--solution", help="Solution name")
    dead_parser.add_argument(
        "-t", "--type", required=True,
        choices=["fields", "scripts", "custom_functions", "layouts", "value_lists"],
        help="Object type to scan",
    )
    dead_parser.add_argument("--verbose", action="store_true",
                             help="Show low-confidence results")

    # confirm
    confirm_parser = subparsers.add_parser(
        "confirm",
        help="Enrich dead candidates with batched deterministic judgment signals",
    )
    confirm_parser.add_argument("-s", "--solution", help="Solution name")
    confirm_parser.add_argument(
        "-t", "--type", required=True,
        choices=["fields", "scripts", "custom_functions", "layouts", "value_lists"],
        help="Object type to confirm",
    )
    confirm_parser.add_argument("--json", action="store_true",
                                help="Emit JSON instead of the text table")
    confirm_parser.add_argument("--verbose", action="store_true",
                                help="Also show low-confidence (heuristic-excluded) candidates")
    confirm_parser.add_argument(
        "--canvas-strict", action="store_true",
        help="Let off-canvas placement demote a medium field to likely-dead "
             "(default: off-canvas is informational only)",
    )

    args = parser.parse_args()
    solution = resolve_solution(args.solution)

    if args.command == "build":
        cmd_build(solution)
    elif args.command == "query":
        cmd_query(solution, args.type, args.name, args.direction)
    elif args.command == "dead":
        cmd_dead(solution, args.type, args.verbose)
    elif args.command == "confirm":
        cmd_confirm(solution, args.type, args.json, args.verbose, args.canvas_strict)


if __name__ == "__main__":
    main()
