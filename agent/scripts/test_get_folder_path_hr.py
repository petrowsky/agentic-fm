#!/usr/bin/env python3
"""test_get_folder_path_hr.py — Get Folder Path read from the line FileMaker
draws for it.

FileMaker draws the target variable first with the repetition as its subscript
(``$result[2]``, ``$result[$i]``; nothing for 1 or empty), then Dialog title and
Default location with no labels, leaving an empty one out:
``Get Folder Path [ Allow Folder Creation ; $result ; "t" ; Get ( DocumentsPath ) ]``.
The converter's HR labels the three calculations and keeps the variable last.
The catalog marks Dialog title and Default location ``fmLineBare`` and the
repetition ``fmLineSubscriptOf: Name``, so HR -> XML of FileMaker's line keeps
every calculation instead of dropping both and writing ``$result[2]`` as the name.

The cases in ``agent/fixtures/converter/get-folder-path-fm-lines.json`` were
pasted into FileMaker Pro 26.0.3 and read back. FileMaker draws the same line
for a lone Dialog title and a lone Default location (``guess``): it reads as
Dialog title. Mirrors the webviewer ``get-folder-path-hr.test.ts``. Runs under
pytest, or standalone.
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
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "get-folder-path-fm-lines.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def _step(xml: str) -> ET.Element:
    root = ET.fromstring(xml if xml.lstrip().startswith("<Step") else xml.split("?>", 1)[-1])
    return root if root.tag == "Step" else root.find("Step")


def _stored(s: ET.Element, drawn: bool = False) -> dict[str, str]:
    """Name, repetition and both calculations. ``drawn``: as FileMaker's line
    shows them, where repetition 1 and an empty one look the same."""
    def calc(w: str) -> str:
        return (s.findtext(w + "/Calculation") or "").strip()

    rep = calc("Repetition")
    return {
        "Name": s.findtext("Name") or "",
        "Repetition": "" if drawn and rep == "1" else rep,
        "DialogTitle": calc("DialogTitle"),
        "DefaultLocation": calc("DefaultLocation"),
    }


def test_filemakers_line_keeps_every_calculation():
    for c in CASES:
        if c["kind"] != "exact":
            continue
        got = _stored(_step(_emit(c["fmLine"])))
        assert got == _stored(_step(c["xml"]), drawn=True), (c["id"], c["fmLine"], got)


def test_a_lone_unlabeled_calculation_reads_as_the_dialog_title():
    for c in CASES:
        if c["kind"] != "guess":
            continue
        want = _stored(_step(c["xml"]), drawn=True)
        want["DialogTitle"] = want["DialogTitle"] or want["DefaultLocation"]
        want["DefaultLocation"] = ""
        got = _stored(_step(_emit(c["fmLine"])))
        assert got == want, (c["id"], c["fmLine"], got)


def test_an_empty_slot_or_the_label_places_the_default_location():
    for hr in (
        "Get Folder Path [ Allow Folder Creation ; $result[2] ; ; Get ( DocumentsPath ) ]",
        "Get Folder Path [ Allow Folder Creation ; $result[2] ; Default location: Get ( DocumentsPath ) ]",
    ):
        got = _stored(_step(_emit(hr)))
        assert got == {
            "Name": "$result",
            "Repetition": "2",
            "DialogTitle": "",
            "DefaultLocation": "Get ( DocumentsPath )",
        }, (hr, got)


def test_converter_hr_round_trips_every_step():
    for c in CASES:
        (line,) = snippet_to_hr(f'<fmxmlsnippet type="FMObjectList">{c["xml"]}</fmxmlsnippet>')
        got = _stored(_step(_emit(line)))
        assert got == _stored(_step(c["xml"])), (c["id"], line, got)


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
