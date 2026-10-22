#!/usr/bin/env python3
"""test_bare_option_flags_hr.py — option flags FileMaker draws bare read back
with the state FileMaker stored.

FileMaker draws Get Folder Path's Allow Folder Creation, Add Account's and Reset
Account Password's Expire password, Go to List of Records' New window, Convert
File's Skip Indexes and Show/Hide Toolbars' Include Edit Record Toolbar and Lock
as a bare label only when on, and Convert File's Open File only when Option is
False. The catalog marks them ``flagStyle`` (Open File also ``invertedHr``), so
the label's presence is the value. Before that, FileMaker's own line converted
back wrong: ``Get Folder Path [ $result ]`` turned Allow Folder Creation on and
``Reset Account Password [ … ; Expire password ]`` turned it off.

The cases in ``agent/fixtures/converter/bare-option-flags-fm-lines.json`` were
pasted into FileMaker Pro 26.0.3 and read back: the stored step and the line
FileMaker displays. Mirrors the webviewer ``bare-option-flags-hr.test.ts``. Runs
under pytest, or standalone.
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
with open(os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "bare-option-flags-fm-lines.json"), encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]

LABELS = {
    "AllowFolderCreation": "Allow Folder Creation",
    "ChgPwdOnNextLogin": "Expire password",
    "ShowInNewWindow": "New window",
    "Option": "Open File",
    "SkipIndexes": "Skip Indexes",
    "IncludeEditRecordToolbar": "Include Edit Record Toolbar",
    "Lock": "Lock",
}


def _state(xml: str, el: str) -> str | None:
    m = re.search(rf'<{el} (?:state|value)="(\w+)"', xml)
    return m.group(1) if m else None


def _tokens(line: str) -> list[str]:
    m = re.search(r"\[(.*)\]\s*$", line)
    return [t.strip() for t in m.group(1).split(" ; ")] if m else []


def _snippet(step_xml: str) -> str:
    return f'<fmxmlsnippet type="FMObjectList">{step_xml}</fmxmlsnippet>'


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def test_filemakers_line_converts_back_to_the_stored_state():
    for c in CASES:
        xml = _emit(c["fmLine"])
        for el, want in c["flags"].items():
            assert _state(xml, el) == want, (c["fmLine"], el)


def test_xml_to_hr_draws_each_label_when_filemaker_does():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        ours, fm = _tokens(line), _tokens(c["fmLine"])
        for el in c["flags"]:
            label = LABELS[el]
            assert (label in ours) == (label in fm), (line, c["fmLine"], el)
            assert not any(t.startswith(label + ":") for t in ours), (line, el)


def test_our_line_converts_back_to_the_stored_state():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        xml = _emit(line)
        for el, want in c["flags"].items():
            assert _state(xml, el) == want, (line, el)


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
