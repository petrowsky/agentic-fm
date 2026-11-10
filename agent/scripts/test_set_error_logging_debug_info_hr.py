#!/usr/bin/env python3
"""test_set_error_logging_debug_info_hr.py — Set Error Logging's custom debug
info reads and prints with its label, and is written only when there is one.

FileMaker draws the calculation labeled
(``Set Error Logging [ On ; Custom debug info: "dbg" ]``, FileMaker Pro 19.6.3
to 26.0.3), keeps it while logging is off, and stores no <Calculation> when
there is none. The catalog marks the param ``omitWhenEmpty``. The stored bodies
below are FileMaker Pro 26.0.3's copy-back of each case (2026-10-20). Mirrors
the webviewer ``set-error-logging-debug-info-hr.test.ts``. Runs under pytest,
or standalone.
"""

from __future__ import annotations

import os
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

SEMI = 'Get ( ScriptName ) & " ; " & Get ( LastError )'
# (stored state, stored calculation or None, the converter's line)
CASES = [
    ("True", '"dbg"', 'Set Error Logging [ Logging: On ; Custom debug info: "dbg" ]'),
    ("False", '"dbg"', 'Set Error Logging [ Logging: Off ; Custom debug info: "dbg" ]'),
    ("True", SEMI, f"Set Error Logging [ Logging: On ; Custom debug info: {SEMI} ]"),
    ("True", None, "Set Error Logging [ Logging: On ]"),
    ("False", None, "Set Error Logging [ Logging: Off ]"),
]


def _snippet(state: str, calc: str | None) -> str:
    body = f'<Option state="{state}"/>'
    if calc is not None:
        body += f"<Calculation><![CDATA[{calc}]]></Calculation>"
    return ('<fmxmlsnippet type="FMObjectList">'
            f'<Step enable="True" id="200" name="Set Error Logging">{body}</Step>'
            "</fmxmlsnippet>")


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def test_xml_to_hr_labels_the_debug_info():
    for state, calc, line in CASES:
        assert snippet_to_hr(_snippet(state, calc)) == [line]


def test_line_converts_back_to_the_stored_step():
    for state, calc, line in CASES:
        xml = _emit(line)
        assert f'<Option state="{state}"/>' in xml, line
        if calc is None:
            assert "<Calculation" not in xml, line
        else:
            assert f"<Calculation><![CDATA[{calc}]]></Calculation>" in xml, line


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
