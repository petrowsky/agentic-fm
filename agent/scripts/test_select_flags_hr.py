#!/usr/bin/env python3
"""test_select_flags_hr.py — Copy, Cut, Clear, Check Selection, Paste and Execute
FileMaker Data API read and print their Select flag as FileMaker draws it.

FileMaker draws Select (and Paste's No style) as a bare flag only when on:
``Copy [ Select ; Invoices::Description ]``, ``Copy [ Invoices::Description ]``.
The catalog marks these ``flagStyle``, so the bare label reads back as on and its
absence as off; the same rule covers Go to Field's Select/perform and the Insert
steps' Select, whose default is on. Paste's Link if available is never drawn by
FileMaker, so its line converts with it off; our own line prints it bare when on.
The cases in ``agent/fixtures/converter/select-flags-fm-lines.json`` were pasted
into FileMaker Pro 26.0.3 and read back: the stored step and the line FileMaker
displays. Mirrors the webviewer ``select-flags-hr.test.ts``. Runs under pytest,
or standalone.
"""

from __future__ import annotations

import json
import os
import re
import sys

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
with open(os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "select-flags-fm-lines.json"), encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]

FLAGS = ("SelectAll", "NoStyle", "LinkAvail")
LINK = "Link if available ; "


def _state(xml: str, el: str) -> str | None:
    m = re.search(rf'<{el} state="(\w+)"', xml)
    return m.group(1) if m else None


def _field(xml: str) -> tuple[str, str] | None:
    """The field reference, or None for none at all. A step stored with no <Field>
    (``Copy [ Select ]``) emits an empty one; that absent-field round trip is a
    separate gap, so the cases compare the field only where FileMaker stored one."""
    m = re.search(r'<Field table="([^"]*)" id="\d+" name="([^"]*)"', xml)
    return (m.group(1), m.group(2)) if m else None


def _snippet(step_xml: str) -> str:
    return f'<fmxmlsnippet type="FMObjectList">{step_xml}</fmxmlsnippet>'


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def test_filemakers_line_converts_back_to_the_stored_step():
    for c in CASES:
        xml = _emit(c["fmLine"])
        for el in FLAGS:
            want = _state(c["xml"], el)
            if el == "LinkAvail" and want is not None:
                want = "False"  # FileMaker's line does not carry it
            assert _state(xml, el) == want, (c["fmLine"], el)
        if _field(c["xml"]):
            assert _field(xml) == _field(c["xml"]), c["fmLine"]


def test_xml_to_hr_prints_filemakers_line():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        assert (LINK in line) == (_state(c["xml"], "LinkAvail") == "True"), line
        assert line.replace(LINK, "") == c["fmLine"], (line, c["fmLine"])


def test_our_line_converts_back_to_the_stored_step():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        xml = _emit(line)
        for el in FLAGS:
            assert _state(xml, el) == _state(c["xml"], el), (line, el)
        if _field(c["xml"]):
            assert _field(xml) == _field(c["xml"]), line


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
