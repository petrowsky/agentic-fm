#!/usr/bin/env python3
"""test_replace_field_contents_calc_omit.py — Replace Field Contents writes a
<Calculation> only in the Calculation mode.

FileMaker stores no <Calculation> for With None, Current contents or Serial
numbers. Converting FileMaker's own line for those modes used to write an empty
<Calculation><![CDATA[]]></Calculation>, which FileMaker drops on paste. The
catalog marks the Calculation param ``omitWhenEmpty``. The lines below are the
ones FileMaker Pro 26.0.3 draws for each mode. Mirrors the webviewer
``replace-field-contents-calc-omit.test.ts``. Runs under pytest, or standalone.
"""

from __future__ import annotations

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

from agent.fmlint.formats.hr_parser import parse_hr

_CATALOG = {
    e.name: e
    for e in load_catalog(os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
}

CALC = 'Get ( CurrentDate ) & "x"'
CASES = [
    ("None", "Replace Field Contents [ With dialog: Off ; Invoices::Description ]"),
    ("CurrentContents", "Replace Field Contents [ With dialog: Off ; Invoices::Description ; Current contents ]"),
    ("SerialNumbers", "Replace Field Contents [ With dialog: Off ; Invoices::Description ; Serial numbers ]"),
    ("Calculation", f"Replace Field Contents [ With dialog: Off ; Invoices::Description ; {CALC} ]"),
]


def _emit(hr: str) -> ET.Element:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    xml = convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())
    root = ET.fromstring(xml if xml.lstrip().startswith("<Step") else xml.split("?>", 1)[-1])
    return root if root.tag == "Step" else root.find("Step")


def test_calculation_written_only_in_calculation_mode():
    for with_value, line in CASES:
        step = _emit(line)
        assert step.find("With").get("value") == with_value, line
        calcs = step.findall("Calculation")
        if with_value == "Calculation":
            assert [c.text for c in calcs] == [CALC], line
        else:
            assert calcs == [], line


if __name__ == "__main__":
    test_calculation_written_only_in_calculation_mode()
    print("ok")
