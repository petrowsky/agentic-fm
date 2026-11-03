#!/usr/bin/env python3
"""test_import_records_source_hr.py — Import Records keeps its source Profile.

Import Records holds its source format in the same ``<Profile>`` as Convert
File: a file source's FieldDelimiter / IsPredefined / FieldNameRow / DataType
(``table`` on a FileMaker source), a folder source's defaults, and an XML
source's XMLType / XSLType with one child per source (``<XMLFile>`` /
``<XMLHttp>`` / ``<XMLCalc>``, ``<XSLFile>`` / ``<XSLHttp>`` / ``<XSLCalc>``),
plus the step-level ``<Calculation>`` copy of an XML calculation (the catalog's
``mirrorOf`` param). ``<ImportOptions>`` carries KeepOriginalData and, when set,
AddRemainingRecords. The cases in
``agent/fixtures/converter/import-records-source-fm.json`` were pasted into
FileMaker Pro 26.0.3 and read back. FileMaker fills ``<TargetFields>`` with
every field of the target table, which the converter cannot resolve without the
file, so the comparison leaves the field mapping and the table id out. Mirrors
the webviewer ``import-records-source-hr.test.ts``. Runs under pytest, or
standalone.
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

from test_attrgroup_defaults_hr import _emit, _line  # noqa: E402
from test_convert_file_xml_source_hr import _canon  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "import-records-source-fm.json"),
    encoding="utf-8",
) as fh:
    _FIXTURE = json.load(fh)
# Cases marked restoreFromCompanions hold a Restore state the converter derives
# from the import options and table, not from the source.
CASES = [c for c in _FIXTURE["cases"] if not c.get("restoreFromCompanions")]


def _source(xml: str) -> tuple:
    """The step without its field mapping and with no table id."""
    xml = re.sub(r"<TargetFields>.*</TargetFields>|<TargetFields\s*/>", "", xml, flags=re.S)
    xml = re.sub(r'(<Table\b[^>]*?\bid=")[^"]*(")', r"\1\2", xml)
    return _canon(xml)


def test_fixture_holds_every_case():
    assert len(_FIXTURE["cases"]) == 25
    assert len(CASES) == 22
    assert sum("XMLType=" in c["fm"] for c in _FIXTURE["cases"]) == 15
    assert sum("<XMLCalc>" in c["fm"] for c in _FIXTURE["cases"]) == 2


def test_filemakers_copy_back_round_trips():
    for c in CASES:
        assert _source(_emit(_line(c["fm"]))) == _source(c["fm"]), c["id"]


def test_xml_source_prints_in_the_profile():
    for c in _FIXTURE["cases"]:
        line = _line(c["fm"])
        if "XMLType=" in c["fm"]:
            assert "Profile: " in line and "XMLType=" in line, (c["id"], line)


def test_xml_calculation_prints_once():
    for c in _FIXTURE["cases"]:
        calc = re.search(r"<XMLCalc><Calculation><!\[CDATA\[(.*?)\]\]>", c["fm"])
        if calc:
            line = _line(c["fm"])
            assert line.count("XMLCalc=" + calc.group(1)) == 1, (c["id"], line)
            assert line.count(calc.group(1)) == 1, (c["id"], line)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
