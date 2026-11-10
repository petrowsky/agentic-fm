#!/usr/bin/env python3
"""test_replace_field_contents_hr.py — Replace Field Contents reads and prints
`Skip auto-enter options` the way FileMaker draws it.

FileMaker draws the bare token `Skip auto-enter options` last on the line exactly
when <Restore state="True"/> and <SerialNumbers PerformAutoEnter="False"/>, in
every With mode. The catalog models it as a flagStyle projection of the
SerialNumbers group's PerformAutoEnter, gated on Restore, so XML -> HR prints the
token when FileMaker does and HR -> XML of FileMaker's line writes
PerformAutoEnter False and derives Restore True from it.

The cases in ``agent/fixtures/converter/replace-field-contents-fm-lines.json``
were pasted into FileMaker Pro 26.0.3 and read back: the stored step and the
line FileMaker displays. Mirrors the webviewer
``replace-field-contents-hr.test.ts``. Runs under pytest, or standalone.
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from catalog_emit import EmptyResolver, convert_step_with_catalog, match_param_values
from catalog_grammar import load_catalog
from snippet_to_hr import snippet_to_hr

from agent.fmlint.formats.hr_parser import parse_hr

_CATALOG = {
    e.name: e
    for e in load_catalog(os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
}
with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "replace-field-contents-fm-lines.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]

TOKEN = "Skip auto-enter options"


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def _step(xml: str) -> ET.Element:
    root = ET.fromstring(xml if xml.lstrip().startswith("<Step") else xml.split("?>", 1)[-1])
    return root if root.tag == "Step" else root.find("Step")


def _state(s: ET.Element) -> dict[str, str]:
    """The stored state FileMaker's line shows: the dialog, the With mode and
    whether auto-enter is skipped (Restore on and PerformAutoEnter off)."""
    sn = s.find("SerialNumbers")
    return {
        "NoInteract": s.find("NoInteract").get("state"),
        "With": s.find("With").get("value"),
        "skip": str(s.find("Restore").get("state") == "True"
                    and sn is not None and sn.get("PerformAutoEnter") == "False"),
    }


def test_xml_to_hr_prints_filemakers_line():
    for c in CASES:
        (line,) = snippet_to_hr(f'<fmxmlsnippet type="FMObjectList">{c["xml"]}</fmxmlsnippet>')
        assert line == c["fmLine"], (c["id"], line)


def test_filemakers_line_converts_back_to_the_state_it_shows():
    for c in CASES:
        got = _state(_step(_emit(c["fmLine"])))
        assert got == _state(_step(c["xml"])), (c["id"], c["fmLine"], got)


def test_the_token_restores_both_stored_values():
    xml = _emit("Replace Field Contents [ With dialog: Off ; Invoices::Description ; Current contents ; " + TOKEN + " ]")
    s = _step(xml)
    assert s.find("Restore").get("state") == "True"
    assert s.find("SerialNumbers").get("PerformAutoEnter") == "False"
    assert s.find("With").get("value") == "CurrentContents"
    # Without the token nothing is skipped and Restore stays at its default.
    s = _step(_emit("Replace Field Contents [ With dialog: Off ; Invoices::Description ; Current contents ]"))
    assert s.find("Restore").get("state") == "False"


def test_with_none_never_reads_the_token_as_a_calculation():
    s = _step(_emit("Replace Field Contents [ With dialog: Off ; Invoices::Description ; " + TOKEN + " ]"))
    assert s.find("With").get("value") == "None"
    assert TOKEN not in (s.findtext("Calculation") or "")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
