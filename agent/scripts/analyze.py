#!/usr/bin/env python3
"""
analyze.py — Solution-level analysis and profiling for FileMaker solutions.

Reads pre-indexed data (index files, xref, layout summaries, sanitized scripts)
and produces a structured solution profile. Never reads raw xml_parsed XML.

Usage:
  python3 agent/scripts/analyze.py -s "Solution_Name_Here"
  python3 agent/scripts/analyze.py -s "Solution_Name_Here" --format markdown
  python3 agent/scripts/analyze.py -s "Solution_Name_Here" --deep
  python3 agent/scripts/analyze.py -s "Solution_Name_Here" --ensure-prerequisites
  python3 agent/scripts/analyze.py --list-extensions

Options:
  -s, --solution             Solution name (as it appears in agent/context/)
  --format                   Output format: json (default) or markdown (spec document)
  --deep                     Enable full script text analysis (step frequency,
                             error handling, transaction usage, nesting depth,
                             external calls). Default mode uses index + call chains.
  --ensure-prerequisites     Build xref.index / layout summaries if missing
  --list-extensions          Show available optional dependencies and exit
  --output, -o               Output path override
"""

import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

try:
    import jinja2
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False

EXTENSIONS = {
    "networkx": {
        "available": HAS_NETWORKX,
        "description": "graph topology, community detection, cycle detection",
    },
    "pandas": {
        "available": HAS_PANDAS,
        "description": "statistical profiling, outlier detection",
    },
    "matplotlib": {
        "available": HAS_MATPLOTLIB,
        "description": "visualizations (heatmaps, charts, graph diagrams)",
    },
    "jinja2": {
        "available": HAS_JINJA2,
        "description": "rich templated reports",
    },
}


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # agent/scripts/ -> project root

CONTEXT_DIR = PROJECT_ROOT / "agent" / "context"
XML_PARSED_DIR = PROJECT_ROOT / "agent" / "xml_parsed"

# ---------------------------------------------------------------------------
# Status output
# ---------------------------------------------------------------------------

_STATUS_JSON = False  # Set via --status-json flag
_T0 = 0.0  # Analysis start time


def _status(phase, event="start", **kwargs):
    """Emit a status message. JSONL to stderr when --status-json, else print()."""
    elapsed = round(time.monotonic() - _T0, 4) if _T0 else 0
    if _STATUS_JSON:
        msg = {"status": f"phase_{event}", "phase": phase, "t": elapsed}
        msg.update(kwargs)
        print(json.dumps(msg), file=sys.stderr)
    else:
        if event == "start":
            print(f"  {kwargs.get('label', phase)}...")
        elif event == "end":
            dt = kwargs.get("elapsed", 0)
            items = kwargs.get("items")
            extra = f" ({items} items)" if items else ""
            print(f"    {dt:.3f}s{extra}")
        elif event == "info":
            print(f"  {kwargs.get('label', '')}")
        elif event == "complete":
            print(f"\n==> Analysis complete. ({elapsed:.3f}s)")
            phases = kwargs.get("phases", {})
            if phases and _STATUS_JSON:
                pass  # Already emitted per-phase
            elif phases:
                print("  Phase timing:")
                for p, t in phases.items():
                    print(f"    {p:.<30s} {t:.3f}s")


# ---------------------------------------------------------------------------
# Regex patterns for script text analysis
# ---------------------------------------------------------------------------

RE_PERFORM_SCRIPT = re.compile(
    r'Perform Script\s*\[.*?"([^"]+)"', re.DOTALL
)
RE_LAYOUT_REF = re.compile(r'Layout:\s*"([^"]+)"')
RE_SET_ERROR_CAPTURE = re.compile(r'Set Error Capture\s*\[', re.IGNORECASE)
RE_OPEN_TRANSACTION = re.compile(r'Open Transaction', re.IGNORECASE)
RE_INSERT_FROM_URL = re.compile(r'Insert from URL', re.IGNORECASE)
RE_SEND_MAIL = re.compile(r'Send Mail', re.IGNORECASE)
RE_EXPORT_RECORDS = re.compile(r'Export Records', re.IGNORECASE)
RE_IMPORT_RECORDS = re.compile(r'Import Records', re.IGNORECASE)
RE_IF_BLOCK = re.compile(r'^If\s*\[', re.IGNORECASE)
RE_LOOP_BLOCK = re.compile(r'^Loop$', re.IGNORECASE)

# Naming convention patterns
NAMING_PATTERNS = {
    "__kpt": "primary_key",
    "_kft": "foreign_key",
    "_kf": "foreign_key",
    "zzz": "deprecated",
    "zz": "deprecated",
    "z_": "deprecated",
    "zgt": "global_temp",
    "zg": "global",
    "c_": "unstored_calc",
    "g_": "global",
    "id_": "id_field",
    "flag": "boolean_flag",
}


# ---------------------------------------------------------------------------
# Index parsers (reused from trace.py pattern)
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
        ["to_name", "to_id", "base_table", "base_table_id", "type", "data_source"],
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


def load_custom_functions_index(solution_dir):
    return _parse_index(
        solution_dir / "custom_functions.index",
        ["name", "id", "parameters", "access", "display", "category",
         "folder_path"],
    )


def load_xref_index(solution_dir):
    return _parse_index(
        solution_dir / "xref.index",
        ["source_type", "source_name", "source_location",
         "ref_type", "ref_name", "ref_context"],
    )


# ---------------------------------------------------------------------------
# Data model analysis
# ---------------------------------------------------------------------------

def analyze_data_model(fields_index, to_index, relationships_index,
                       solution_name=None, multi_file_info=None,
                       correlated_data=None, layouts_index=None,
                       layout_classification=None):
    """Analyze base tables, fields, TOs, and relationships.

    When multi_file_info and correlated_data are provided, enriches output
    with source_file attribution, TO classification, and cross-file edge
    detection for data separation model solutions.
    """
    # --- Base tables ---
    tables = {}
    for row in fields_index:
        tname = row["table"]
        if tname not in tables:
            tables[tname] = {
                "id": row["table_id"],
                "fields": [],
                "field_count": 0,
                "by_datatype": collections.Counter(),
                "by_fieldtype": collections.Counter(),
                "auto_enter_patterns": collections.Counter(),
                "has_primary_key": False,
                "foreign_keys": [],
                "unstored_count": 0,
                "global_count": 0,
                "summary_count": 0,
            }
        t = tables[tname]
        t["fields"].append(row)
        t["field_count"] += 1
        t["by_datatype"][row["datatype"]] += 1
        t["by_fieldtype"][row["fieldtype"]] += 1

        # Performance-relevant flags
        flags = row.get("flags", "")
        if "unstored" in flags:
            t["unstored_count"] += 1
        if "global" in flags:
            t["global_count"] += 1
        if row["fieldtype"] == "Summary":
            t["summary_count"] += 1

        ae = row["auto_enter"]
        if ae:
            # Normalize auto-enter to category
            if ae.startswith("auto:"):
                ae_type = ae[5:].split("(")[0].strip()
                t["auto_enter_patterns"][ae_type] += 1
            else:
                t["auto_enter_patterns"][ae] += 1

        fname_lower = row["field"].lower()
        if fname_lower.startswith("__kpt") or fname_lower == "primarykey":
            t["has_primary_key"] = True
        if fname_lower.startswith("_kft") or fname_lower.startswith("_kf"):
            t["foreign_keys"].append(row["field"])

    # Build table summary (without raw field lists for output)
    table_summaries = {}
    for tname, t in tables.items():
        table_summaries[tname] = {
            "id": t["id"],
            "field_count": t["field_count"],
            "by_datatype": dict(t["by_datatype"]),
            "by_fieldtype": dict(t["by_fieldtype"]),
            "auto_enter_patterns": dict(t["auto_enter_patterns"]),
            "has_primary_key": t["has_primary_key"],
            "foreign_key_count": len(t["foreign_keys"]),
            "unstored_count": t["unstored_count"],
            "global_count": t["global_count"],
            "summary_count": t["summary_count"],
        }

    # --- Table occurrences ---
    to_by_base = collections.defaultdict(list)
    for row in to_index:
        base = row["base_table"] or "(external/unknown)"
        to_by_base[base].append(row["to_name"])

    to_groups = {
        base: {"count": len(tos), "names": tos}
        for base, tos in to_by_base.items()
    }

    # --- Relationships ---
    rel_summary = {
        "total": len(relationships_index),
        "by_join_type": dict(collections.Counter(
            r["join_type"] for r in relationships_index
        )),
        "cascades": {
            "create": sum(
                1 for r in relationships_index if r["cascade_create"] == "True"
            ),
            "delete": sum(
                1 for r in relationships_index if r["cascade_delete"] == "True"
            ),
        },
        "multi_predicate": sum(
            1 for r in relationships_index if "+" in r["join_type"]
        ),
        "self_joins": 0,
    }

    # Detect self-joins (left and right TO share same base table)
    to_map = {row["to_name"]: row["base_table"] for row in to_index}
    for r in relationships_index:
        left_base = to_map.get(r["left_to"], "")
        right_base = to_map.get(r["right_to"], "")
        if left_base and left_base == right_base:
            rel_summary["self_joins"] += 1

    # --- Topology analysis ---
    # Use UI-only layout concentration for topology if available,
    # otherwise fall back to raw layout concentration
    topo_layouts = layouts_index
    topo_ui_concentration = None
    if layout_classification:
        topo_ui_concentration = layout_classification.get("ui_concentration")
    topology = _analyze_topology(to_index, relationships_index, to_map,
                                  layouts_index=topo_layouts,
                                  ui_concentration=topo_ui_concentration)

    # --- TO classification ---
    to_classification = {"local": 0, "external": 0, "by_data_source": {}}
    for row in to_index:
        to_type = row.get("type", "")
        if to_type == "External":
            to_classification["external"] += 1
            ds = row.get("data_source", "")
            if ds:
                to_classification["by_data_source"][ds] = (
                    to_classification["by_data_source"].get(ds, 0) + 1
                )
        elif to_type == "Local":
            to_classification["local"] += 1

    # --- Source file attribution ---
    # Determine which base tables are local to which file
    data_source_map = {}
    if multi_file_info:
        data_source_map = multi_file_info.get("data_source_map", {})

    # Build set of locally-defined base tables (from TOs with type=Local)
    local_base_tables = set()
    for row in to_index:
        if row.get("type", "") == "Local":
            local_base_tables.add(row["base_table"])

    # Build mapping: base_table_name -> source_file
    # Also track base_table_id per name to handle name collisions
    table_source = {}  # table_name -> source_file
    table_ids_by_source = {}  # (table_name, source_file) -> base_table_id

    for row in to_index:
        bt = row["base_table"]
        if not bt:
            continue
        if row.get("type", "") == "Local":
            table_source[bt] = solution_name or ""
            table_ids_by_source[(bt, solution_name or "")] = row["base_table_id"]
        elif row.get("type", "") == "External":
            ds = row.get("data_source", "")
            corr_sol = data_source_map.get(ds, "")
            if corr_sol and bt not in local_base_tables:
                table_source.setdefault(bt, corr_sol)
                table_ids_by_source[(bt, corr_sol)] = row["base_table_id"]

    # Apply source_file to table summaries
    for tname in table_summaries:
        table_summaries[tname]["source_file"] = table_source.get(tname, solution_name or "")

    # Add external tables from correlated solutions into table_summaries
    # so they appear as nodes in the ERD graph
    if correlated_data:
        for corr_name, corr_info in correlated_data.items():
            for tname in corr_info.get("local_tables", set()):
                if tname not in table_summaries and tname in table_source:
                    field_count = corr_info.get("table_field_counts", {}).get(tname, 0)
                    table_summaries[tname] = {
                        "id": "",
                        "field_count": field_count,
                        "by_datatype": {},
                        "by_fieldtype": {},
                        "auto_enter_patterns": {},
                        "has_primary_key": False,
                        "foreign_key_count": 0,
                        "unstored_count": 0,
                        "global_count": 0,
                        "summary_count": 0,
                        "source_file": corr_name,
                        "is_external": True,
                    }

    # Build local_tables and external_tables lists
    local_tables_list = sorted(
        tname for tname, src in table_source.items()
        if src == (solution_name or "")
    )
    external_tables_dict = collections.defaultdict(list)
    for tname, src in sorted(table_source.items()):
        if src and src != (solution_name or ""):
            # Find which data source name maps to this correlated solution
            ds_name = ""
            for dsn, corr in data_source_map.items():
                if corr == src:
                    ds_name = dsn
                    break
            external_tables_dict[ds_name or src].append(tname)

    # --- Base-table relationship pairs for ERD ---
    seen = set()
    base_table_edges = []
    for r in relationships_index:
        left_base = to_map.get(r["left_to"], "")
        right_base = to_map.get(r["right_to"], "")
        if left_base and right_base and left_base != right_base:
            pair = tuple(sorted([left_base, right_base]))
            if pair not in seen:
                seen.add(pair)
                left_src = table_source.get(left_base, "")
                right_src = table_source.get(right_base, "")
                cross_file = bool(
                    left_src and right_src and left_src != right_src
                )
                base_table_edges.append({
                    "left": pair[0],
                    "right": pair[1],
                    "cross_file": cross_file,
                })

    # --- Performance metrics (solution-wide) ---
    total_unstored = sum(t["unstored_count"] for t in tables.values())
    total_summary = sum(t["summary_count"] for t in tables.values())
    total_global = sum(t["global_count"] for t in tables.values())
    total_calculated = sum(
        t.get("by_fieldtype", {}).get("Calculated", 0)
        for t in table_summaries.values()
    )

    # Tables sorted by unstored+summary count (performance hotspots)
    perf_hotspots = sorted(
        [
            {
                "table": tname,
                "unstored": t["unstored_count"],
                "summary": t["summary_count"],
                "calculated": t.get("by_fieldtype", {}).get("Calculated", 0),
                "total_fields": t["field_count"],
            }
            for tname, t in table_summaries.items()
            if t["unstored_count"] > 0 or t["summary_count"] > 0
        ],
        key=lambda x: x["unstored"] + x["summary"],
        reverse=True,
    )

    performance = {
        "total_unstored": total_unstored,
        "total_summary": total_summary,
        "total_global": total_global,
        "total_calculated": total_calculated,
        "unstored_pct": round(
            total_unstored / max(1, len(fields_index)) * 100, 1
        ),
        "summary_pct": round(
            total_summary / max(1, len(fields_index)) * 100, 1
        ),
        "hotspot_tables": perf_hotspots[:20],
    }

    # Count local tables (from this file's fields_index, not correlated)
    local_table_count = sum(
        1 for t in table_summaries.values() if not t.get("is_external")
    )

    return {
        "tables": table_summaries,
        "table_count": local_table_count,
        "total_table_count": len(table_summaries),
        "total_fields": len(fields_index),
        "table_occurrences": to_groups,
        "to_count": len(to_index),
        "relationships": rel_summary,
        "topology": topology,
        "base_table_edges": base_table_edges,
        "performance": performance,
        "to_classification": to_classification,
        "local_tables": local_tables_list,
        "external_tables": dict(external_tables_dict),
    }


