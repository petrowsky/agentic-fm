#!/usr/bin/env python3
"""test_import_records_field_options_hr.py — Import Records keeps each field's
import options and, with Restore off, its stored import order.

FileMaker keeps a ``FieldOptions`` number on every mapped ``<Field>`` and never
draws it; the HR writes it after the map, ``Invoices::Date=Import:2``, when it
is not 0 (the catalog's ``hrEntrySuffix``). The cases in
``agent/fixtures/converter/import-records-field-options-fm.json`` were pasted
into FileMaker Pro 26.0.3 and copied back; XML -> HR -> XML of FileMaker's copy
keeps the mapping (field ids and tables aside: unresolved without the file).
Mirrors the webviewer ``import-records-field-options-hr.test.ts``. Runs under
pytest, or standalone.
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
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "import-records-field-options-fm.json"),
    encoding="utf-8",
) as fh:
    _FIXTURE = json.load(fh)
# ROFF-BARE holds no import order: written back, the step carries the default
# import options and an empty table, which FileMaker keeps (the converter writes
# them for every Import Records).
CASES = [c for c in _FIXTURE["cases"] if c["id"] != "ROFF-BARE"]


def _mapping(xml: str) -> tuple:
    """The step with no table id and no field id or table: unresolved without the file."""
    xml = re.sub(r'(<Table\b[^>]*?\bid=")[^"]*(")', r"\1\2", xml)
    xml = re.sub(r'(<Field\b[^>]*?) (?:id|table)="[^"]*"', r"\1", xml)
    xml = re.sub(r'(<Field\b[^>]*?) (?:id|table)="[^"]*"', r"\1", xml)
    return _canon(xml)


def test_fixture_holds_every_case():
    assert len(_FIXTURE["cases"]) == 15
    assert len(CASES) == 14


def test_filemakers_copy_back_round_trips():
    for c in CASES:
        assert _mapping(_emit(_line(c["fm"]))) == _mapping(c["fm"]), c["id"]


def test_restore_off_keeps_the_stored_order():
    # FileMaker keeps the table, options and mapping with Restore off and draws
    # none of them; the HR shows what is not the default and says the order is off.
    by = {c["id"]: c for c in _FIXTURE["cases"]}
    line = _line(by["ROFF-FULL"]["fm"])
    assert line.startswith("Import Records [ With dialog: Off ; Specify import order: Off ; Table: Invoices ;"), line
    assert "; Update ; Windows ANSI ;" in line and "AutoEnter=False" in line, line
    assert "Name=Match, Status=Import:2, Date=DoNotImport:1" in line, line
    line = _line(by["ROFF-STD"]["fm"])
    assert "Specify import order: Off ; Table: Invoices" in line and "; Add ;" not in line, line
    assert "Specify import order" not in _line(by["ROFF-BARE"]["fm"])
    assert "Specify import order" not in _line(by["RON-FULL"]["fm"])
    xml = _emit("Import Records [ Specify import order: Off ; Table: T ; Import fields: T::A ]")
    assert '<Restore state="False"' in xml and 'name="T"' in xml, xml
    xml = _emit("Import Records [ Specify import order: On ]")
    assert '<Restore state="True"' in xml, xml


def test_field_options_print_after_the_map():
    by = {c["id"]: c for c in _FIXTURE["cases"]}
    line = _line(by["FO-3"]["fm"])
    assert "Name=Import:1, Date=Import:3, PrimaryKey=DoNotImport:2" in line, line
    assert "ForeignKeyClient=DoNotImport," in line, line
    xml = _emit("Import Records [ Table: T ; Import fields: T::A=Import:2, T::B=:16, T::C=Match ]")
    assert 'FieldOptions="2" map="Import"' in xml, xml
    assert 'FieldOptions="16" map="Import"' in xml, xml
    assert 'FieldOptions="0" map="Match"' in xml, xml


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
