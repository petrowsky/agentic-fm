#!/usr/bin/env python3
"""test_truncate_table_hr.py — Truncate Table's table name drawn the way FileMaker draws it.

FileMaker Pro 26.0.3 draws a named table quoted, ``Table: “Line Items”``, and the
current table bare, ``Table: <Current Table>``. It never escapes the name it
displays, so a name holding a curly quote shows as ``“QFC“Probe”``; XML -> HR writes
that one as an escaped literal (``“QFC\\“Probe”``) so it reads back unambiguously.
The (stored step, FileMaker's displayed line) pairs below were pasted into FileMaker
and read back. XML -> HR must print FileMaker's line (or its escaped form), and
both FileMaker's line and ours must convert back to the same table, from the
clipboard reader and from the SaXML reader. Mirrors the webviewer
``truncate-table-hr.test.ts``. Runs under pytest, or standalone.
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

from catalog_emit import EmptyResolver, convert_step_with_catalog, esc_xml, match_param_values
from catalog_grammar import load_catalog
from saxml_read import read_saxml_step
from snippet_to_hr import snippet_to_hr

from agent.fmlint.formats.hr_parser import parse_hr

_ENTRY = {
    e.name: e
    for e in load_catalog(os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
}["Truncate Table"]

# (stored id, stored name, FileMaker's displayed line, our line)
CASES = [
    ("143", "Testing", "Truncate Table [ With dialog: Off ; Table: “Testing” ]", None),
    ("138", "Line Items", "Truncate Table [ With dialog: Off ; Table: “Line Items” ]", None),
    ("301", "QFC¶Pil", "Truncate Table [ With dialog: Off ; Table: “QFC¶Pil” ]", None),
    ("300", "QFC“Probe", "Truncate Table [ With dialog: Off ; Table: “QFC“Probe” ]",
     "Truncate Table [ With dialog: Off ; Table: “QFC\\“Probe” ]"),
    ("-1", "<Current Table>", "Truncate Table [ With dialog: Off ; Table: <Current Table> ]", None),
]


def _snippet(tid: str, name: str) -> str:
    return (
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="182" name="Truncate Table">'
        '<NoInteract state="True"/><BaseTable id="' + tid + '" comment="" name="' + esc_xml(name)
        + '"/></Step></fmxmlsnippet>'
    )


def _saxml(tid: str, name: str) -> ET.Element:
    return ET.fromstring(
        '<Step id="182" name="Truncate Table" enable="True"><Options>138</Options>'
        '<ParameterValues membercount="2"><Parameter type="Boolean">'
        '<Boolean type="With dialog" id="128" value="False"/></Parameter>'
        '<Parameter type="List"><List name="' + esc_xml(name) + '" value="1">'
        '<BaseTableReference id="' + tid + '" name="' + esc_xml(name) + '"/></List></Parameter>'
        "</ParameterValues></Step>"
    )


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    return convert_step_with_catalog(_ENTRY, pl.disabled, match_param_values(_ENTRY, pl.params), EmptyResolver())


def _base_table(tid: str, name: str) -> str:
    if tid == "-1":
        return '<BaseTable id="-1" name="' + esc_xml(name) + '" comment=""/>'
    return '<BaseTable name="' + esc_xml(name) + '" comment=""/>'


def test_xml_to_hr_prints_filemakers_line():
    for tid, name, fm, ours in CASES:
        assert snippet_to_hr(_snippet(tid, name)) == [ours or fm], name


def test_both_lines_convert_back_to_the_table():
    for tid, name, fm, ours in CASES:
        for line in (fm, ours or fm):
            assert _base_table(tid, name) in _emit(line), (line, _emit(line))


def test_saxml_reads_the_table():
    for tid, name, _fm, _ours in CASES:
        disabled, values, resolver = read_saxml_step(_ENTRY, _saxml(tid, name))
        out = convert_step_with_catalog(_ENTRY, disabled, values, resolver)
        assert _base_table(tid, name) in out, (name, out)


if __name__ == "__main__":
    failed = 0
    for fname, fn in sorted(globals().items()):
        if fname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {fname}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {fname}: {e}")
    sys.exit(1 if failed else 0)
