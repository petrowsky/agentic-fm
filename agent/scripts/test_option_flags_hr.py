#!/usr/bin/env python3
"""test_option_flags_hr.py — the export and Save Records as steps read and print
their option flags as FileMaker draws them.

FileMaker draws Automatically open and Create email on Export Field Contents,
Export Records and Save Records as Excel / JSONL / PDF / Snapshot Link as bare
flags only when on, right after the path (after With dialog on Save Records as
PDF); Restore (Excel, PDF) and Append (PDF) are bare flags too. The catalog marks
them ``flagStyle`` and orders the line with ``hrSlot``. The cases in
``agent/fixtures/converter/option-flags-fm-lines.json`` were pasted into
FileMaker Pro 26.0.3 and read back: the stored step and the line FileMaker
displays. Mirrors the webviewer ``option-flags-hr.test.ts``. Runs under pytest,
or standalone.

FileMaker's line is lossy where a step has no path: Export Field Contents and
Save Records as JSONL / PDF / Snapshot Link then draw no option flag although the
step keeps them on (``hidesOptions``), so that line converts with them off.
"""

from __future__ import annotations

import json
import os
import re
import sys
import warnings

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
with open(os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "option-flags-fm-lines.json"), encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]

# The flags each step draws bare, by element.
FLAGS = {"AutoOpen": "Automatically open", "CreateEmail": "Create email", "Restore": "Restore"}
PDF_FLAGS = dict(FLAGS, Option="Append")
FLAG_LABELS = set(PDF_FLAGS.values())


def _flags(step: str) -> dict[str, str]:
    if step == "Save Records as PDF":
        return PDF_FLAGS
    if step == "Save Records as Excel":
        return FLAGS
    return {k: v for k, v in FLAGS.items() if k != "Restore"}


def _state(xml: str, el: str) -> str | None:
    m = re.search(rf'<{el} state="(\w+)"', xml)
    return m.group(1) if m else None


def _snippet(step_xml: str) -> str:
    return f'<fmxmlsnippet type="FMObjectList">{step_xml}</fmxmlsnippet>'


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def _tokens(line: str) -> list[str]:
    body = line[line.index("[") + 1 : line.rindex("]")]
    return [t.strip() for t in body.split(";")]


def _flag_and_path_order(line: str) -> list[str]:
    """The flag tokens and the quoted path, in line order."""
    return [t for t in _tokens(line) if t in FLAG_LABELS or t.startswith("“")]


def test_filemakers_line_converts_back_with_the_stored_options():
    for c in CASES:
        xml = _emit(c["fmLine"])
        for el in _flags(c["step"]):
            hidden = c["hidesOptions"] and el in ("AutoOpen", "CreateEmail")
            want = _state(c["xml"], el)
            if hidden and want is not None:
                want = "False"
            assert _state(xml, el) == want, (c["fmLine"], el)


def test_xml_to_hr_prints_the_options_as_filemaker_draws_them():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        for label in FLAG_LABELS:
            assert f"{label}:" not in line, line
        if c["hidesOptions"]:
            continue
        assert _flag_and_path_order(line) == _flag_and_path_order(c["fmLine"]), (line, c["fmLine"])


def test_our_line_converts_back_to_the_stored_options():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        xml = _emit(line)
        for el in _flags(c["step"]):
            assert _state(xml, el) == _state(c["xml"], el), (line, el)


def test_snapshot_link_prints_filemakers_line():
    for c in CASES:
        if c["step"] == "Save Records as Snapshot Link" and not c["hidesOptions"]:
            assert snippet_to_hr(_snippet(c["xml"])) == [c["fmLine"]]



# Save Records as JSONL draws no token for its Option; with it on FileMaker draws
# the source table labeled and quoted after Format for fine-tuning
# (``Table: “Invoices”``) and writes <Table> only then. A Completion Field that
# names no field draws as ``<Field Missing>``, which the converters do not print.
def _jsonl_cases():
    return [c for c in CASES if c["step"] == "Save Records as JSONL"]


def test_save_records_as_jsonl_prints_filemakers_line():
    for c in _jsonl_cases():
        if not c["hidesOptions"] and "<Field Missing>" not in c["fmLine"]:
            assert snippet_to_hr(_snippet(c["xml"])) == [c["fmLine"]]