# ---------------------------------------------------------------------------
# Per-file graph building and ERD classification
# ---------------------------------------------------------------------------

_UTILITY_TABLE_PATTERNS = {
    "globals", "startup", "defaults", "settings", "config", "admin",
    "log", "import", "export", "temp", "staging", "navigation",
    "preferences", "system", "popovers", "vlist", "images",
}


def _classify_tables(fields_index, relationships_index, to_index):
    """Classify tables as entity/join/utility using heuristics.

    Returns a dict of {table_name: {classification, signals}}.
    """
    # Build per-table stats from fields
    table_stats = {}
    for row in fields_index:
        tname = row["table"]
        if tname not in table_stats:
            table_stats[tname] = {
                "has_pk": False,
                "fk_count": 0,
                "global_count": 0,
                "field_count": 0,
                "descriptive_count": 0,
                "fk_fields": [],
            }
        ts = table_stats[tname]
        ts["field_count"] += 1
        fname_lower = row["field"].lower()
        flags = row.get("flags", "")

        # PK detection
        if (fname_lower.startswith("__kpt") or fname_lower == "primarykey"
                or fname_lower == "id"):
            ts["has_pk"] = True

        # FK detection
        if (fname_lower.startswith("_kft") or fname_lower.startswith("_kf")
                or fname_lower.startswith("foreignkey")
                or fname_lower.startswith("id_")):
            ts["fk_count"] += 1
            ts["fk_fields"].append(row["field"])

        # Global detection
        if "global" in flags:
            ts["global_count"] += 1

        # Descriptive field: not a key, not auto-enter timestamp/account, not global
        ae = row.get("auto_enter", "")
        is_auto = any(k in ae.lower() for k in [
            "timestamp", "accountname", "uuid", "serial",
        ]) if ae else False
        if (not fname_lower.startswith("__kpt")
                and not fname_lower.startswith("_kf")
                and not fname_lower.startswith("foreignkey")
                and not fname_lower.startswith("id_")
                and fname_lower != "id"
                and fname_lower != "primarykey"
                and "global" not in flags
                and not is_auto):
            ts["descriptive_count"] += 1

    # Count inbound FK references: how many other tables have FK fields
    # whose name contains this table's name
    inbound_refs = collections.Counter()
    for tname in table_stats:
        tname_lower = tname.lower()
        for other_name, other_stats in table_stats.items():
            if other_name == tname:
                continue
            for fk_field in other_stats["fk_fields"]:
                if tname_lower in fk_field.lower():
                    inbound_refs[tname] += 1
                    break  # count each referring table once

    # Classify each table
    classifications = {}
    for tname, ts in table_stats.items():
        signals = []
        if ts["has_pk"]:
            signals.append("has_pk")
        if ts["fk_count"] > 0:
            signals.append(f"fk_count:{ts['fk_count']}")
        if ts["global_count"] > 0:
            signals.append(f"globals:{ts['global_count']}")
        if inbound_refs[tname] > 0:
            signals.append(f"inbound_refs:{inbound_refs[tname]}")

        # Classification rules
        # Note: FM entity tables commonly have global fields for search/filter
        # UI, so globals alone don't indicate utility. Only classify as utility
        # if the table has no inbound FK references and no PK.
        is_referenced = inbound_refs[tname] > 0
        if (not is_referenced and not ts["has_pk"]
                and ts["global_count"] > 3):
            classification = "utility"
        elif (not is_referenced and ts["fk_count"] == 0
              and tname.lower() in _UTILITY_TABLE_PATTERNS):
            classification = "utility"
        elif (ts["fk_count"] >= 2
              and ts["descriptive_count"] < ts["fk_count"]
              and not is_referenced):
            classification = "join"
        else:
            classification = "entity"

        classifications[tname] = {
            "classification": classification,
            "signals": signals,
        }

    return classifications


def _classify_relationship(join_fields, join_type):
    """Classify a collapsed relationship as true_erd/utility/uncertain."""
    if join_type != "Equal" and "+" not in join_type:
        return "utility"

    # Check if join fields follow PK→FK pattern
    pairs = join_fields.split("+") if "+" in join_fields else [join_fields]
    pk_fk_count = 0
    for pair in pairs:
        if "=" not in pair:
            continue
        left_f, right_f = pair.split("=", 1)
        left_lower = left_f.strip().lower()
        right_lower = right_f.strip().lower()
        # PK on one side, FK or id_ on the other
        is_pk_fk = (
            (left_lower in ("id", "primarykey") or left_lower.startswith("__kpt"))
            or (right_lower in ("id", "primarykey") or right_lower.startswith("__kpt"))
            or left_lower.startswith("id_") or right_lower.startswith("id_")
            or left_lower.startswith("_kf") or right_lower.startswith("_kf")
            or left_lower.startswith("foreignkey") or right_lower.startswith("foreignkey")
        )
        if is_pk_fk:
            pk_fk_count += 1

    if pk_fk_count == len(pairs) and pk_fk_count > 0:
        return "true_erd"
    elif pk_fk_count > 0:
        return "uncertain"
    return "utility"


def _build_file_graph(solution_name, fields_index, to_index, relationships_index,
                      is_data_file=False):
    """Build a per-file relationship graph with optional ERD classification.

    Returns a dict with tables, edges, classifications, and counts.
    """
    # Build TO→base_table map for this file
    to_map = {row["to_name"]: row["base_table"] for row in to_index}

    # Collapse relationships to base table edges with detail
    seen = set()
    edges = []
    for r in relationships_index:
        left_base = to_map.get(r["left_to"], "")
        right_base = to_map.get(r["right_to"], "")
        if not left_base or not right_base or left_base == right_base:
            continue
        pair = tuple(sorted([left_base, right_base]))
        if pair in seen:
            # Increment to_pairs count on existing edge
            for e in edges:
                if (e["left"], e["right"]) == pair:
                    e["to_pairs"] += 1
                    break
            continue
        seen.add(pair)

        join_fields = r.get("join_fields", "")
        join_type = r.get("join_type", "Equal")
        erd_class = _classify_relationship(join_fields, join_type)

        edges.append({
            "left": pair[0],
            "right": pair[1],
            "join_fields": join_fields,
            "join_type": join_type,
            "cascade_create": r.get("cascade_create", "False") == "True",
            "cascade_delete": r.get("cascade_delete", "False") == "True",
            "erd_classification": erd_class,
            "to_pairs": 1,
        })

    # Build table info
    table_field_counts = collections.Counter()
    for row in fields_index:
        table_field_counts[row["table"]] += 1

    tables = {}
    for tname, fc in table_field_counts.items():
        tables[tname] = {"field_count": fc}

    # ERD classification for data files
    table_classifications = None
    if is_data_file and fields_index:
        table_classifications = _classify_tables(
            fields_index, relationships_index, to_index
        )

    result = {
        "tables": tables,
        "base_table_edges": edges,
        "relationship_count": len(relationships_index),
        "to_count": len(to_index),
    }
    if table_classifications is not None:
        result["table_classifications"] = table_classifications

    return result


def build_per_file_graphs(solution_name, fields_index, to_index,
                          relationships_index, multi_file_info, correlated_data):
    """Build per-file relationship graphs for all files in a multi-file solution.

    Returns a dict keyed by solution name with graph data for each file.
    """
    graphs = {}

    # Primary file's own graph
    primary_graph = _build_file_graph(
        solution_name, fields_index, to_index, relationships_index,
        is_data_file=False,
    )
    if primary_graph["relationship_count"] > 0:
        graphs[solution_name] = primary_graph

    # Correlated files
    if correlated_data:
        data_source_map = multi_file_info.get("data_source_map", {})
        # Determine which correlated solutions are "data" files
        data_file_names = set()
        for f in multi_file_info.get("files", []):
            if f.get("role") == "data":
                data_file_names.add(f["name"])

        for corr_name, corr_info in correlated_data.items():
            corr_rels = corr_info.get("relationships_index", [])
            if not corr_rels:
                continue
            is_data = corr_name in data_file_names
            graph = _build_file_graph(
                corr_name,
                corr_info.get("fields_index", []),
                corr_info.get("to_index", []),
                corr_rels,
                is_data_file=is_data,
            )
            if graph["relationship_count"] > 0:
                graphs[corr_name] = graph

    return graphs


def _analyze_topology(to_index, relationships_index, to_map,
                      layouts_index=None, ui_concentration=None):
    """Analyze TO topology pattern.

    Classifies the relationship graph management strategy using signals
    documented in references/relationship-graph-topologies.md.

    Patterns: anchor-buoy, star, tiered-hub, spider-web, flat, hybrid.
    """
    if HAS_NETWORKX:
        return _topology_networkx(to_index, relationships_index, to_map,
                                  layouts_index, ui_concentration)
    return _topology_basic(to_index, relationships_index, to_map,
                           layouts_index, ui_concentration)


def _compute_layout_concentration(layouts_index, to_map):
    """Compute how concentrated layouts are across TOs.

    Returns a dict with:
      - cover_50: how many TOs needed to cover 50% of layouts
      - cover_50_pct: that number as a percentage of distinct layout TOs
      - top_to_pct: what percentage of layouts use the single most-used TO
      - distinct_layout_tos: count of TOs that serve as layout bases
      - spread_ratio: cover_50 / distinct_layout_tos (low = concentrated)
    """
    if not layouts_index:
        return None
    layout_tos = collections.Counter(
        r["base_to"] for r in layouts_index if r.get("base_to")
    )
    if not layout_tos:
        return None

    total = sum(layout_tos.values())
    counts = sorted(layout_tos.values(), reverse=True)
    distinct = len(counts)

    running = 0
    cover_50 = distinct
    for i, c in enumerate(counts):
        running += c
        if running >= total * 0.5:
            cover_50 = i + 1
            break

    top_to_pct = counts[0] / total if counts else 0

    return {
        "cover_50": cover_50,
        "cover_50_pct": round(cover_50 / max(distinct, 1), 2),
        "top_to_pct": round(top_to_pct, 2),
        "distinct_layout_tos": distinct,
        "spread_ratio": round(cover_50 / max(distinct, 1), 2),
    }


