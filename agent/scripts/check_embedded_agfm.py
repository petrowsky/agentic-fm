#!/usr/bin/env python3
"""
check_embedded_agfm.py

Detect whether the agentic-fm scripts and the Context custom function that are
*embedded* in a host FileMaker solution have drifted from the canonical bundled
reference shipped with this repository.

Why this exists
---------------
The agentic-fm scripts (AGFMScriptBridge, AGFMEvaluation, Push Context, the
menu, etc.) and the Context custom function are *pasted* into each host
solution during setup. Bug fixes to those objects (see the AGFM_Bridge issues)
do NOT propagate automatically -- someone has to re-paste them. Over time an
embedded copy can silently fall behind the version bundled in `filemaker/`,
which leads to confusing failures. This tool surfaces that drift.

Sources
-------
Canonical (source of truth, versioned in this repo):
    filemaker/agentic-fm.xml   -- fmxmlsnippet containing every agentic-fm script
    filemaker/Context.fmfn     -- the Context custom function

Embedded (produced by "Explode XML" against a host solution):
    agent/xml_parsed/scripts/<ScriptName>.xml           -- SaXML, one file per script
    agent/xml_parsed/custom_functions_sanitized/*Context*  -- Context CF text

How it compares
---------------
Internal IDs, GUIDs and table/field references change when a script is pasted
into a different solution, so a raw text diff is useless. Instead we build a
*semantic signature* per object: the normalized calculation expressions plus
comment text, whitespace-collapsed. Two objects with the same signature share
the same logic regardless of which solution they live in.

SaXML embedded scripts are first normalized to fmxmlsnippet using the repo's
own converter (fm_xml_to_snippet.py) so both sides are compared in the same
format.

Usage
-----
    python3 agent/scripts/check_embedded_agfm.py [options]

Options:
    --repo-root PATH   Project root (default: two levels up from this file)
    --json             Emit machine-readable JSON instead of a table
    --advisory         Always exit 0 (report only; do not signal drift)
    -h, --help         Show this help

Exit codes:
    0  all embedded objects match, OR no agentic-fm objects were found
    1  at least one object is STALE or MISSING (unless --advisory)
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Signature extraction
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _norm(text):
    """Collapse all whitespace so reformatting does not look like a logic change."""
    return _WS_RE.sub(" ", (text or "")).strip()


def _signature_from_steps(steps):
    """Build a semantic signature from an iterable of <Step> elements.

    The signature captures, in document order:
      * each step's name attribute (structural shape of the script)
      * every <Calculation> CDATA payload (the actual logic)
      * every comment <Text> payload (doc-block / inline comments)

    Volatile data (ids, GUIDs, field/TO references rendered as <Field>) is
    deliberately excluded, so the signature is stable across solutions.
    """
    parts = []
    for step in steps:
        parts.append("S:" + _norm(step.get("name", "")))
        for calc in step.iter("Calculation"):
            if calc.text and calc.text.strip():
                parts.append("C:" + _norm(calc.text))
        for txt in step.iter("Text"):
            if txt.text and txt.text.strip():
                parts.append("T:" + _norm(txt.text))
    blob = "\x1f".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _canonical_signatures(reference_path):
    """Return {script_name: signature} for the bundled fmxmlsnippet reference.

    The reference lists some scripts multiple times (0-step entries are just
    Perform-Script references); we keep, per name, the definition with the most
    steps.
    """
    root = ET.parse(reference_path).getroot()
    best = {}  # name -> (step_count, signature)
    for script in root.findall(".//Script"):
        name = script.get("name")
        if not name:
            continue
        steps = script.findall(".//Step")
        if not steps:
            continue
        if name not in best or len(steps) > best[name][0]:
            best[name] = (len(steps), _signature_from_steps(steps))
    return {name: sig for name, (_, sig) in best.items()}


def _steps_from_embedded(path, converter):
    """Parse an embedded per-script file into <Step> elements.

    Accepts either fmxmlsnippet (used directly) or SaXML (converted first via
    the repo's fm_xml_to_snippet.py). Returns a list of Step elements, or None
    if the file cannot be understood.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None

    if root.tag == "fmxmlsnippet":
        return root.findall(".//Step")

    # Assume SaXML -> convert to fmxmlsnippet with the bundled converter.
    try:
        out = subprocess.run(
            [sys.executable, str(converter), str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        conv = ET.fromstring(out.stdout)
    except ET.ParseError:
        return None
    return conv.findall(".//Step")


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _key(name):
    """Normalize a script name for filename matching (case/punctuation-insensitive)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _index_embedded_scripts(scripts_dir):
    """Return {normalized_name: Path} for every *.xml in the scripts dir."""
    index = {}
    if not scripts_dir.is_dir():
        return index
    for p in sorted(scripts_dir.glob("*.xml")):
        index[_key(p.stem)] = p
    return index


# ---------------------------------------------------------------------------
# Context custom function
# ---------------------------------------------------------------------------

def _context_signature(text):
    """Signature of the Context custom function calculation text."""
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _embedded_context_text(xml_parsed):
    """Locate the embedded Context CF text, trying sanitized then calc XML."""
    san = xml_parsed / "custom_functions_sanitized"
    if san.is_dir():
        for p in san.glob("*"):
            if "context" in p.stem.lower():
                try:
                    return p.read_text(encoding="utf-8")
                except OSError:
                    pass
    calcs = xml_parsed / "custom_function_calcs"
    if calcs.is_dir():
        for p in calcs.glob("*.xml"):
            if "context" in p.stem.lower():
                try:
                    root = ET.parse(p).getroot()
                except ET.ParseError:
                    continue
                # Grab the largest text payload in the file.
                texts = [e.text for e in root.iter() if e.text and e.text.strip()]
                if texts:
                    return max(texts, key=len)
    return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def check(repo_root):
    """Compare embedded objects against the canonical reference.

    Returns a dict: {"objects": [ {name, kind, status}... ], "checked": bool}
    where status is one of OK / STALE / MISSING / UNREADABLE.
    """
    reference = repo_root / "filemaker" / "agentic-fm.xml"
    context_fmfn = repo_root / "filemaker" / "Context.fmfn"
    xml_parsed = repo_root / "agent" / "xml_parsed"
    converter = repo_root / "agent" / "scripts" / "fm_xml_to_snippet.py"
    scripts_dir = xml_parsed / "scripts"

    results = []

    # --- scripts -----------------------------------------------------------
    canonical = _canonical_signatures(reference)
    embedded_index = _index_embedded_scripts(scripts_dir)

    for name in sorted(canonical):
        embedded_path = embedded_index.get(_key(name))
        if embedded_path is None:
            status = "MISSING"
        else:
            steps = _steps_from_embedded(embedded_path, converter)
            if steps is None:
                status = "UNREADABLE"
            else:
                status = "OK" if _signature_from_steps(steps) == canonical[name] else "STALE"
        results.append({"name": name, "kind": "script", "status": status})

    # --- Context custom function ------------------------------------------
    if context_fmfn.exists():
        canon_ctx = _context_signature(context_fmfn.read_text(encoding="utf-8"))
        emb_ctx = _embedded_context_text(xml_parsed)
        if emb_ctx is None:
            status = "MISSING"
        else:
            status = "OK" if _context_signature(emb_ctx) == canon_ctx else "STALE"
        results.append({"name": "Context", "kind": "custom_function", "status": status})

    # "checked" is False when nothing embedded was found at all -> nothing to do.
    any_embedded = bool(embedded_index) or _embedded_context_text(xml_parsed) is not None
    return {"objects": results, "checked": any_embedded}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_table(report):
    objs = report["objects"]
    if not report["checked"]:
        print("No agentic-fm objects found in the exploded solution — nothing to check.")
        print("(Run 'Explode XML' on a solution that has agentic-fm installed to enable this check.)")
        return
    drift = [o for o in objs if o["status"] not in ("OK",)]
    width = max((len(o["name"]) for o in objs), default=4)
    print("Embedded agentic-fm code vs bundled reference:\n")
    for o in objs:
        mark = {"OK": "✓", "STALE": "✗", "MISSING": "•", "UNREADABLE": "?"}.get(o["status"], "?")
        print(f"  {mark} {o['name']:<{width}}  {o['status']:<10} ({o['kind']})")
    print()
    if drift:
        print(f"⚠  {len(drift)} object(s) out of date. Re-deploy the agentic-fm scripts /")
        print("   Context custom function into this solution from filemaker/ to resync.")
    else:
        print("All embedded agentic-fm objects match the bundled reference.")


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=True, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--advisory", action="store_true", help="always exit 0")
    args = parser.parse_args(argv)

    report = check(args.repo_root)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_table(report)

    if args.advisory or not report["checked"]:
        return 0
    drift = any(o["status"] not in ("OK",) for o in report["objects"])
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
