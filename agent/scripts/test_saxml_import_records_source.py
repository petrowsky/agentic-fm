#!/usr/bin/env python3
"""test_saxml_import_records_source.py — the Save as XML reading of Import Records.

Each case in ``agent/fixtures/converter/saxml-import-records-source-fm26.json`` is
one Import Records step pasted into FileMaker Pro 26.0.3, copied back (``fm``) and
exported with Save a Copy as XML (``saxml``). The reader must turn the export into
FileMaker's copy of the same step: the data source kind, the source Profile (file
format, delimiter, field-name row, XML and XSL sources), the path, the target
table, the character set, the import action, KeepOriginalData and
AddRemainingRecords. The field mapping is left out of both sides.

Two things the export does not carry are refused rather than guessed: an XML file
source (FileMaker exports the step's path list in its place) and a source given
without a format (the export has no data source for it). One value is not
exported and takes the catalog default: the delimiter of a source whose
IsPredefined is unset (-1), which FileMaker neither draws nor uses. Runs under
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
from saxml_read import UnsupportedSaXML, read_saxml_step  # noqa: E402
from test_convert_file_xml_source_hr import _canon  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter",
                 "saxml-import-records-source-fm26.json"),
    encoding="utf-8",
) as fh:
    _FIXTURE = json.load(fh)
_CASES = {c["id"]: c for c in _FIXTURE["cases"]}
_ENTRY = next(e for e in load_catalog(
    os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
    if e.name == "Import Records")

# Not decodable from the export (fail loud): an XML file source, and a source
# FileMaker exports no data source for (a path or folder given without a format).
_XML_FILE = {"XF-XSF", "XF-XSH", "XFP-XSFP", "XF-RESTOREOFF", "XF-OPTS", "XF-WITHUPL",
             "XF-NOTABLE", "XF-FIELDS4", "X-EMPTYXSL", "XF-RESTOREOFF-BARE",
             "R2-XUPL", "R2-XUPL-XSL", "R2-XML-PRE"}
_SOURCE_NOT_EXPORTED = {"F-PATH", "F-CORPUS", "FOLDER-BARE", "F-MERG"}


def _decode(case_id: str) -> str:
    disabled, values, resolver = read_saxml_step(_ENTRY, ET.fromstring(_CASES[case_id]["saxml"]))
    return convert_step_with_catalog(_ENTRY, disabled, values, resolver)


def _norm(xml: str, *, unset_delimiter: bool = False) -> tuple:
    """The step without DisableStepCollapsed, the table id and the Profile
    attributes FileMaker fills in itself on paste (a FileMaker source's ``table``,
    an Excel source's FileName / WorksheetName / SelectedSheet, and
    AddRemainingRecords False beside a method other than Add: the reader's
    output pasted into FileMaker 26.0.3 copies back with them as in ``fm``); with
    ``unset_delimiter`` the FieldDelimiter of a Profile whose IsPredefined is -1
    is left out too."""
    xml = re.sub(r"<DisableStepCollapsed[^>]*>(</DisableStepCollapsed>)?", "", xml)
    xml = re.sub(r'\s(?:table|FileName|WorksheetName|SelectedSheet)="[^"]*"(?=[^<>]*>)',
                 "", xml)
    xml = xml.replace(' AddRemainingRecords="False"', "")
    xml = re.sub(r'(<Table\b[^>]*?\bid=")[^"]*(")', r"\1\2", xml)
    if unset_delimiter:
        xml = re.sub(r'(<Profile\b(?=[^>]*\bIsPredefined="-1")[^>]*?)\s+FieldDelimiter="[^"]*"',
                     r"\1", xml)
    return _canon(xml)


def test_fixture_holds_every_case():
    assert len(_CASES) == 53
    assert (_XML_FILE | _SOURCE_NOT_EXPORTED) <= set(_CASES)


def test_export_reads_as_filemakers_copy():
    decoded = [c for c in _CASES if c not in _XML_FILE | _SOURCE_NOT_EXPORTED]
    assert len(decoded) == 36
    for case_id in decoded:
        got, fm = _decode(case_id), _CASES[case_id]["fm"]
        assert _norm(got, unset_delimiter=True) == _norm(fm, unset_delimiter=True), case_id


def test_only_an_unset_delimiter_differs():
    """Outside the unexported delimiter the copy is matched exactly; with a
    predefined (1) or set (0) IsPredefined the delimiter is read too."""
    exact = {c for c in _CASES if c not in _XML_FILE | _SOURCE_NOT_EXPORTED
             and _norm(_decode(c)) == _norm(_CASES[c]["fm"])}
    assert {"R2-PRE0", "R2-PRE1", "R2-PRETAB", "XH-NO", "XC-XSC", "XH-XSC", "FOLDER",
            "F-NOPATH", "X-BLANKTYPE", "R2-FOLDER-PATH", "F-XLSX", "F-DBF"} <= exact
    assert ';' in _decode("R2-PRE0") and 'IsPredefined="0"' in _decode("R2-PRE0")


def test_xml_file_source_is_refused():
    for case_id in sorted(_XML_FILE):
        try:
            _decode(case_id)
        except UnsupportedSaXML as ex:
            assert "does not export an XML file source" in str(ex), case_id
        else:
            raise AssertionError(f"{case_id}: an XML file source was read")


def test_source_the_export_lacks_is_refused():
    for case_id in sorted(_SOURCE_NOT_EXPORTED):
        try:
            _decode(case_id)
        except UnsupportedSaXML as ex:
            assert "does not export" in str(ex), case_id
        else:
            raise AssertionError(f"{case_id}: a source the export lacks was read as none")


def test_every_character_set_and_action():
    sets = {c: re.search(r'CharacterSet="([^"]*)"', _decode(c)).group(1)
            for c in _CASES if c.startswith("R3-")}
    assert sets == {c: c[3:] for c in sets} and len(sets) == 9
    assert 'method="UpdateOnMatch"' in _decode("F-ADDREM")
    assert 'AddRemainingRecords="True"' in _decode("F-ADDREM")
    assert 'method="Update"' in _decode("F-ADDREM-F")
    assert 'KeepOriginalData="True"' in _decode("F-KEEP")
    assert '<Table id="0" name="Invoices"/>' in _decode("F-KEEP")


def test_every_path_list_line_is_kept():
    """A path list exported one line per <Location> keeps every line."""
    step = ET.fromstring(_CASES["F-CSVPROF"]["saxml"])
    obj = step.find(".//DataSourceReference/UniversalPathList/ObjectList")
    ET.SubElement(obj, "Location").text = "file:b.csv"
    obj.find("Location").text = "$path"
    disabled, values, resolver = read_saxml_step(_ENTRY, step)
    out = convert_step_with_catalog(_ENTRY, disabled, values, resolver)
    assert "<UniversalPathList>$path&#10;file:b.csv</UniversalPathList>" in out \
        or "<UniversalPathList>$path\nfile:b.csv</UniversalPathList>" in out, out


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as ex:
                fails += 1
                print(f"FAIL {name}: {ex}")
    sys.exit(1 if fails else 0)
