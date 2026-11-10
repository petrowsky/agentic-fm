#!/usr/bin/env python3
"""test_pdf_title_copy_hr.py — Create PDF's step-level calculation is the Title's copy.

FileMaker writes the Document Title of Create PDF and Save Records as PDF a
second time, as the step-level ``<Calculation>``: pasted with only the Title it
adds the copy, pasted with only the copy it adds the Title, and with both set to
different values it keeps the Title in both. The catalog marks that param
``hrHidden`` + ``mirrorOf``: XML -> HR prints the value once, as the Title (read
from the copy when the Title is absent), and HR -> XML writes both from it. The
cases in ``agent/fixtures/converter/pdf-title-copy-fm.json`` were pasted into
FileMaker Pro 26.0.3 and read back. Mirrors the webviewer
``pdf-title-copy-hr.test.ts``. Runs under pytest, or standalone.
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

from test_attrgroup_defaults_hr import _canon, _emit, _line  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "pdf-title-copy-fm.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]


def _title_facet(xml: str) -> tuple[list[str], list[str]]:
    """The step-level calculation(s) and the Title(s) a step holds."""
    top = re.sub(r"<(CreatePDFFile|PDFOptions)\b.*?</\1>", "", xml, flags=re.S)
    calc = r"<Calculation><!\[CDATA\[(.*?)\]\]></Calculation>"
    return (re.findall(calc, top, re.S),
            re.findall(r"<Title>\s*" + calc, xml, re.S))


def _converts_to_filemakers_step(case: dict, xml: str) -> None:
    out = _emit(_line(xml))
    if case.get("titleFacetOnly"):
        assert _title_facet(out) == _title_facet(case["fm"]), (case["id"], out)
    else:
        assert _canon(out) == _canon(case["fm"]), (case["id"], out)


def test_value_prints_once_as_the_title():
    for c in CASES:
        for xml in (c["pasted"], c["fm"]):
            line = _line(xml)
            assert "Output File" not in line and "Saved label" not in line, (c["id"], line)
            calcs, titles = _title_facet(c["fm"])
            if titles:
                assert line.count(titles[0]) == 1, (c["id"], line)


def test_pasted_step_converts_to_filemakers_copy_back():
    for c in CASES:
        _converts_to_filemakers_step(c, c["pasted"])


def test_filemakers_copy_back_round_trips():
    for c in CASES:
        _converts_to_filemakers_step(c, c["fm"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