def _classify_topology(degrees, max_degree, hub_count, base_table_count,
                       to_count, has_cartesian, layout_concentration=None):
    """Apply multi-signal classification heuristics.

    See references/relationship-graph-topologies.md for the full decision
    matrix and signal definitions.
    """
    if not degrees:
        return "unknown", 0.0

    low_degree_pct = (
        sum(1 for d in degrees if d <= 2) / len(degrees)
    )
    to_ratio = to_count / max(base_table_count, 1)

    # Count hubs at a lower threshold too (degree >= 3) for sparser graphs
    hub3_count = sum(1 for d in degrees if d >= 3)
    hub3_ratio = hub3_count / max(base_table_count, 1)

    hub_ratio = hub_count / max(base_table_count, 1)

    # Layout concentration signals (when available)
    # spread_ratio: low = layouts concentrated on few TOs (hub-centric)
    #               high = layouts spread across many TOs (anchor-buoy)
    layout_spread = None
    if layout_concentration:
        layout_spread = layout_concentration["spread_ratio"]

    # Flat/Minimal: very few relationships, TOs ~ base tables
    if max_degree < 5 and to_ratio < 2:
        return "flat", round(1.0 - (max_degree / 5), 2)

    # Spider-Web: dense, no clear hierarchy
    if low_degree_pct < 0.4:
        return "spider-web", round(1.0 - low_degree_pct, 2)

    # Anchor-Buoy: high low_degree_pct, hubs proportional to base tables,
    # AND layouts NOT dominated by a single TO (top_to_pct < 15%).
    # In anchor-buoy, each entity has its own layouts — no single TO dominates.
    top_to_low = True
    if layout_concentration:
        top_to_low = layout_concentration["top_to_pct"] < 0.15
    if (low_degree_pct >= 0.8
            and (hub_ratio >= 0.3 or hub3_ratio >= 0.4)
            and to_ratio >= 2
            and top_to_low):
        return "anchor-buoy", round(low_degree_pct, 2)

    # Tiered Hub: one or a few dominant TOs drive most of the UI (top_to_pct
    # >= 15%), high max degree, many TOs per base table. The "single-page
    # app" pattern — a primary entity with function-specific sub-hubs.
    top_to_high = False
    if layout_concentration:
        top_to_high = layout_concentration["top_to_pct"] >= 0.12
    if (low_degree_pct >= 0.6 and max_degree >= 10
            and to_ratio >= 5
            and (top_to_high or max_degree >= 20)):
        return "tiered-hub", round(min(hub_ratio * 2, 0.95), 2)

    # Star (Context-Hub): few centralized hubs managing many satellites.
    # Hub count is much lower than base table count.
    if (low_degree_pct >= 0.7 and hub_count >= 1
            and hub_ratio < 0.2 and to_ratio >= 3):
        conf = round(max_degree / (max_degree + 10), 2)
        return "star", conf

    # Anchor-Buoy (relaxed): sparser graphs where most connections are
    # degree 2-4 but structurally one-hub-per-table, and layouts are spread
    if (low_degree_pct >= 0.7 and hub3_ratio >= 0.3
            and to_ratio >= 1.5 and top_to_low):
        return "anchor-buoy", round(low_degree_pct * 0.8, 2)

    # Hybrid: mixed signals
    return "hybrid", 0.5


def _topology_basic(to_index, relationships_index, to_map,
                    layouts_index=None, ui_concentration=None):
    """Basic topology analysis without networkx."""
    degree = collections.Counter()
    has_cartesian = False
    for r in relationships_index:
        degree[r["left_to"]] += 1
        degree[r["right_to"]] += 1
        if "CartesianProduct" in r.get("join_type", ""):
            has_cartesian = True

    if not degree:
        return {"pattern": "unknown", "note": "no relationships found"}

    degrees = list(degree.values())
    avg_degree = sum(degrees) / len(degrees) if degrees else 0
    max_degree = max(degrees) if degrees else 0
    low_degree_pct = sum(1 for d in degrees if d <= 2) / len(degrees) if degrees else 0
    hub_count = sum(1 for d in degrees if d >= 5)

    base_table_count = len(set(to_map.values()))

    # Prefer UI-only concentration (from button-based classification)
    # over raw layout concentration
    layout_conc = ui_concentration
    if not layout_conc:
        layout_conc = _compute_layout_concentration(layouts_index, to_map)
    pattern, confidence = _classify_topology(
        degrees, max_degree, hub_count, base_table_count,
        len(to_index), has_cartesian, layout_conc,
    )

    result = {
        "pattern": pattern,
        "confidence": confidence,
        "avg_degree": round(avg_degree, 2),
        "max_degree": max_degree,
        "low_degree_pct": round(low_degree_pct, 2),
        "hub_count": hub_count,
        "has_cartesian": has_cartesian,
    }
    if layout_conc:
        result["layout_concentration"] = layout_conc
    return result


def _topology_networkx(to_index, relationships_index, to_map,
                       layouts_index=None, ui_concentration=None):
    """Advanced topology analysis with networkx."""
    G = nx.Graph()
    has_cartesian = False
    for row in to_index:
        G.add_node(row["to_name"], base_table=row["base_table"])
    for r in relationships_index:
        jt = r.get("join_type", "")
        G.add_edge(r["left_to"], r["right_to"], join_type=jt)
        if "CartesianProduct" in jt:
            has_cartesian = True

    if G.number_of_nodes() == 0:
        return {"pattern": "unknown", "note": "no table occurrences found"}

    degrees = [d for _, d in G.degree()]
    avg_degree = sum(degrees) / len(degrees) if degrees else 0
    max_degree = max(degrees) if degrees else 0
    low_degree_pct = sum(1 for d in degrees if d <= 2) / len(degrees) if degrees else 0
    hub_count = sum(1 for d in degrees if d >= 5)

    base_table_count = len(set(to_map.values()))

    layout_conc = ui_concentration
    if not layout_conc:
        layout_conc = _compute_layout_concentration(layouts_index, to_map)
    pattern, confidence = _classify_topology(
        degrees, max_degree, hub_count, base_table_count,
        len(to_index), has_cartesian, layout_conc,
    )

    # Connected components
    components = list(nx.connected_components(G))

    # Identify anchor/hub tables (hubs by base table)
    hub_tos = [n for n, d in G.degree() if d >= 5]
    anchor_tables = sorted(set(to_map.get(t, t) for t in hub_tos))

    # Bridge edges (whose removal disconnects the graph)
    bridges = list(nx.bridges(G))

    result = {
        "pattern": pattern,
        "confidence": confidence,
        "avg_degree": round(avg_degree, 2),
        "max_degree": max_degree,
        "low_degree_pct": round(low_degree_pct, 2),
        "hub_count": hub_count,
        "has_cartesian": has_cartesian,
        "anchor_tables": anchor_tables,
        "connected_components": len(components),
        "isolated_components": [
            sorted(c) for c in components if len(c) <= 3
        ] if len(components) > 1 else [],
        "bridge_count": len(bridges),
        "method": "networkx",
    }
    if layout_conc:
        result["layout_concentration"] = layout_conc
    return result


# ---------------------------------------------------------------------------
# Naming convention detection
# ---------------------------------------------------------------------------

def detect_naming_conventions(fields_index):
    """Detect dominant naming conventions from field names."""
    prefix_counts = collections.Counter()
    case_styles = collections.Counter()

    for row in fields_index:
        fname = row["field"]
        # Check known prefixes
        for prefix, label in NAMING_PATTERNS.items():
            if fname.lower().startswith(prefix):
                prefix_counts[f"{prefix} ({label})"] += 1
                break

        # Detect case style
        if "_" in fname and fname == fname.lower():
            case_styles["snake_case"] += 1
        elif fname[0].isupper() and "_" not in fname:
            case_styles["PascalCase"] += 1
        elif fname[0].islower() and "_" not in fname and any(c.isupper() for c in fname):
            case_styles["camelCase"] += 1
        else:
            case_styles["mixed"] += 1

    dominant_case = case_styles.most_common(1)[0][0] if case_styles else "unknown"

    return {
        "prefix_conventions": dict(prefix_counts.most_common()),
        "case_styles": dict(case_styles),
        "dominant_case": dominant_case,
    }


# ---------------------------------------------------------------------------
# Script file cache
# ---------------------------------------------------------------------------

def find_script_files(solution_name):
    """Find all sanitized script text files for a solution."""
    scripts_dir = XML_PARSED_DIR / "scripts_sanitized" / solution_name
    if not scripts_dir.exists():
        return []
    return sorted(scripts_dir.rglob("*.txt"))


