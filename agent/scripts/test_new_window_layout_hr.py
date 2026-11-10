#!/usr/bin/env python3
"""test_new_window_layout_hr.py — New Window's layout choice through every converter.

Each case in ``agent/fixtures/converter/new-window-layout-fm.json`` is one step
pasted into FileMaker Pro 26.0.3, copied back (``fm``), drawn in the Script
Workspace (``fmLine``) and exported with Save a Copy as XML (``saxml``).

FileMaker draws every layout choice after ``Using layout:``, and the catalog
prints that label. The layout is discriminated by LayoutDestination like Go to
Related Record's: a named layout, none (``<unknown>``), the current layout and
both by-calculation forms survive XML -> HR -> XML. The Save as XML reading
gives FileMaker's copy for every case, including the export's value 1 (labelled
``original layout``) on a step that has no OriginalLayout, which is
CurrentLayout. Runs under pytest, or standalone.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from catalog_emit import EmptyResolver, convert_step_with_catalog, match_param_values  # noqa: E402
from catalog_grammar import load_catalog  # noqa: E402
from saxml_read import read_saxml_step  # noqa: E402
from snippet_to_hr import snippet_to_hr  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "new-window-layout-fm.json"),
    encoding="utf-8",
) as fh:
    _CASES = {c["id"]: c for c in json.load(fh)["cases"]}
_ENTRIES = {e.name: e for e in load_catalog(
    os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))}

# FileMaker draws a layout-name calculation that is a quoted literal exactly as a
# named layout, so the HR reads it back as the named layout (the same layout opens).
_LITERAL_NAME_CALC = {"NAMECALC-LIT"}


def _layout(step_xml: str) -> tuple[str, str]:
    """(LayoutDestination, what the step says about the layout), ids and FileMaker's
    equivalent spellings of an empty layout normalised away."""
    dest_m = re.search(r'<LayoutDestination value="([^"]*)"', step_xml)
    dest = dest_m.group(1) if dest_m else ""
    calc = re.search(r"<Layout[^>]*>\s*<Calculation>(.*?)</Calculation>", step_xml, re.S)
    if calc:
        text = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", calc.group(1).strip(), flags=re.S)
        return dest, "calc:" + text
    if dest == "CurrentLayout":
        return dest, ""
    name = re.search(r'<Layout\b[^>]*\bname="([^"]*)"', step_xml)
    return dest, name.group(1) if name else ""


def _hr_params(line: str) -> list[str]:
    inner = line[line.index("[") + 1:line.rindex("]")].strip()
    return [t.strip() for t in inner.split(" ; ")] if inner else []


def _new_window_cases():
    return [c for c in _CASES.values() if '"New Window"' in c["fm"]]


def test_prints_the_label_filemaker_draws():
    for case in _new_window_cases():
        hr = snippet_to_hr('<fmxmlsnippet type="FMObjectList">' + case["fm"] + "</fmxmlsnippet>")[0]
        if "Using layout:" not in case["fmLine"]:
            continue  # a step FileMaker itself draws without its layout
        assert "Using layout: " in hr and "; Layout: " not in hr, (case["id"], hr)


def test_layout_choice_survives_xml_to_hr_to_xml():
    entry = _ENTRIES["New Window"]
    for case in _new_window_cases():
        hr = snippet_to_hr('<fmxmlsnippet type="FMObjectList">' + case["fm"] + "</fmxmlsnippet>")[0]
        back = convert_step_with_catalog(
            entry, False, match_param_values(entry, _hr_params(hr)), EmptyResolver())
        want = _layout(case["fm"])
        if case["id"] in _LITERAL_NAME_CALC:
            want = ("SelectedLayout", want[1][len('calc:"'):-1])
        assert _layout(back) == want, (case["id"], hr, back)


def test_reads_the_line_filemaker_draws():
    """FileMaker's own line names the same layout choice, except where it cannot
    tell them apart: a quoted literal name calculation, a number calculation (both
    drawn bare) and a current layout drawn without its label."""
    entry = _ENTRIES["New Window"]
    ambiguous = _LITERAL_NAME_CALC | {"NUMCALC", "NUMCALC-OPTS", "NOTDEST-DASH"}
    for case in _new_window_cases():
        if case["id"] in ambiguous:
            continue
        back = convert_step_with_catalog(
            entry, False, match_param_values(entry, _hr_params(case["fmLine"])), EmptyResolver())
        assert _layout(back) == _layout(case["fm"]), (case["id"], case["fmLine"], back)


def test_save_as_xml_reads_as_filemakers_copy():
    for case in _CASES.values():
        step = ET.fromstring(case["saxml"])
        entry = _ENTRIES[step.get("name")]
        disabled, values, resolver = read_saxml_step(entry, step)
        got = convert_step_with_catalog(entry, disabled, values, resolver)
        assert _layout(got) == _layout(case["fm"]), (case["id"], got)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
