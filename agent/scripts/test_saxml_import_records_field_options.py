#!/usr/bin/env python3
"""test_saxml_import_records_field_options.py — the Save as XML reading of an
Import Records field mapping.

Each case in ``agent/fixtures/converter/saxml-import-records-field-options-fm26.json``
is one Import Records step pasted into FileMaker Pro 26.0.3, copied back (``fm``)
and exported with Save a Copy as XML (``saxml``). ``<Map kind>`` is the field's
map (0 Import, 1 DoNotImport, 2 Match) and ``<Map Options>`` its FieldOptions,
which the reader writes after the map (the catalog's ``hrEntrySuffix``). With
Restore off the export holds no ``<ImportField>`` at all, so the reading has no
table, options or mapping to keep, while FileMaker's copy does. Runs under
pytest, or standalone.
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

from catalog_emit import convert_step_with_catalog  # noqa: E402
from catalog_grammar import load_catalog  # noqa: E402
from saxml_read import read_saxml_step  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter",
                 "saxml-import-records-field-options-fm26.json"),
    encoding="utf-8",
) as fh:
    _CASES = {c["id"]: c for c in json.load(fh)["cases"]}
_ENTRY = next(e for e in load_catalog(
    os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
    if e.name == "Import Records")


def _decode(case_id: str) -> str:
    disabled, values, resolver = read_saxml_step(_ENTRY, ET.fromstring(_CASES[case_id]["saxml"]))
    return convert_step_with_catalog(_ENTRY, disabled, values, resolver)


def _mapping(xml: str) -> list:
    return re.findall(r'<Field FieldOptions="(\d+)" map="(\w+)"[^>]*name="([^"]*)"', xml)


def _restore(xml: str) -> str:
    return re.search(r'<Restore state="(\w+)"', xml).group(1)


def test_fixture_holds_every_case():
    assert len(_CASES) == 15


def test_mapping_reads_as_filemakers_copy():
    kept = [c for c in _CASES if not c.startswith("ROFF")]
    assert len(kept) == 10
    for case_id in kept:
        got, fm = _decode(case_id), _CASES[case_id]["fm"]
        assert _mapping(got) == _mapping(fm), case_id
        assert len(_mapping(fm)) == (0 if case_id == "RON-NOTABLE" else 23), case_id
        assert _restore(got) == "True", case_id
    assert ("0", "Match", "Name") in _mapping(_decode("RON-FULL"))
    assert ("16", "Import", "Name") in _mapping(_decode("FO-16"))


def test_restore_off_exports_no_import_order():
    for case_id in (c for c in _CASES if c.startswith("ROFF")):
        assert "<ImportField" not in _CASES[case_id]["saxml"], case_id
        got = _decode(case_id)
        assert _restore(got) == "False", case_id
        assert _mapping(got) == [], case_id


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
