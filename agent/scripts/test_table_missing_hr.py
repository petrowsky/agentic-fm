#!/usr/bin/env python3
"""test_table_missing_hr.py — a field reference with no table and no field reads
``<Table Missing>``, the way FileMaker shows it.

FileMaker stores a single-field parameter with neither a table nor a field as
``<Field table="" id="0" name=""/>`` and its Script Workspace line draws
``<Table Missing>`` in the field slot. The cases below were pasted into
FileMaker Pro 26.0.3 and read back: each pair is FileMaker's stored step and the
line it displays. XML -> HR must print that line, and the line must convert back
to the same step, with an empty reference rather than a field called
``<Table Missing>``. Parameters the catalog marks ``hrNoTableBlank`` draw nothing
for that reference, and an absent ``<Field>`` is a different step that draws no
field token at all. Mirrors the webviewer ``table-missing-hr.test.ts``. Runs
under pytest, or standalone.
"""

from __future__ import annotations

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

_EMPTY = '<Field table="" id="0" name=""/>'

# (step id, step name, FileMaker's stored step body, FileMaker's displayed line),
# measured 2026-10-14.
CASES = [
    ("76", "Set Field", "<Calculation><![CDATA[1]]></Calculation>" + _EMPTY,
     "Set Field [ <Table Missing> ; 1 ]"),
    ("17", "Go to Field", '<SelectAll state="False"/>' + _EMPTY,
     "Go to Field [ <Table Missing> ]"),
    ("61", "Insert Text", '<SelectAll state="False"/><Text>x</Text>' + _EMPTY,
     "Insert Text [ Target: <Table Missing> ; “x” ]"),
    ("116", "Set Next Serial Value", "<Calculation><![CDATA[1]]></Calculation>" + _EMPTY,
     "Set Next Serial Value [ <Table Missing> ; 1 ]"),
    ("77", "Insert Calculated Result",
     '<SelectAll state="False"/><Calculation><![CDATA[1]]></Calculation>' + _EMPTY,
     "Insert Calculated Result [ Target: <Table Missing> ; 1 ]"),
    ("154", "Sort Records by Field", '<SortRecordsByField value="SortAscending"/>' + _EMPTY,
     "Sort Records by Field [ Ascending ; <Table Missing> ]"),
    ("91", "Replace Field Contents",
     '<NoInteract state="True"/><Restore state="False"/>' + _EMPTY
     + '<With value="Calculation"/><Calculation><![CDATA["value"]]></Calculation>'
     '<SerialNumbers PerformAutoEnter="False" UpdateEntryOptions="False" increment="0"'
     ' InitialValue="" UseEntryOptions="False"/>',
     'Replace Field Contents [ With dialog: Off ; <Table Missing> ; "value" ]'),
    ("225", "Save Records as JSONL",
     '<Option state="True"/><CreateDirectories state="False"/><FineTuneFormat state="False"/>'
     '<AutoOpen state="False"/><CreateEmail state="False"/>'
     "<UniversalPathList>$path</UniversalPathList>"
     '<Table id="1065094" name="Invoices"/>' + _EMPTY
     + "<SaveAsJSONL>" + _EMPTY + "</SaveAsJSONL>",
     ("Save Records as JSONL [ Format for fine-tuning: Off ; Table: “Invoices” ; "
      "Completion Field: <Table Missing> ; “$path” ; Create folders: Off ]")),
]


def _snippet(sid: str, name: str, body: str) -> str:
    return (
        '<fmxmlsnippet type="FMObjectList">'
        f'<Step enable="True" id="{sid}" name="{name}">{body}</Step>'
        "</fmxmlsnippet>"
    )


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def _fields(xml: str) -> set[str]:
    """The distinct ``<Field>`` references."""
    return {re.sub(r"\s*/>", "/>", f) for f in re.findall(r"<Field\b[^>]*>", xml)}


def test_xml_to_hr_prints_filemakers_line():
    for sid, name, body, fm_line in CASES:
        assert snippet_to_hr(_snippet(sid, name, body)) == [fm_line]


def test_filemakers_line_converts_back_to_an_empty_reference():
    for sid, name, body, fm_line in CASES:
        xml = _emit(fm_line)
        assert "Table Missing" not in xml, fm_line
        assert _fields(xml) == _fields(_snippet(sid, name, body)), fm_line


def test_hr_no_table_blank_param_draws_nothing():
    # Perform Semantic Find as FileMaker stores it unconfigured: the outer Target
    # field draws <Table Missing>, the Save result field (hrNoTableBlank) nothing.
    xml = _snippet(
        "218", "Perform Semantic Find",
        _EMPTY + '<LLMSemanticFind><Query type="1"/><SaveResult>' + _EMPTY
        + "</SaveResult></LLMSemanticFind>",
    )
    (line,) = snippet_to_hr(xml)
    assert "Target field: <Table Missing>" in line
    assert "Save result" not in line


def test_absent_field_draws_no_token():
    xml = _snippet("61", "Insert Text", '<SelectAll state="False"/><Text>x</Text>')
    assert snippet_to_hr(xml) == ["Insert Text [ “x” ]"]


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