def load_script_cache(solution_name, scripts_index):
    """Load all script files once. Returns list of dicts (preserving per-file iteration).

    Each entry: {"name": str, "path": Path, "text": str, "lines": list,
                 "line_count": int, "is_empty": bool, "calls": list,
                 "layout_refs": list, "has_insert_from_url": bool,
                 "has_send_mail": bool, "has_export_records": bool,
                 "has_import_records": bool}
    """
    scripts_by_id = {s["id"]: s for s in scripts_index}
    script_files = find_script_files(solution_name)
    cache = []

    for script_path in script_files:
        # Resolve script name
        script_id = extract_script_id_from_filename(script_path.name)
        if script_id and script_id in scripts_by_id:
            script_name = scripts_by_id[script_id]["name"]
        else:
            script_name = script_path.stem.rsplit(" - ID ", 1)[0]

        try:
            with open(script_path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        lines = text.strip().split("\n")
        non_empty_lines = [l for l in lines if l.strip()]

        cache.append({
            "name": script_name,
            "path": script_path,
            "text": text,
            "lines": lines,
            "line_count": len(lines),
            "is_empty": len(non_empty_lines) == 0,
            "calls": RE_PERFORM_SCRIPT.findall(text),
            "layout_refs": RE_LAYOUT_REF.findall(text),
            "has_insert_from_url": bool(RE_INSERT_FROM_URL.search(text)),
            "has_send_mail": bool(RE_SEND_MAIL.search(text)),
            "has_export_records": bool(RE_EXPORT_RECORDS.search(text)),
            "has_import_records": bool(RE_IMPORT_RECORDS.search(text)),
        })

    return cache


# ---------------------------------------------------------------------------
# Script analysis
# ---------------------------------------------------------------------------


def extract_script_id_from_filename(filename):
    """Extract script ID from filename like 'Contact - Navigate To - ID 71.txt'."""
    match = re.search(r'ID (\d+)\.txt$', filename)
    return match.group(1) if match else None


def analyze_scripts(solution_name, scripts_index, script_cache, deep=False):
    """Analyze scripts: inventory, call chains, and optionally deep metrics."""
    # Build inventory from index
    scripts_by_id = {s["id"]: s for s in scripts_index}
    scripts_by_name = {s["name"]: s for s in scripts_index}

    # Organize by folder
    by_folder = collections.defaultdict(list)
    for s in scripts_index:
        folder = s["folder"] or "(root)"
        by_folder[folder].append(s["name"])

    folder_tree = {
        folder: {"scripts": names, "count": len(names)}
        for folder, names in sorted(by_folder.items())
    }

    # --- Call chain extraction from cached scripts ---
    call_graph = {}  # script_name -> [called_script_names]
    script_line_counts = {}

    # Deep mode accumulators
    deep_metrics = None
    if deep:
        deep_metrics = {
            "error_handling": {"with_capture": 0, "without_capture": 0},
            "transactions": {"scripts_using": 0},
            "external_calls": collections.Counter(),
            "step_frequency": collections.Counter(),
            "nesting": {"max_depth": 0, "avg_depth": 0, "depths": []},
        }

    for info in script_cache:
        script_name = info["name"]
        text = info["text"]
        lines = info["lines"]
        script_line_counts[script_name] = info["line_count"]

        # Extract Perform Script calls (pre-computed in cache)
        calls = info["calls"]
        if calls:
            call_graph[script_name] = calls

        # Deep analysis
        if deep and deep_metrics is not None:
            has_error_capture = bool(RE_SET_ERROR_CAPTURE.search(text))
            if has_error_capture:
                deep_metrics["error_handling"]["with_capture"] += 1
            else:
                deep_metrics["error_handling"]["without_capture"] += 1

            if RE_OPEN_TRANSACTION.search(text):
                deep_metrics["transactions"]["scripts_using"] += 1

            for pattern, label in [
                (RE_INSERT_FROM_URL, "Insert from URL"),
                (RE_SEND_MAIL, "Send Mail"),
                (RE_EXPORT_RECORDS, "Export Records"),
                (RE_IMPORT_RECORDS, "Import Records"),
            ]:
                count = len(pattern.findall(text))
                if count:
                    deep_metrics["external_calls"][label] += count

            # Nesting depth
            depth = 0
            max_depth = 0
            for line in lines:
                stripped = line.strip()
                if RE_IF_BLOCK.match(stripped) or RE_LOOP_BLOCK.match(stripped):
                    depth += 1
                    max_depth = max(max_depth, depth)
                elif stripped.startswith("End If") or stripped.startswith("End Loop"):
                    depth = max(0, depth - 1)

                # Step frequency: extract step name (first word before [)
                step_match = re.match(r'^([A-Z][A-Za-z ]+?)(?:\s*\[|$)', stripped)
                if step_match:
                    deep_metrics["step_frequency"][step_match.group(1).strip()] += 1

            deep_metrics["nesting"]["depths"].append(max_depth)
            if max_depth > deep_metrics["nesting"]["max_depth"]:
                deep_metrics["nesting"]["max_depth"] = max_depth

    # --- Build call chain analysis ---
    # Identify entry points and utilities
    called_by = collections.defaultdict(list)
    for caller, callees in call_graph.items():
        for callee in callees:
            called_by[callee].append(caller)

    all_script_names = set(s["name"] for s in scripts_index)
    entry_points = sorted(
        name for name in all_script_names
        if name not in called_by and name in call_graph
    )
    utility_scripts = sorted(
        name for name in all_script_names
        if len(called_by.get(name, [])) >= 3
    )
    leaf_scripts = sorted(
        name for name in all_script_names
        if name not in call_graph and name in called_by
    )

    # --- Functional clusters ---
    clusters = _cluster_scripts(call_graph, scripts_by_name, all_script_names)

    # Finalize deep metrics
    if deep and deep_metrics is not None:
        depths = deep_metrics["nesting"]["depths"]
        deep_metrics["nesting"]["avg_depth"] = (
            round(sum(depths) / len(depths), 1) if depths else 0
        )
        del deep_metrics["nesting"]["depths"]
        deep_metrics["external_calls"] = dict(deep_metrics["external_calls"])
        deep_metrics["step_frequency"] = dict(
            deep_metrics["step_frequency"].most_common(20)
        )
        deep_metrics["error_handling"]["coverage_pct"] = round(
            deep_metrics["error_handling"]["with_capture"]
            / max(1, deep_metrics["error_handling"]["with_capture"]
                  + deep_metrics["error_handling"]["without_capture"])
            * 100, 1
        )

    result = {
        "total_scripts": len(scripts_index),
        "total_files_analyzed": len(script_cache),
        "folders": folder_tree,
        "call_graph_edges": sum(len(v) for v in call_graph.values()),
        "call_graph": [
            [caller, callee]
            for caller, callees in call_graph.items()
            for callee in callees
            if callee in all_script_names
        ],
        "entry_points": entry_points,
        "utility_scripts": utility_scripts,
        "leaf_scripts": leaf_scripts[:20],  # Cap for readability
        "clusters": clusters,
        "line_counts": {
            "total": sum(script_line_counts.values()),
            "avg": round(
                sum(script_line_counts.values()) / max(1, len(script_line_counts)), 1
            ),
            "max": max(script_line_counts.values()) if script_line_counts else 0,
            "largest_scripts": sorted(
                script_line_counts.items(), key=lambda x: x[1], reverse=True
            )[:10],
        },
    }

    if deep and deep_metrics is not None:
        result["deep_metrics"] = deep_metrics

    return result


def _cluster_scripts(call_graph, scripts_by_name, all_script_names):
    """Cluster scripts into functional domains."""
    if HAS_NETWORKX:
        return _cluster_scripts_networkx(call_graph, scripts_by_name, all_script_names)
    return _cluster_scripts_basic(call_graph, scripts_by_name)


def _cluster_scripts_basic(call_graph, scripts_by_name):
    """Basic clustering by folder + call chain connectivity."""
    # Group by top-level folder
    clusters = collections.defaultdict(set)
    for script_name, info in scripts_by_name.items():
        folder = info.get("folder", "") or "(root)"
        top_folder = folder.split("/")[0]
        clusters[top_folder].add(script_name)

    # Merge clusters that are connected by call chains
    result = []
    for folder, members in sorted(clusters.items()):
        entry_pts = [
            m for m in members
            if m in call_graph and not any(
                m in callees
                for callees in call_graph.values()
                if callees
            )
        ]
        result.append({
            "name": folder,
            "script_count": len(members),
            "entry_points": sorted(entry_pts)[:5],
            "method": "folder_grouping",
        })

    return result


def _cluster_scripts_networkx(call_graph, scripts_by_name, all_script_names):
    """Advanced clustering with networkx community detection."""
    G = nx.DiGraph()
    for name in all_script_names:
        folder = scripts_by_name.get(name, {}).get("folder", "")
        G.add_node(name, folder=folder)
    for caller, callees in call_graph.items():
        for callee in callees:
            if callee in all_script_names:
                G.add_edge(caller, callee)

    # Use weakly connected components as clusters
    components = list(nx.weakly_connected_components(G))

    # Detect cycles
    cycles = list(nx.simple_cycles(G))
    cycle_scripts = set()
    for cycle in cycles[:50]:  # Cap to avoid combinatorial explosion
        cycle_scripts.update(cycle)

    result = []
    for comp in sorted(components, key=len, reverse=True):
        if len(comp) < 2:
            continue

        # Determine dominant folder
        folders = collections.Counter(
            scripts_by_name.get(s, {}).get("folder", "").split("/")[0]
            for s in comp
        )
        dominant_folder = folders.most_common(1)[0][0] if folders else "(root)"

        # Find entry points in this cluster
        sub = G.subgraph(comp)
        entry_pts = [n for n in comp if sub.in_degree(n) == 0]

        # Betweenness centrality for bottleneck detection
        if len(comp) >= 3:
            centrality = nx.betweenness_centrality(sub)
            bottleneck = max(centrality, key=centrality.get) if centrality else None
        else:
            bottleneck = None

        cluster_info = {
            "name": dominant_folder or "(unnamed)",
            "script_count": len(comp),
            "entry_points": sorted(entry_pts)[:5],
            "method": "networkx_components",
        }
        if bottleneck:
            cluster_info["bottleneck"] = bottleneck
        if cycle_scripts & comp:
            cluster_info["has_cycles"] = True

        result.append(cluster_info)

    # Add cycle info at top level if any
    if cycles:
        result.insert(0, {
            "_cycles_detected": len(cycles),
            "_cycle_scripts": sorted(cycle_scripts)[:20],
        })

    return result


# ---------------------------------------------------------------------------
# Custom function analysis
# ---------------------------------------------------------------------------

def analyze_custom_functions(solution_name):
    """Analyze custom functions: inventory and dependency chains."""
    cf_dir = XML_PARSED_DIR / "custom_functions_sanitized" / solution_name
    if not cf_dir.exists():
        return {"total": 0, "note": "no custom functions directory found"}

    stub_dir = XML_PARSED_DIR / "custom_function_stubs" / solution_name

    # Recurse into folder subdirectories — CFs organised into FileMaker
    # folders are nested in "{FolderName} - ID N/" subdirs, and a flat
    # glob would silently skip them.
    cf_files = sorted(cf_dir.rglob("*.txt"))
    functions = {}
    all_cf_names = set()

    # First pass: collect names and read all content
    cf_data = []  # (name, id, text, param_count, folder_path) — single pass I/O
    for cf_path in cf_files:
        # Folder/separator pseudo-files (e.g. "ACCOUNTS - ID 39.txt",
        # "-- - ID 37.txt") are emitted at the flat level by fmparse.sh
        # alongside the per-folder subdirs. They have no backing stub XML
        # and are not real custom functions — skip them.
        rel_parent = cf_path.parent.relative_to(cf_dir)
        stub_path = stub_dir / rel_parent / f"{cf_path.stem}.xml"
        if not stub_path.exists():
            continue

        name = cf_path.stem.rsplit(" - ID ", 1)[0]
        all_cf_names.add(name)
        cf_id = None
        id_match = re.search(r'ID (\d+)$', cf_path.stem)
        if id_match:
            cf_id = id_match.group(1)
        try:
            with open(cf_path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        # Derive the folder path (parent dirs between the solution root and
        # the file), stripping " - ID N" suffixes to match scripts/layouts.
        folder_path = re.sub(r' - ID \d+', '', str(rel_parent)) if str(rel_parent) != '.' else ''

        # Read param count from stub XML (ObjectList/@membercount).
        param_count = 0
        try:
            stub_text = stub_path.read_text(encoding="utf-8")
            mc_match = re.search(r'membercount="(\d+)"', stub_text)
            if mc_match:
                param_count = int(mc_match.group(1))
        except (OSError, UnicodeDecodeError):
            pass

        cf_data.append((name, cf_id, text, param_count, folder_path))

    # Patterns for classification
    _FIELD_REF_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_ ]*::[A-Za-z_]')
    _UTILITY_MARKUP_RE = re.compile(r'<svg|<path |<html|<div |<style')
    _UTILITY_JS_FUNC_RE = re.compile(r'function\s*\(')
    _UTILITY_JS_KW_RE = re.compile(r'\bvar\b|\bconst\b|\blet\b|\breturn\b')
    _UTILITY_CSS_RE = re.compile(r'\{margin:|\{padding:|\{display:|\{font-|\{line-height:')
    _BLOCK_KW_RE = re.compile(
        r'\bLet\s*\(|\bWhile\s*\(|\bCase\s*\(|\bIf\s*\(|\bFor\s*\(',
        re.IGNORECASE,
    )

    for name, cf_id, text, param_count, folder_path in cf_data:

        # Find references to other custom functions (substring match)
        deps = sorted(
            other_name for other_name in all_cf_names
            if other_name != name and other_name in text
        )

        line_count = len(text.strip().split("\n"))

        functions[name] = {
            "id": cf_id,
            "param_count": param_count,
            "line_count": line_count,
            "folder_path": folder_path,
            "dependencies": deps,
            "text": text,
        }

    # Classify using four categories (evaluated in order, first match wins):
    #   1. utility          – embedded non-FM code (JS/CSS/SVG/HTML)
    #   2. solution_specific – contains TO::Field references
    #   3. constant         – zero params + no block-level keywords
    #   4. functional       – everything else
    categories = {
        "constant": [], "functional": [],
        "solution_specific": [], "utility": [],
    }
    for name, info in functions.items():
        text = info["text"]
        body_size = len(text)

        # 1. Utility: embedded JS/CSS/SVG/HTML
        is_utility = bool(
            _UTILITY_MARKUP_RE.search(text)
            or (body_size > 2000
                and _UTILITY_JS_FUNC_RE.search(text)
                and _UTILITY_JS_KW_RE.search(text))
            or _UTILITY_CSS_RE.search(text)
        )

        if is_utility:
            categories["utility"].append(name)
        # 2. Solution-specific: TO::Field reference
        elif _FIELD_REF_RE.search(text):
            categories["solution_specific"].append(name)
        # 3. Constant: zero params + no block keywords
        elif info["param_count"] == 0 and not _BLOCK_KW_RE.search(text):
            categories["constant"].append(name)
        # 4. Functional: everything else
        else:
            categories["functional"].append(name)

    # Drop text from output to keep return value lightweight
    for info in functions.values():
        del info["text"]

    return {
        "total": len(functions),
        "functions": functions,
        "categories": {k: len(v) for k, v in categories.items()},
        "dependency_chains": _find_cf_chains(functions),
    }


def _find_cf_chains(functions):
    """Find the longest dependency chains in custom functions.

    Uses memoized DFS with cycle detection instead of exponential visited.copy().
    """
    if not functions:
        return []

    PENDING = -1  # Sentinel: currently being computed (cycle detection)
    memo = {}  # name -> depth (memoized result)

    def _chain_depth(name):
        if name in memo:
            return max(memo[name], 0)  # PENDING (-1) -> 0
        if name not in functions:
            return 0
        memo[name] = PENDING  # Mark as in-progress
        deps = functions[name].get("dependencies", [])
        if not deps:
            memo[name] = 1
            return 1
        depth = 1 + max(_chain_depth(d) for d in deps)
        memo[name] = depth
        return depth

    chains = [(name, _chain_depth(name)) for name in functions]
    chains.sort(key=lambda x: x[1], reverse=True)
    return [{"function": name, "depth": depth} for name, depth in chains[:5] if depth > 1]


# ---------------------------------------------------------------------------
# Layout analysis
# ---------------------------------------------------------------------------

def analyze_layouts(solution_name, solution_dir, layouts_index, scripts_index,
                    script_cache=None):
    """Analyze layouts: inventory, classification, portal usage."""
    # Organize by base TO
    by_base_to = collections.defaultdict(list)
    for layout in layouts_index:
        base_to = layout["base_to"] or "(none)"
        by_base_to[base_to].append(layout["name"])

    # Organize by folder
    by_folder = collections.defaultdict(list)
    for layout in layouts_index:
        folder = layout["folder"] or "(root)"
        by_folder[folder].append(layout["name"])

    # Layout classification heuristics
    classifications = collections.Counter()
    classified = {}
    for layout in layouts_index:
        name_lower = layout["name"].lower()
        if any(kw in name_lower for kw in ["list", "search", "browse"]):
            cat = "list"
        elif any(kw in name_lower for kw in ["detail", "entry", "edit", "form"]):
            cat = "detail"
        elif any(kw in name_lower for kw in ["dialog", "popup", "pop up", "modal"]):
            cat = "dialog"
        elif any(kw in name_lower for kw in ["report", "print", "pdf"]):
            cat = "report"
        elif any(kw in name_lower for kw in ["menu", "nav", "dashboard"]):
            cat = "navigation"
        else:
            cat = "other"
        classifications[cat] += 1
        classified[layout["name"]] = cat

    # Check layout summaries if available
    layout_summaries_dir = solution_dir / "layouts"
    portal_usage = []
    button_wiring = collections.Counter()
    field_coverage = collections.Counter()

    if layout_summaries_dir.exists():
        for json_path in sorted(layout_summaries_dir.glob("*.json")):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    summary = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            layout_name = summary.get("layout", json_path.stem)
            _walk_layout_objects(summary, layout_name, portal_usage,
                                button_wiring, field_coverage)

    # Detect orphaned layouts (not referenced by any script)
    script_referenced_layouts = set()
    if script_cache is not None:
        for info in script_cache:
            for ref in info["layout_refs"]:
                script_referenced_layouts.add(ref)
    else:
        for script_path in find_script_files(solution_name):
            try:
                with open(script_path, "r", encoding="utf-8") as f:
                    text = f.read()
                for match in RE_LAYOUT_REF.findall(text):
                    script_referenced_layouts.add(match)
            except (OSError, UnicodeDecodeError):
                continue

    layout_names = set(l["name"] for l in layouts_index)
    orphaned = sorted(layout_names - script_referenced_layouts)

    return {
        "total": len(layouts_index),
        "by_base_to": {k: len(v) for k, v in sorted(by_base_to.items())},
        "by_folder": {k: len(v) for k, v in sorted(by_folder.items())},
        "classifications": dict(classifications),
        "portals": portal_usage[:20] if portal_usage else [],
        "button_wiring_count": len(button_wiring),
        "orphaned_layouts": orphaned,
        "has_layout_summaries": layout_summaries_dir.exists()
            and any(layout_summaries_dir.glob("*.json")),
    }


_BUILTIN_LAYOUT_SIGNALS = {
    "developer_prefixes": ["@"],
    "utility_prefixes": ["blank ", "json ", "api "],
    "utility_names": ["vlist"],
    "output_suffixes": ["pdf"],
    "output_names": [],
    "output_patterns": ["print", "report", "scorecard", "hardcopy",
                        "compass", "double", "single", "ladder",
                        "round robin", "face-off"],
    "ignore_names": [],
    "dual_purpose_button_threshold": 10,
}


def _load_layout_signals():
    """Load layout classification signals from config files.

    Priority: layout-signals.json (override) > layout-signals.json.example
    (shipped defaults) > _BUILTIN_LAYOUT_SIGNALS (hardcoded fallback).

    The builtin fallback ensures classification works even if both config
    files are missing or corrupt.
    """
    config_dir = PROJECT_ROOT / "agent" / "config"
    result = dict(_BUILTIN_LAYOUT_SIGNALS)

    # Layer 1: shipped defaults from .example file
    example_path = config_dir / "layout-signals.json.example"
    if example_path.exists():
        try:
            with open(example_path, "r", encoding="utf-8") as f:
                example = json.load(f)
            result.update({k: v for k, v in example.items()
                           if not k.startswith("_")})
        except (json.JSONDecodeError, OSError):
            pass

    # Layer 2: developer overrides from .json file
    override_path = config_dir / "layout-signals.json"
    if override_path.exists():
        try:
            with open(override_path, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            result.update({k: v for k, v in overrides.items()
                           if not k.startswith("_")})
        except (json.JSONDecodeError, OSError):
            pass

    return result


def _classify_layout_purpose(layout_name, button_count, folder_path="",
                             has_pdf_script=False, signals=None):
    """Classify a layout's purpose as ui/output/utility/developer.

    Uses the decision tree from references/layout-classifications.md.
    All convention-dependent signals come from layout-signals.json.example
    (defaults) overlaid by layout-signals.json (overrides). The button
    count signal is always applied as a structural fallback.
    """
    name_lower = layout_name.lower()

    # Extract signals (all from config, no hardcoded defaults)
    dev_prefixes = [p.lower() for p in (signals or {}).get("developer_prefixes", [])]
    util_prefixes = [p.lower() for p in (signals or {}).get("utility_prefixes", [])]
    util_names = {n.lower() for n in (signals or {}).get("utility_names", [])}
    out_suffixes = [s.lower() for s in (signals or {}).get("output_suffixes", [])]
    out_names = {n.lower() for n in (signals or {}).get("output_names", [])}
    out_patterns = [p.lower() for p in (signals or {}).get("output_patterns", [])]
    ignore_names = {n.lower() for n in (signals or {}).get("ignore_names", [])}
    dual_threshold = (signals or {}).get("dual_purpose_button_threshold", 10)

    # 0. Ignored layouts
    if name_lower in ignore_names:
        return "utility"

    # 1. Developer prefix
    for prefix in dev_prefixes:
        if name_lower.startswith(prefix):
            return "developer"

    # 2. Utility prefix/name
    for prefix in util_prefixes:
        if name_lower.startswith(prefix):
            return "utility"
    if name_lower in util_names:
        return "utility"

    # 3-6. Output detection with dual-purpose awareness
    is_output = False

    if has_pdf_script:
        is_output = True

    if not is_output:
        for suffix in out_suffixes:
            if name_lower.endswith(suffix):
                is_output = True
                break
    if not is_output and name_lower in out_names:
        is_output = True
    if not is_output:
        for pat in out_patterns:
            if pat in name_lower:
                is_output = True
                break

    if is_output:
        if button_count >= dual_threshold:
            return "output/ui"
        return "output"

    # 7-8. Button count threshold (universal, not convention-dependent)
    if button_count >= 2:
        return "ui"

    return "utility"


def _count_layout_buttons(solution_name):
    """Count <Button> elements in each layout XML file.

    Returns a dict of {layout_name: button_count}.
    """
    import xml.etree.ElementTree as ET

    layout_dir = XML_PARSED_DIR / "layouts" / solution_name
    counts = {}
    if not layout_dir.exists():
        return counts

    for xml_path in layout_dir.rglob("*.xml"):
        name = xml_path.stem.rsplit(" - ID ", 1)[0]
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            counts[name] = len(list(root.iter("Button")))
        except ET.ParseError:
            counts[name] = 0

    return counts


def _find_pdf_layouts(script_cache):
    """Find layouts referenced by scripts that use Save Records as PDF.

    Returns a set of layout names.
    """
    pdf_layouts = set()
    if not script_cache:
        return pdf_layouts

    re_pdf = re.compile(r"Save Records as PDF", re.IGNORECASE)
    re_goto = re.compile(
        r'Go to Layout\s*\[\s*"([^"]+)"', re.IGNORECASE
    )
    for info in script_cache:
        text = info.get("text", "")
        if re_pdf.search(text):
            for match in re_goto.findall(text):
                pdf_layouts.add(match)
    return pdf_layouts


def classify_layouts(solution_name, layouts_index, script_cache=None):
    """Classify all layouts by purpose (ui/output/utility/developer).

    Loads convention-dependent signals from agent/config/layout-signals.json
    when present, otherwise uses defaults. The button count signal is always
    applied as a structural fallback.

    Returns a dict with:
      - by_purpose: {purpose: count}
      - layouts: [{name, purpose, button_count}, ...]
      - ui_concentration: layout concentration computed from UI layouts only
    """
    button_counts = _count_layout_buttons(solution_name)
    pdf_layouts = _find_pdf_layouts(script_cache)
    signals = _load_layout_signals()

    classified = []
    by_purpose = collections.Counter()

    for layout in layouts_index:
        name = layout["name"]
        bc = button_counts.get(name, 0)
        folder = layout.get("folder", "")
        has_pdf = name in pdf_layouts

        purpose = _classify_layout_purpose(name, bc, folder, has_pdf, signals)
        by_purpose[purpose] += 1
        classified.append({
            "name": name,
            "purpose": purpose,
            "button_count": bc,
            "base_to": layout.get("base_to", ""),
        })

    # Compute UI-only layout concentration
    # Include output/ui (dual-purpose) in UI concentration — they are user-facing
    ui_layouts = [c for c in classified if c["purpose"] in ("ui", "output/ui")]
    ui_by_to = collections.Counter(c["base_to"] for c in ui_layouts if c["base_to"])
    ui_concentration = None
    if ui_by_to:
        total_ui = sum(ui_by_to.values())
        sorted_counts = sorted(ui_by_to.values(), reverse=True)
        top_to_pct = sorted_counts[0] / total_ui if sorted_counts else 0

        cover_50 = len(sorted_counts)
        running = 0
        for i, c in enumerate(sorted_counts):
            running += c
            if running >= total_ui * 0.5:
                cover_50 = i + 1
                break

        ui_concentration = {
            "total_ui": total_ui,
            "distinct_ui_tos": len(ui_by_to),
            "top_to_pct": round(top_to_pct, 2),
            "cover_50": cover_50,
            "spread_ratio": round(cover_50 / max(len(ui_by_to), 1), 2),
        }

    return {
        "by_purpose": dict(by_purpose),
        "layouts": classified,
        "ui_concentration": ui_concentration,
    }


def _walk_layout_objects(obj, layout_name, portal_usage, button_wiring, field_coverage):
    """Recursively walk layout summary JSON for portal/button/field data."""
    if isinstance(obj, dict):
        obj_type = obj.get("type", "")
        if obj_type == "Portal":
            portal_usage.append({
                "layout": layout_name,
                "table": obj.get("table", "unknown"),
            })
        if "script" in obj:
            button_wiring[obj["script"]] += 1
        if "field" in obj:
            field_coverage[obj["field"]] += 1

        # Recurse into children
        for key in ("objects", "parts"):
            children = obj.get(key, [])
            if isinstance(children, list):
                for child in children:
                    _walk_layout_objects(child, layout_name, portal_usage,
                                        button_wiring, field_coverage)
    elif isinstance(obj, list):
        for item in obj:
            _walk_layout_objects(item, layout_name, portal_usage,
                                button_wiring, field_coverage)


# ---------------------------------------------------------------------------
# Integration points
# ---------------------------------------------------------------------------

def analyze_integrations(solution_name, value_lists_index, scripts_index,
                        script_cache=None):
    """Analyze external data sources, value lists, and external script calls."""
    # External data sources
    eds_dir = XML_PARSED_DIR / "external_data_sources" / solution_name
    external_sources = []
    if eds_dir.exists():
        for xml_path in sorted(eds_dir.glob("*.xml")):
            external_sources.append(xml_path.stem)

    # Value lists
    vl_by_source = collections.Counter(vl["source_type"] for vl in value_lists_index)

    # External calls from scripts (use cache if available)
    external_call_scripts = collections.defaultdict(list)
    if script_cache is not None:
        for info in script_cache:
            script_name = info["name"]
            for flag, label in [
                ("has_insert_from_url", "Insert from URL"),
                ("has_send_mail", "Send Mail"),
                ("has_export_records", "Export Records"),
                ("has_import_records", "Import Records"),
            ]:
                if info[flag]:
                    external_call_scripts[label].append(script_name)
    else:
        for script_path in find_script_files(solution_name):
            try:
                with open(script_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            script_name = script_path.stem.rsplit(" - ID ", 1)[0]
            for pattern, label in [
                (RE_INSERT_FROM_URL, "Insert from URL"),
                (RE_SEND_MAIL, "Send Mail"),
                (RE_EXPORT_RECORDS, "Export Records"),
                (RE_IMPORT_RECORDS, "Import Records"),
            ]:
                if pattern.search(text):
                    external_call_scripts[label].append(script_name)

    return {
        "external_data_sources": external_sources,
        "value_lists": {
            "total": len(value_lists_index),
            "by_source": dict(vl_by_source),
        },
        "external_calls": {
            label: {"count": len(scripts), "scripts": scripts[:10]}
            for label, scripts in external_call_scripts.items()
        },
    }


# ---------------------------------------------------------------------------
# Multi-file solution detection
# ---------------------------------------------------------------------------

def _extract_filenames_from_path(path_str):
    """Extract FM filenames from a UniversalPathList value.

    Parses file: and fmnet: entries, strips .fmp12 extensions.
    Skips variable references ($$VAR, $var).
    Returns a set of candidate filenames.
    """
    filenames = set()
    for line in path_str.replace("\r", "\n").split("\n"):
        part = line.strip()
        if not part or part.startswith("$"):
            continue
        # file:Name or file:Name.fmp12
        m = re.match(r"file:(.+?)(?:\.fmp12)?$", part)
        if m:
            filenames.add(m.group(1))
            continue
        # fmnet:/host/Name or fmnet:/host/Name.fmp12
        m = re.match(r"fmnet:/[^/]+/(.+?)(?:\.fmp12)?$", part)
        if m:
            filenames.add(m.group(1))
    return filenames


def detect_multi_file(solution_name, to_index=None):
    """Detect if this solution references other FM files.

    Uses a multi-level resolution strategy to map external data source names
    to correlated solution names:

    Level 1 — Literal path resolution: Parse UniversalPathList for file:/fmnet:
              entries and match the extracted filename against available solutions.
              Deterministic, no heuristics.

    Level 2 — Base table overlap: For EDS entries with variable-based paths,
              compare the external TOs grouped by that data source against each
              candidate solution's local base tables. Highest overlap wins.

    Returns a dict with multi-file metadata including data_source_map
    (mapping EDS names to correlated solution names) and file_architecture.
    """
    eds_dir = XML_PARSED_DIR / "external_data_sources" / solution_name
    if not eds_dir.exists():
        return {
            "is_multi_file": False,
            "file_architecture": "single",
        }

    import xml.etree.ElementTree as ET

    # Parse external data source XML files
    data_sources = []  # list of {name, id, type, path, filenames, has_variable}
    for xml_path in sorted(eds_dir.glob("*.xml")):
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            ds_name = root.get("name", xml_path.stem.split(" - ")[0])
            ds_id = root.get("id", "")
            ds_type = root.get("type", "")
            path_el = root.find(".//UniversalPathList")
            ds_path = path_el.text.strip() if path_el is not None and path_el.text else ""
            filenames = _extract_filenames_from_path(ds_path)
            has_variable = any(
                p.strip().startswith("$")
                for p in ds_path.replace("\r", "\n").split("\n")
                if p.strip()
            )
            data_sources.append({
                "name": ds_name,
                "id": ds_id,
                "type": ds_type,
                "path": ds_path,
                "filenames": sorted(filenames),
                "has_variable": has_variable,
            })
        except ET.ParseError:
            data_sources.append({
                "name": xml_path.stem.split(" - ")[0],
                "id": "",
                "type": "",
                "path": "",
                "filenames": [],
                "has_variable": False,
            })

    referenced_files = [ds["name"] for ds in data_sources]

    # Find all solutions that have been exploded and indexed
    all_solutions = set()
    for domain_dir in XML_PARSED_DIR.iterdir():
        if domain_dir.is_dir() and domain_dir.name != "_":
            for sol_dir in domain_dir.iterdir():
                if sol_dir.is_dir():
                    all_solutions.add(sol_dir.name)
    all_solutions.discard(solution_name)  # exclude self

    available_solutions = {s for s in all_solutions
                           if (CONTEXT_DIR / s).exists()}

    # ---------------------------------------------------------------
    # Level 1: Literal path resolution
    # Match extracted filenames from UniversalPathList against available
    # solutions. This is deterministic — no heuristics needed.
    # ---------------------------------------------------------------
    data_source_map = {}
    unresolved_ds = []  # EDS entries that need Level 2 fallback

    for ds in data_sources:
        matched = False
        for fname in ds["filenames"]:
            if fname in available_solutions:
                data_source_map[ds["name"]] = fname
                matched = True
                break
        if not matched and ds["has_variable"]:
            # Variable-based path with no literal fallback — needs Level 2
            unresolved_ds.append(ds["name"])

    # ---------------------------------------------------------------
    # Level 2: Base table overlap (fallback for variable-based paths)
    # For unresolved EDS entries, compare their external TOs' base tables
    # against candidate solutions' local base tables.
    # ---------------------------------------------------------------
    if unresolved_ds and to_index and available_solutions:
        # Group external TOs by data source, collecting their base tables
        ds_base_tables = collections.defaultdict(set)
        for row in to_index:
            if (row.get("type") == "External"
                    and row.get("data_source") in unresolved_ds):
                ds_base_tables[row["data_source"]].add(row["base_table"])

        if ds_base_tables:
            # Only load candidates not already resolved by Level 1
            already_mapped = set(data_source_map.values())
            candidates = available_solutions - already_mapped

            corr_local_tables = {}
            for corr_name in candidates:
                corr_to = load_table_occurrences_index(CONTEXT_DIR / corr_name)
                local_tables = set()
                for row in corr_to:
                    if row.get("type", "") in ("Local", ""):
                        local_tables.add(row["base_table"])
                corr_fields = load_fields_index(CONTEXT_DIR / corr_name)
                for row in corr_fields:
                    local_tables.add(row["table"])
                corr_local_tables[corr_name] = local_tables

            # Match: data source -> candidate with highest table overlap
            for ds_name, ds_tables in ds_base_tables.items():
                best_match = None
                best_overlap = 0
                for corr_name, corr_tables in corr_local_tables.items():
                    overlap = len(ds_tables & corr_tables)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_match = corr_name
                if best_match and best_overlap > 0:
                    data_source_map[ds_name] = best_match

    # Narrow correlated list to only solutions actually mapped
    correlated = sorted(set(data_source_map.values()))

    # Determine file architecture
    file_architecture = "single"
    if data_source_map:
        # Count external vs local TOs
        external_count = sum(
            1 for row in (to_index or []) if row.get("type") == "External"
        )
        local_count = sum(
            1 for row in (to_index or []) if row.get("type") == "Local"
        )
        total = external_count + local_count
        if total > 0 and external_count / total > 0.5:
            file_architecture = "data_separation"
        else:
            file_architecture = "multi_file"
    elif len(referenced_files) > 0:
        file_architecture = "multi_file"

    # Build per-file summary
    files = []
    if to_index:
        local_tables = set()
        external_to_count = 0
        local_to_count = 0
        for row in to_index:
            if row.get("type") == "Local":
                local_to_count += 1
                local_tables.add(row["base_table"])
            elif row.get("type") == "External":
                external_to_count += 1

        role = "ui" if file_architecture == "data_separation" else "primary"
        files.append({
            "name": solution_name,
            "role": role,
            "local_table_count": len(local_tables),
            "local_to_count": local_to_count,
            "external_to_count": external_to_count,
        })

        # Add correlated solution summaries
        for corr_name in sorted(data_source_map.values()):
            corr_to = load_table_occurrences_index(CONTEXT_DIR / corr_name)
            corr_local_tables = set()
            corr_local_to = 0
            corr_ext_to = 0
            for row in corr_to:
                if row.get("type", "") in ("Local", ""):
                    corr_local_to += 1
                    corr_local_tables.add(row["base_table"])
                elif row.get("type") == "External":
                    corr_ext_to += 1

            corr_role = "data" if file_architecture == "data_separation" else "secondary"
            files.append({
                "name": corr_name,
                "role": corr_role,
                "local_table_count": len(corr_local_tables),
                "local_to_count": corr_local_to,
                "external_to_count": corr_ext_to,
            })

    return {
        "is_multi_file": len(referenced_files) > 0,
        "file_architecture": file_architecture,
        "referenced_files": referenced_files,
        "correlated_solutions": correlated,
        "data_source_map": data_source_map,
        "data_sources": data_sources,
        "files": files,
    }


def load_correlated_tables(correlated_solution):
    """Load index data from a correlated solution to identify table ownership.

    Returns a dict with the solution's local base tables and field/TO data,
    or None if the context directory doesn't exist.
    """
    correlated_dir = CONTEXT_DIR / correlated_solution
    if not correlated_dir.exists():
        return None

    fields = load_fields_index(correlated_dir)
    tos = load_table_occurrences_index(correlated_dir)

    # Determine which base tables are locally defined in this file
    local_tables = set()
    for row in tos:
        if row.get("type", "") in ("Local", ""):
            local_tables.add(row["base_table"])

    # Fields index is ground truth for which tables actually exist in the file
    tables_from_fields = set(row["table"] for row in fields)

    # Build table->field_count map for correlated tables
    table_field_counts = collections.Counter()
    for row in fields:
        table_field_counts[row["table"]] += 1

    relationships = load_relationships_index(correlated_dir)

    return {
        "solution": correlated_solution,
        "local_tables": local_tables | tables_from_fields,
        "table_field_counts": dict(table_field_counts),
        "to_index": tos,
        "fields_index": fields,
        "relationships_index": relationships,
    }


# ---------------------------------------------------------------------------
# Health metrics
# ---------------------------------------------------------------------------

def analyze_health(solution_dir, fields_index, scripts_index, layouts_index,
                   relationships_index, to_index, script_cache=None):
    """Compute health metrics from xref and index data."""
    xref = load_xref_index(solution_dir)

    result = {
        "xref_available": len(xref) > 0,
    }

    if not xref:
        result["note"] = (
            "xref.index not found. Run: python3 agent/scripts/trace.py build "
            f'-s "{solution_dir.name}" to enable health metrics.'
        )
        return result

    # Dead object analysis
    referenced = collections.defaultdict(set)
    for row in xref:
        referenced[row["ref_type"]].add(row["ref_name"])

    # Dead fields
    all_fields = set(f"{row['table']}::{row['field']}" for row in fields_index)
    referenced_fields = referenced.get("field", set())
    dead_fields = all_fields - referenced_fields
    # Filter out system fields
    system_prefixes = ("__kpt", "creation", "modification", "PrimaryKey")
    dead_fields_filtered = [
        f for f in dead_fields
        if not any(f.split("::")[-1].lower().startswith(p.lower())
                   for p in system_prefixes)
    ]

    # Dead scripts
    all_scripts = set(s["name"] for s in scripts_index)
    referenced_scripts = referenced.get("script", set())
    dead_scripts = all_scripts - referenced_scripts

    # Dead custom functions
    referenced_cfs = referenced.get("custom_func", set())

    # Disconnected tables (no relationships)
    tables_in_rels = set()
    to_map = {row["to_name"]: row["base_table"] for row in to_index}
    for r in relationships_index:
        tables_in_rels.add(to_map.get(r["left_to"], ""))
        tables_in_rels.add(to_map.get(r["right_to"], ""))
    all_tables = set(row["table"] for row in fields_index)
    disconnected_tables = sorted(all_tables - tables_in_rels - {""})

    # Empty scripts (0-1 lines)
    empty_scripts = []
    if script_cache is not None:
        for info in script_cache:
            if info["is_empty"]:
                empty_scripts.append(info["name"])
    else:
        for script_path in find_script_files(solution_dir.name):
            try:
                with open(script_path, "r", encoding="utf-8") as f:
                    lines = [l for l in f.read().strip().split("\n") if l.strip()]
                if len(lines) == 0:
                    name = script_path.stem.rsplit(" - ID ", 1)[0]
                    empty_scripts.append(name)
            except (OSError, UnicodeDecodeError):
                continue

    result.update({
        "dead_fields": {
            "count": len(dead_fields_filtered),
            "sample": sorted(dead_fields_filtered)[:20],
        },
        "dead_scripts": {
            "count": len(dead_scripts),
            "sample": sorted(dead_scripts)[:20],
        },
        "disconnected_tables": disconnected_tables,
        "empty_scripts": empty_scripts[:20],
        "total_xref_entries": len(xref),
    })

    return result


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def ensure_prerequisites(solution_name, solution_dir):
    """Build missing prerequisites (xref.index, layout summaries)."""
    built = []

    # Check xref.index
    xref_path = solution_dir / "xref.index"
    if not xref_path.exists():
        print(f"  Building xref.index...")
        trace_script = SCRIPT_DIR / "trace.py"
        result = subprocess.run(
            ["python3", str(trace_script), "build", "-s", solution_name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            built.append("xref.index")
            print(f"    Done.")
        else:
            print(f"    WARNING: trace.py build failed: {result.stderr.strip()}")

    # Check layout summaries
    layouts_dir = solution_dir / "layouts"
    layout_xml_dir = XML_PARSED_DIR / "layouts" / solution_name
    if layout_xml_dir.exists() and (
        not layouts_dir.exists() or not any(layouts_dir.glob("*.json"))
    ):
        print(f"  Building layout summaries...")
        summary_script = SCRIPT_DIR / "layout_to_summary.py"
        result = subprocess.run(
            ["python3", str(summary_script), "--solution", solution_name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            built.append("layout summaries")
            print(f"    Done.")
        else:
            print(f"    WARNING: layout_to_summary.py failed: {result.stderr.strip()}")

    return built


# ---------------------------------------------------------------------------
# Profile assembly
# ---------------------------------------------------------------------------

def build_profile(solution_name, deep=False, correlated_solutions=None):
    """Build the complete solution profile.

    Args:
        solution_name: Name of the primary solution to analyze.
        deep: Enable full script text analysis.
        correlated_solutions: Explicit list of correlated solution names for
            multi-file analysis. If None, auto-detects from external data sources.
    """
    global _T0
    _T0 = time.monotonic()
    phase_times = {}

    solution_dir = CONTEXT_DIR / solution_name

    if not solution_dir.exists():
        print(f"ERROR: No context directory for '{solution_name}'", file=sys.stderr)
        print(f"  Expected: {solution_dir}", file=sys.stderr)
        sys.exit(1)

    _status("init", "info", label=f"==> Analyzing solution: {solution_name}")

    # Load all index files
    t = time.monotonic()
    fields_index = load_fields_index(solution_dir)
    relationships_index = load_relationships_index(solution_dir)
    to_index = load_table_occurrences_index(solution_dir)
    scripts_index = load_scripts_index(solution_dir)
    layouts_index = load_layouts_index(solution_dir)
    value_lists_index = load_value_lists_index(solution_dir)
    phase_times["index_loading"] = round(time.monotonic() - t, 4)

    _status("loading", "info",
            label=f"  Loaded: {len(fields_index)} fields, {len(to_index)} TOs, "
                  f"{len(scripts_index)} scripts, {len(layouts_index)} layouts, "
                  f"{len(relationships_index)} relationships, "
                  f"{len(value_lists_index)} value lists")

    # Load script cache once (eliminates 3x redundant file reads)
    _status("script_cache", "start", label="Loading script files...")
    t = time.monotonic()
    script_cache = load_script_cache(solution_name, scripts_index)
    dt = round(time.monotonic() - t, 4)
    phase_times["script_cache"] = dt
    _status("script_cache", "end", elapsed=dt, items=len(script_cache))

    # Detect multi-file references early (needed for data model analysis)
    _status("multi_file", "start", label="Detecting multi-file references...")
    t = time.monotonic()
    multi_file = detect_multi_file(solution_name, to_index=to_index)
    dt = round(time.monotonic() - t, 4)
    phase_times["multi_file"] = dt
    _status("multi_file", "end", elapsed=dt)

    # Load correlated solution data for cross-file attribution
    correlated_data = {}
    if correlated_solutions is not None:
        resolved = correlated_solutions
    else:
        resolved = multi_file.get("correlated_solutions", [])

    if resolved:
        _status("correlated", "start",
                label=f"Loading correlated solutions: {', '.join(resolved)}")
        t = time.monotonic()
        for corr_name in resolved:
            corr_data = load_correlated_tables(corr_name)
            if corr_data:
                correlated_data[corr_name] = corr_data
        dt = round(time.monotonic() - t, 4)
        phase_times["correlated"] = dt
        _status("correlated", "end", elapsed=dt, items=len(correlated_data))

    # Classify layouts early (needed for topology signal in data_model)
    _status("layout_classification", "start",
            label="Classifying layouts (button analysis)...")
    t = time.monotonic()
    layout_classification = classify_layouts(
        solution_name, layouts_index, script_cache=script_cache,
    )
    dt = round(time.monotonic() - t, 4)
    phase_times["layout_classification"] = dt
    _status("layout_classification", "end", elapsed=dt)

    # Analyze each domain
    _status("data_model", "start", label="Analyzing data model...")
    t = time.monotonic()
    data_model = analyze_data_model(
        fields_index, to_index, relationships_index,
        solution_name=solution_name,
        multi_file_info=multi_file if multi_file.get("is_multi_file") else None,
        correlated_data=correlated_data or None,
        layouts_index=layouts_index,
        layout_classification=layout_classification,
    )
    dt = round(time.monotonic() - t, 4)
    phase_times["data_model"] = dt
    _status("data_model", "end", elapsed=dt)

    _status("naming", "start", label="Detecting naming conventions...")
    t = time.monotonic()
    conventions = detect_naming_conventions(fields_index)
    dt = round(time.monotonic() - t, 4)
    phase_times["naming"] = dt
    _status("naming", "end", elapsed=dt)

    _status("scripts", "start", label="Analyzing scripts...")
    t = time.monotonic()
    scripts = analyze_scripts(solution_name, scripts_index, script_cache,
                              deep=deep)
    dt = round(time.monotonic() - t, 4)
    phase_times["scripts"] = dt
    _status("scripts", "end", elapsed=dt, items=len(script_cache))

    _status("custom_functions", "start", label="Analyzing custom functions...")
    t = time.monotonic()
    custom_functions = analyze_custom_functions(solution_name)
    dt = round(time.monotonic() - t, 4)
    phase_times["custom_functions"] = dt
    _status("custom_functions", "end", elapsed=dt,
            items=custom_functions.get("total", 0))

    _status("layouts", "start", label="Analyzing layouts...")
    t = time.monotonic()
    layouts = analyze_layouts(solution_name, solution_dir, layouts_index,
                              scripts_index, script_cache=script_cache)
    dt = round(time.monotonic() - t, 4)
    phase_times["layouts"] = dt
    _status("layouts", "end", elapsed=dt, items=layouts["total"])

    _status("integrations", "start", label="Analyzing integrations...")
    t = time.monotonic()
    integrations = analyze_integrations(solution_name, value_lists_index,
                                        scripts_index,
                                        script_cache=script_cache)
    dt = round(time.monotonic() - t, 4)
    phase_times["integrations"] = dt
    _status("integrations", "end", elapsed=dt)

    _status("health", "start", label="Computing health metrics...")
    t = time.monotonic()
    health = analyze_health(
        solution_dir, fields_index, scripts_index, layouts_index,
        relationships_index, to_index, script_cache=script_cache,
    )
    dt = round(time.monotonic() - t, 4)
    phase_times["health"] = dt
    _status("health", "end", elapsed=dt)

    # Per-file relationship graphs (multi-file solutions)
    per_file_graphs = {}
    if multi_file.get("is_multi_file") and correlated_data:
        _status("per_file_graphs", "start",
                label="Building per-file relationship graphs...")
        t = time.monotonic()
        per_file_graphs = build_per_file_graphs(
            solution_name, fields_index, to_index, relationships_index,
            multi_file, correlated_data,
        )
        dt = round(time.monotonic() - t, 4)
        phase_times["per_file_graphs"] = dt
        _status("per_file_graphs", "end", elapsed=dt,
                items=len(per_file_graphs))

    # Extension availability
    extensions_used = [
        name for name, info in EXTENSIONS.items() if info["available"]
    ]
    extensions_skipped = [
        name for name, info in EXTENSIONS.items() if not info["available"]
    ]

    profile = {
        "solution": solution_name,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "generator": "analyze.py",
        "deep_mode": deep,
        "extensions": {
            "used": extensions_used,
            "skipped": extensions_skipped,
        },
        "summary": {
            "tables": data_model["table_count"],
            "fields": data_model["total_fields"],
            "table_occurrences": data_model["to_count"],
            "relationships": data_model["relationships"]["total"],
            "scripts": scripts["total_scripts"],
            "layouts": layouts["total"],
            "custom_functions": custom_functions["total"],
            "value_lists": integrations["value_lists"]["total"],
        },
        "data_model": data_model,
        "naming_conventions": conventions,
        "business_logic": scripts,
        "custom_functions": custom_functions,
        "ui_layer": {**layouts, "layout_purpose": layout_classification},
        "integrations": integrations,
        "multi_file": multi_file,
        "per_file_graphs": per_file_graphs,
        "health": health,
    }

    _status("complete", "complete", phases=phase_times)
    return profile


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------

def format_html(profile):
    """Format the profile as a self-contained HTML report."""
    # Template lives in the skill's assets folder per agentskills.io spec.
    # Try skill folder first, fall back to alongside this script.
    skill_assets = PROJECT_ROOT / ".cursor" / "skills" / "solution-analysis" / "assets"
    template_path = skill_assets / "report_template.html"
    if not template_path.exists():
        template_path = SCRIPT_DIR / "report_template.html"
    if not template_path.exists():
        print(f"ERROR: HTML template not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    # Embed the profile JSON into the template.
    # Escape </script> and <!-- sequences that would break the HTML parser.
    profile_json = json.dumps(profile, ensure_ascii=False)
    profile_json = profile_json.replace("</", "<\\/")
    profile_json = profile_json.replace("<!--", "<\\!--")

    html = template.replace("{{PROFILE_JSON}}", profile_json)
    html = html.replace("{{SOLUTION_NAME}}", profile["solution"])
    html = html.replace("{{GENERATED_AT}}", profile["generated_at"])

    return html


def format_markdown(profile):
    """Format the profile as a markdown specification document."""
    lines = []
    sol = profile["solution"]
    summary = profile["summary"]

    lines.append(f"# Solution Analysis: {sol}")
    lines.append("")
    lines.append(f"*Generated: {profile['generated_at']}*")
    if profile["deep_mode"]:
        lines.append("*Mode: Deep analysis*")
    lines.append("")

    # --- Overview ---
    lines.append("## Overview")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    for label, key in [
        ("Base Tables", "tables"),
        ("Fields", "fields"),
        ("Table Occurrences", "table_occurrences"),
        ("Relationships", "relationships"),
        ("Scripts", "scripts"),
        ("Layouts", "layouts"),
        ("Custom Functions", "custom_functions"),
        ("Value Lists", "value_lists"),
    ]:
        lines.append(f"| {label} | {summary[key]} |")
    lines.append("")

    # Extensions note
    ext = profile.get("extensions", {})
    if ext.get("skipped"):
        lines.append(
            f"*Optional extensions not installed: {', '.join(ext['skipped'])}. "
            f"Install via `pip3 install -r .cursor/skills/solution-analysis/assets/requirements-analyze.txt` "
            f"for deeper analysis.*"
        )
        lines.append("")

    # --- Data Model ---
    dm = profile["data_model"]
    lines.append("## Data Model")
    lines.append("")

    # Tables
    lines.append("### Base Tables")
    lines.append("")
    has_multi_sources = len(set(
        t.get("source_file", sol) for t in dm["tables"].values()
    )) > 1
    if has_multi_sources:
        lines.append("| Table | Source | Fields | PK | FKs | Primary Types |")
        lines.append("|-------|--------|--------|----|-----|---------------|")
    else:
        lines.append("| Table | Fields | PK | FKs | Primary Types |")
        lines.append("|-------|--------|----|-----|---------------|")
    for tname, t in sorted(dm["tables"].items()):
        pk = "Yes" if t["has_primary_key"] else "No"
        top_types = ", ".join(
            f"{k}: {v}" for k, v in sorted(
                t["by_datatype"].items(), key=lambda x: x[1], reverse=True
            )[:3]
        )
        if has_multi_sources:
            src = t.get("source_file", sol)
            lines.append(
                f"| {tname} | {src} | {t['field_count']} | {pk} | {t['foreign_key_count']} | {top_types} |"
            )
        else:
            lines.append(
                f"| {tname} | {t['field_count']} | {pk} | {t['foreign_key_count']} | {top_types} |"
            )
    lines.append("")

    # Topology
    topo = dm.get("topology", {})
    if topo:
        lines.append("### Relationship Graph Topology")
        lines.append("")
        lines.append(f"- **Pattern:** {topo.get('pattern', 'unknown')}")
        if "confidence" in topo:
            lines.append(f"- **Confidence:** {topo['confidence']}")
        lines.append(f"- **Avg degree:** {topo.get('avg_degree', 'N/A')}")
        lines.append(f"- **Max degree:** {topo.get('max_degree', 'N/A')}")
        lines.append(f"- **Hub TOs (degree >= 5):** {topo.get('hub_count', 0)}")
        if topo.get("anchor_tables"):
            lines.append(f"- **Anchor tables:** {', '.join(topo['anchor_tables'])}")
        if topo.get("connected_components", 0) > 1:
            lines.append(f"- **Connected components:** {topo['connected_components']}")
        if topo.get("bridge_count"):
            lines.append(f"- **Bridge relationships:** {topo['bridge_count']}")
        lines.append("")

    # Relationships summary
    rels = dm["relationships"]
    lines.append("### Relationships")
    lines.append("")
    lines.append(f"- **Total:** {rels['total']}")
    lines.append(f"- **Join types:** {', '.join(f'{k}: {v}' for k, v in rels['by_join_type'].items())}")
    lines.append(f"- **Cascade create:** {rels['cascades']['create']}")
    lines.append(f"- **Cascade delete:** {rels['cascades']['delete']}")
    lines.append(f"- **Multi-predicate joins:** {rels['multi_predicate']}")
    lines.append(f"- **Self-joins:** {rels['self_joins']}")
    lines.append("")

    # ERD (Mermaid) — collapse TOs to base tables and draw edges
    lines.append("### Entity Relationship Diagram")
    lines.append("")

    # Use multi-file ERD only when tables actually come from multiple source files
    source_files = set(
        t.get("source_file", sol) for t in dm["tables"].values()
    )
    use_multi_erd = len(source_files) > 1
    if use_multi_erd:
        # Use flowchart with subgraphs to show file ownership
        lines.append("```mermaid")
        lines.append("flowchart LR")

        # Group tables by source_file
        tables_by_source = collections.defaultdict(list)
        for tname, t in dm["tables"].items():
            src = t.get("source_file", sol)
            tables_by_source[src].append(tname)

        # Determine role labels for subgraph titles
        file_roles = {}
        for f in profile.get("multi_file", {}).get("files", []):
            role = f.get("role", "")
            label = {"ui": "UI", "data": "Data", "primary": "Primary",
                     "secondary": "Secondary"}.get(role, "")
            file_roles[f["name"]] = label

        for src_file, tnames in sorted(tables_by_source.items()):
            safe_src = _mermaid_safe(src_file)
            role_label = file_roles.get(src_file, "")
            title = f"{src_file} ({role_label})" if role_label else src_file
            lines.append(f'    subgraph {safe_src}["{title}"]')
            for tname in sorted(tnames):
                safe_name = _mermaid_safe(tname)
                fc = dm["tables"][tname]["field_count"]
                lines.append(f'        {safe_name}["{tname}<br/>{fc} fields"]')
            lines.append("    end")

        # Add edges — dashed for cross-file, solid for same-file
        for edge in dm.get("base_table_edges", []):
            left_safe = _mermaid_safe(edge["left"])
            right_safe = _mermaid_safe(edge["right"])
            if edge.get("cross_file"):
                lines.append(f'    {left_safe} -.- {right_safe}')
            else:
                lines.append(f'    {left_safe} --- {right_safe}')

        # Style the subgraphs
        for i, src_file in enumerate(sorted(tables_by_source.keys())):
            safe_src = _mermaid_safe(src_file)
            if src_file == sol:
                lines.append(f'    style {safe_src} fill:#1e3a5f,stroke:#6c8cff,color:#e1e4ed')
            else:
                lines.append(f'    style {safe_src} fill:#3d2a0f,stroke:#fb923c,color:#e1e4ed')

        lines.append("```")
        lines.append("")
    else:
        # Single-file or no external tables: use erDiagram
        lines.append("```mermaid")
        lines.append("erDiagram")
        for tname, t in dm["tables"].items():
            if t.get("is_external"):
                continue  # skip correlated tables in single-file mode
            field_count = t["field_count"]
            lines.append(f'    {_mermaid_safe(tname)} {{')
            lines.append(f'        int fields "{field_count} fields"')
            lines.append(f'    }}')

        for edge in dm.get("base_table_edges", []):
            left_safe = _mermaid_safe(edge["left"])
            right_safe = _mermaid_safe(edge["right"])
            lines.append(f'    {left_safe} ||--o{{ {right_safe} : ""')

        lines.append("```")
        lines.append("")

    # --- Naming Conventions ---
    conv = profile["naming_conventions"]
    lines.append("## Naming Conventions")
    lines.append("")
    lines.append(f"- **Dominant case style:** {conv['dominant_case']}")
    lines.append("")
    if conv["prefix_conventions"]:
        lines.append("| Prefix | Convention | Count |")
        lines.append("|--------|-----------|-------|")
        for prefix, count in conv["prefix_conventions"].items():
            lines.append(f"| `{prefix}` | {count} |")
        lines.append("")

    # --- Business Logic ---
    bl = profile["business_logic"]
    lines.append("## Business Logic")
    lines.append("")
    lines.append(f"- **Total scripts:** {bl['total_scripts']}")
    lines.append(f"- **Scripts analyzed:** {bl['total_files_analyzed']}")
    lines.append(f"- **Call graph edges:** {bl['call_graph_edges']}")
    lines.append(f"- **Total lines:** {bl['line_counts']['total']}")
    lines.append(f"- **Avg lines/script:** {bl['line_counts']['avg']}")
    lines.append(f"- **Max lines:** {bl['line_counts']['max']}")
    lines.append("")

    # Script folders
    lines.append("### Script Folders")
    lines.append("")
    lines.append("| Folder | Scripts |")
    lines.append("|--------|---------|")
    for folder, info in sorted(bl["folders"].items()):
        lines.append(f"| {folder} | {info['count']} |")
    lines.append("")

    # Entry points
    if bl["entry_points"]:
        lines.append("### Entry Point Scripts")
        lines.append("")
        lines.append("Scripts not called by any other script (likely triggered by UI):")
        lines.append("")
        for name in bl["entry_points"][:20]:
            lines.append(f"- {name}")
        lines.append("")

    # Utility scripts
    if bl["utility_scripts"]:
        lines.append("### Utility Scripts")
        lines.append("")
        lines.append("Scripts called by 3+ other scripts:")
        lines.append("")
        for name in bl["utility_scripts"][:20]:
            lines.append(f"- {name}")
        lines.append("")

    # Clusters
    if bl["clusters"]:
        lines.append("### Functional Clusters")
        lines.append("")
        for cluster in bl["clusters"]:
            if "_cycles_detected" in cluster:
                lines.append(f"- **Cycles detected:** {cluster['_cycles_detected']}")
                continue
            lines.append(
                f"- **{cluster['name']}** — {cluster['script_count']} scripts"
            )
            if cluster.get("entry_points"):
                lines.append(
                    f"  - Entry points: {', '.join(cluster['entry_points'])}"
                )
            if cluster.get("bottleneck"):
                lines.append(f"  - Bottleneck: {cluster['bottleneck']}")
        lines.append("")

    # Largest scripts
    if bl["line_counts"]["largest_scripts"]:
        lines.append("### Largest Scripts")
        lines.append("")
        lines.append("| Script | Lines |")
        lines.append("|--------|-------|")
        for name, count in bl["line_counts"]["largest_scripts"]:
            lines.append(f"| {name} | {count} |")
        lines.append("")

    # Deep metrics
    if "deep_metrics" in bl:
        dm_deep = bl["deep_metrics"]
        lines.append("### Deep Analysis Metrics")
        lines.append("")
        eh = dm_deep["error_handling"]
        lines.append(f"- **Error handling coverage:** {eh['coverage_pct']}% "
                      f"({eh['with_capture']}/{eh['with_capture'] + eh['without_capture']})")
        lines.append(f"- **Scripts using transactions:** {dm_deep['transactions']['scripts_using']}")
        lines.append(f"- **Max nesting depth:** {dm_deep['nesting']['max_depth']}")
        lines.append(f"- **Avg nesting depth:** {dm_deep['nesting']['avg_depth']}")
        lines.append("")

        if dm_deep["external_calls"]:
            lines.append("#### External Calls")
            lines.append("")
            for call_type, count in dm_deep["external_calls"].items():
                lines.append(f"- {call_type}: {count}")
            lines.append("")

        if dm_deep["step_frequency"]:
            lines.append("#### Most Used Steps")
            lines.append("")
            lines.append("| Step | Count |")
            lines.append("|------|-------|")
            for step, count in dm_deep["step_frequency"].items():
                lines.append(f"| {step} | {count} |")
            lines.append("")

    # --- Custom Functions ---
    cf = profile["custom_functions"]
    lines.append("## Custom Functions")
    lines.append("")
    lines.append(f"- **Total:** {cf['total']}")
    if "categories" in cf:
        cats = cf["categories"]
        lines.append(f"- **Constants:** {cats.get('constant', 0)}")
        lines.append(f"- **Functional:** {cats.get('functional', 0)}")
        lines.append(f"- **Solution-specific:** {cats.get('solution_specific', 0)}")
        lines.append(f"- **Utility:** {cats.get('utility', 0)}")
    lines.append("")

    if cf.get("dependency_chains"):
        lines.append("### Dependency Chains")
        lines.append("")
        for chain in cf["dependency_chains"]:
            lines.append(f"- {chain['function']} (depth: {chain['depth']})")
        lines.append("")

    # --- UI Layer ---
    ui = profile["ui_layer"]
    lines.append("## UI Layer")
    lines.append("")
    lines.append(f"- **Total layouts:** {ui['total']}")
    lines.append(f"- **Orphaned layouts:** {len(ui['orphaned_layouts'])}")
    lines.append(f"- **Layout summaries available:** {'Yes' if ui['has_layout_summaries'] else 'No'}")
    lines.append("")

    if ui["classifications"]:
        lines.append("### Layout Classification")
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("|------|-------|")
        for cat, count in sorted(ui["classifications"].items()):
            lines.append(f"| {cat} | {count} |")
        lines.append("")

    if ui["by_base_to"]:
        lines.append("### Layouts by Base Table")
        lines.append("")
        lines.append("| Base TO | Layouts |")
        lines.append("|---------|---------|")
        for to_name, count in sorted(ui["by_base_to"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {to_name} | {count} |")
        lines.append("")

    if ui["portals"]:
        lines.append("### Portal Usage")
        lines.append("")
        for portal in ui["portals"]:
            lines.append(f"- Layout **{portal['layout']}** embeds portal to **{portal['table']}**")
        lines.append("")

    if ui["orphaned_layouts"]:
        lines.append("### Orphaned Layouts")
        lines.append("")
        lines.append("Layouts not referenced by any script `Go to Layout` step:")
        lines.append("")
        for name in ui["orphaned_layouts"][:20]:
            lines.append(f"- {name}")
        lines.append("")

    # --- Integrations ---
    integ = profile["integrations"]
    lines.append("## Integration Points")
    lines.append("")

    if integ["external_data_sources"]:
        lines.append("### External Data Sources")
        lines.append("")
        for src in integ["external_data_sources"]:
            lines.append(f"- {src}")
        lines.append("")

    lines.append("### Value Lists")
    lines.append("")
    lines.append(f"- **Total:** {integ['value_lists']['total']}")
    for src_type, count in integ["value_lists"]["by_source"].items():
        lines.append(f"- **{src_type}:** {count}")
    lines.append("")

    if integ["external_calls"]:
        lines.append("### External Script Calls")
        lines.append("")
        for call_type, info in integ["external_calls"].items():
            lines.append(f"- **{call_type}:** {info['count']} occurrence(s)")
            for s in info["scripts"][:5]:
                lines.append(f"  - {s}")
        lines.append("")

    # --- Multi-file ---
    mf = profile["multi_file"]
    if mf.get("is_multi_file"):
        arch = mf.get("file_architecture", "multi_file")
        arch_labels = {
            "data_separation": "Data Separation Model (UI + Data)",
            "multi_file": "Multi-File",
            "single": "Single File",
        }
        lines.append("## Multi-File Architecture")
        lines.append("")
        lines.append(f"**Pattern:** {arch_labels.get(arch, arch)}")
        lines.append("")

        # Per-file summary table
        if mf.get("files"):
            lines.append("| File | Role | Local Tables | TOs (Local / External) |")
            lines.append("|------|------|-------------|------------------------|")
            for f in mf["files"]:
                role = f.get("role", "").capitalize()
                ltc = f.get("local_table_count", 0)
                loc_to = f.get("local_to_count", 0)
                ext_to = f.get("external_to_count", 0)
                lines.append(f"| {f['name']} | {role} | {ltc} | {loc_to} / {ext_to} |")
            lines.append("")

        # Table ownership
        dm_section = profile.get("data_model", {})
        local_t = dm_section.get("local_tables", [])
        ext_t = dm_section.get("external_tables", {})
        if local_t:
            lines.append(f"**{sol} (local):** {', '.join(local_t)}")
            lines.append("")
        for ds_name, tables_list in sorted(ext_t.items()):
            corr_name = mf.get("data_source_map", {}).get(ds_name, ds_name)
            lines.append(f"**{corr_name} (via {ds_name}):** {', '.join(tables_list)}")
            lines.append("")

        # Data sources
        if mf.get("data_sources"):
            lines.append("### External Data Sources")
            lines.append("")
            lines.append("| Name | Type | Path |")
            lines.append("|------|------|------|")
            for ds in mf["data_sources"]:
                lines.append(f"| {ds['name']} | {ds['type']} | `{ds['path']}` |")
            lines.append("")

    # --- Health ---
    health = profile["health"]
    lines.append("## Health Metrics")
    lines.append("")

    if not health.get("xref_available"):
        lines.append(f"*{health.get('note', 'xref.index not available')}*")
        lines.append("")
    else:
        lines.append(f"- **Total cross-references:** {health.get('total_xref_entries', 0)}")
        lines.append("")

        df = health.get("dead_fields", {})
        ds = health.get("dead_scripts", {})
        lines.append(f"- **Dead fields:** {df.get('count', 0)}")
        lines.append(f"- **Dead scripts:** {ds.get('count', 0)}")
        lines.append(f"- **Disconnected tables:** {len(health.get('disconnected_tables', []))}")
        lines.append(f"- **Empty scripts:** {len(health.get('empty_scripts', []))}")
        lines.append("")

        if health.get("disconnected_tables"):
            lines.append("### Disconnected Tables")
            lines.append("")
            for t in health["disconnected_tables"]:
                lines.append(f"- {t}")
            lines.append("")

        if health.get("empty_scripts"):
            lines.append("### Empty Scripts")
            lines.append("")
            for s in health["empty_scripts"]:
                lines.append(f"- {s}")
            lines.append("")

    lines.append("---")
    lines.append(f"*Generated by analyze.py | {profile['generated_at']}*")

    return "\n".join(lines)


def _mermaid_safe(name):
    """Make a name safe for Mermaid diagrams."""
    # Replace spaces and special chars with underscores
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_extensions():
    """Print available extensions and exit."""
    for name, info in EXTENSIONS.items():
        try:
            mod = __import__(name)
            version = getattr(mod, "__version__", "unknown")
            status = f"installed (v{version})"
        except ImportError:
            status = "not installed"

        pad = "." * (16 - len(name))
        desc = info["description"]
        print(f"  {name} {pad} {status:30s} -> {desc}")


def main():
    parser = argparse.ArgumentParser(
        description="Solution-level analysis for FileMaker solutions."
    )
    parser.add_argument(
        "-s", "--solution",
        help="Solution name (as it appears in agent/context/)",
    )
    parser.add_argument(
        "--format", choices=["json", "markdown", "html", "all"], default="all",
        help="Output format: json, markdown, html, or all (default: all)",
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="Enable full script text analysis",
    )
    parser.add_argument(
        "--ensure-prerequisites", action="store_true",
        help="Build xref.index and layout summaries if missing",
    )
    parser.add_argument(
        "--list-extensions", action="store_true",
        help="Show available optional dependencies and exit",
    )
    parser.add_argument(
        "--status-json", action="store_true",
        help="Emit structured JSONL status to stderr (for agent consumption)",
    )
    parser.add_argument(
        "--correlated", nargs="*", default=None,
        help="Correlated solution names for multi-file analysis. "
             "Pass with no args to auto-detect, or name solutions explicitly.",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path override (single-format modes only)",
    )

    args = parser.parse_args()

    # Enable structured status output
    global _STATUS_JSON
    if args.status_json:
        _STATUS_JSON = True

    if args.list_extensions:
        print("Optional extensions for analyze.py:")
        print()
        list_extensions()
        return

    if not args.solution:
        # Try to auto-detect if only one solution exists
        solutions = [
            d.name for d in CONTEXT_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        if len(solutions) == 1:
            args.solution = solutions[0]
            print(f"Auto-detected solution: {args.solution}")
        else:
            parser.error(
                "Please specify a solution with -s. "
                f"Available: {', '.join(sorted(solutions))}"
            )

    solution_dir = CONTEXT_DIR / args.solution

    # Ensure prerequisites if requested
    if args.ensure_prerequisites:
        print("Checking prerequisites...")
        built = ensure_prerequisites(args.solution, solution_dir)
        if built:
            print(f"  Built: {', '.join(built)}")
        else:
            print("  All prerequisites present.")

    # Build profile
    # --correlated with no args (empty list) means auto-detect (same as None)
    correlated = args.correlated if args.correlated else None
    profile = build_profile(args.solution, deep=args.deep,
                            correlated_solutions=correlated)

    # Determine output path — deliverables go to sandbox
    sandbox_dir = PROJECT_ROOT / "agent" / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    # Determine which formats to write
    if args.output:
        # Single output path override — use the requested format (or json)
        formats_to_write = [args.format if args.format != "all" else "json"]
    elif args.format == "all":
        formats_to_write = ["json", "markdown", "html"]
    else:
        formats_to_write = [args.format]
        # Always include JSON alongside markdown/html
        if args.format in ("markdown", "html") and "json" not in formats_to_write:
            formats_to_write.append("json")

    base_name = f"{args.solution} - solution-profile"

    for fmt in formats_to_write:
        if args.output and fmt == formats_to_write[0]:
            output_path = Path(args.output)
        elif fmt == "markdown":
            output_path = sandbox_dir / f"{base_name}.md"
        elif fmt == "html":
            output_path = sandbox_dir / f"{base_name}.html"
        else:
            output_path = sandbox_dir / f"{base_name}.json"

        if fmt == "markdown":
            content = format_markdown(profile)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  Markdown: {output_path}")
        elif fmt == "html":
            content = format_html(profile)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  HTML: {output_path}")
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
            print(f"  JSON: {output_path}")


if __name__ == "__main__":
    main()