def test_save_records_as_jsonl_line_keeps_option_table_and_path():
    for c in _jsonl_cases():
        for line in (c["fmLine"], snippet_to_hr(_snippet(c["xml"]))[0]):
            xml = _emit(line)
            assert _state(xml, "Option") == _state(c["xml"], "Option"), line
            table = re.search(r'<Table id="\d*" name="([^"]*)"/>', c["xml"])
            got = re.search(r'<Table id="\d*" name="([^"]*)"/>', xml)
            assert (got and got.group(1)) == (table and table.group(1)), line
            if "<UniversalPathList>" in c["xml"]:
                assert "<UniversalPathList>$path</UniversalPathList>" in xml, line


# A bare flag written in the labeled boolean spelling every other option of the
# step uses (``Append: On``): On sets the flag, Off leaves it clear, and neither,
# nor any other value after the flag's label, is ever read as the file path.
# Before, the unlabeled path took the token positionally and FileMaker drew the
# path ``“ On”`` (FileMaker Pro 26.0.3).
def test_labeled_flag_spelling_sets_the_flag_never_the_path():
    xml = _emit("Save Records as PDF [ Append: On ; With dialog: Off ]")
    assert _state(xml, "Option") == "True", xml
    assert "<UniversalPathList>" not in xml, xml
    xml = _emit("Export Records [ With dialog: Off ; “$p” ; Automatically open: On ; Create email: Off ]")
    assert _state(xml, "AutoOpen") == "True" and _state(xml, "CreateEmail") == "False", xml
    assert "<UniversalPathList>$p</UniversalPathList>" in xml, xml
    xml = _emit("Export Records [ With dialog: Off ; Automatically open: Yes ]")
    assert "<UniversalPathList>" not in xml, xml


# An option in labeled style that the step does not have is not a path either:
# FileMaker keeps ``XMLSource: $xmlPath`` as a literal path. A real path, and the
# Profile that holds an XML source, are unaffected.
def test_labeled_token_is_never_the_path():
    for line in (
        "Import Records [ With dialog: Off ; XMLSource: $xmlPath ; XSLT: $xslPath ; Add ; UTF-8 ; Table: Invoices ]",
        "Convert File [ With dialog: Off ; XMLSource: $url ]",
        "Convert File [ With dialog: Off ; XMLSource:$url ]",
        "Convert File [ With dialog: Off ; XMLHttp:https://e.com/a.xml ]",
        "Save Records as PDF [ With dialog: Off ; Output: On ]",
    ):
        assert "<UniversalPathList" not in _emit(line), line
    assert "<UniversalPathList>$p</UniversalPathList>" in _emit('Convert File [ With dialog: Off ; XSLT: $x ; "$p" ]')
    for token, path in (
        ("$url", "$url"),
        ('"Reports: Q3.csv"', "Reports: Q3.csv"),
        ("file:data.csv", "file:data.csv"),
        ("filewin:/C:/data.csv", "filewin:/C:/data.csv"),
        ("C:/data.csv", "C:/data.csv"),
        ("https://e.com:8080/a.xml", "https://e.com:8080/a.xml"),
        ("file:$name", "file:$name"),
        ("filelinux:$name", "filelinux:$name"),
    ):
        xml = _emit("Convert File [ With dialog: Off ; " + token + " ]")
        assert "<UniversalPathList>" + path + "</UniversalPathList>" in xml, token
    xml = _emit('Convert File [ With dialog: Off ; XMLSource ; Profile: DataType="XML ", XMLType=XMLHttp, XSLType=NoXSL, XMLHttp=https://e.com/a.xml ]')
    assert "<XMLHttp>https://e.com/a.xml</XMLHttp>" in xml, xml


# A held-back token is reported, so a dropped path is never silent.
def test_labeled_token_held_back_is_reported():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _emit("Convert File [ With dialog: Off ; Reports: Q3.csv ]")
    assert any('"Reports: Q3.csv"' in str(w.message) for w in caught), caught
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _emit('Convert File [ With dialog: Off ; "Reports: Q3.csv" ]')
    assert not caught, caught


# Sort Records' Restore as FileMaker draws it (``fmLineToken``) next to a sort
# order is a no-op; the sort and the derived Restore are unchanged.
def test_sort_records_filemakers_restore_token_is_a_no_op():
    entry = _CATALOG["Sort Records"]
    (pl,) = parse_hr("Sort Records [ Restore ; With dialog: Off ; Sort: Invoices::Date=Ascending ]")
    (bare,) = parse_hr("Sort Records [ With dialog: Off ; Sort: Invoices::Date=Ascending ]")
    assert match_param_values(entry, pl.params) == match_param_values(entry, bare.params)

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
