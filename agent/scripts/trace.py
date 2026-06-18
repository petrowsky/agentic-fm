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

        # Script reference
        if "script" in obj and isinstance(obj["script"], str):
            refs.append(XRef(
                "layout", source_name, "button script",
                "script", obj["script"], "",
            ))

        # Recurse into all values
        for v in obj.values():
            _walk_layout_json(v, source_name, to_map, refs)

    elif isinstance(obj, list):
        for item in obj:
            _walk_layout_json(item, source_name, to_map, refs)


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
    layout_refs = parse_layouts(solution_dir, solution_name, to_map)
    all_refs.extend(layout_refs)
    print(f"    {len(layout_refs)} references found")

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

def cmd_dead(solution_name, obj_type, verbose):
    """Find unreferenced objects."""
    solution_dir = CONTEXT_DIR / solution_name
    xrefs = load_xref(solution_dir)

    # Build set of all referenced objects by type
    referenced = set()
    for ref in xrefs:
        if ref.ref_type == _dead_ref_type(obj_type):
            referenced.add(ref.ref_name)

    # Build set of all objects of this type
    all_objects, on_layout, system_excluded = _get_all_objects(
        solution_dir, solution_name, obj_type, xrefs,
    )

    # Compute dead = all - referenced
    unreferenced = all_objects - referenced

    # Classify confidence
    high = []
    medium = []
    low = []

    for obj in sorted(unreferenced):
        if obj in system_excluded:
            low.append(obj)
        elif obj in on_layout:
            medium.append(obj)
        else:
            high.append(obj)

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

    if verbose and low:
        print(f"LOW CONFIDENCE — excluded by heuristics ({len(low)}):")
        for obj in low:
            print(f"  {obj}")
        print()

    total = len(all_objects)
    print(f"Summary: {len(high)} high, {len(medium)} medium"
          f"{f', {len(low)} low' if verbose else ''} "
          f"unused out of {total} total {obj_type}")


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


def _get_all_objects(solution_dir, solution_name, obj_type, xrefs):
    """Get all objects, layout-only objects, and system-excluded objects."""
    system_excluded = set()
    on_layout = {}  # {obj_name: [layout_names]}

    if obj_type == "fields":
        fields_index = load_fields_index(solution_dir)
        all_objects = set()
        for row in fields_index:
            canonical = f"{row['table']}::{row['field']}"
            all_objects.add(canonical)

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
            # Exclude agentic-fm scripts
            if row.get("folder", "").startswith("agentic-fm"):
                system_excluded.add(row["name"])

        # Find scripts only on layouts
        for ref in xrefs:
            if ref.ref_type == "script" and ref.source_type == "layout":
                on_layout.setdefault(ref.ref_name, []).append(
                    ref.source_name.split(" (ID")[0]
                )

    elif obj_type == "custom_functions":
        cf_names = build_cf_names(solution_name)
        all_objects = {cf["name"] for cf in cf_names}

    elif obj_type == "layouts":
        layouts_index = load_layouts_index(solution_dir)
        all_objects = {row["name"] for row in layouts_index}

    elif obj_type == "value_lists":
        vl_index = load_value_lists_index(solution_dir)
        all_objects = {row["name"] for row in vl_index}

    else:
        all_objects = set()

    return all_objects, on_layout, system_excluded


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

    args = parser.parse_args()
    solution = resolve_solution(args.solution)

    if args.command == "build":
        cmd_build(solution)
    elif args.command == "query":
        cmd_query(solution, args.type, args.name, args.direction)
    elif args.command == "dead":
        cmd_dead(solution, args.type, args.verbose)


if __name__ == "__main__":
    main()
