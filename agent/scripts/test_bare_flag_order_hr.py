#!/usr/bin/env python3
"""test_bare_flag_order_hr.py — Show/Hide Toolbars and Convert File print their
options in the order FileMaker draws them.

FileMaker draws ``Show/Hide Toolbars [ Lock ; Include Edit Record Toolbar ;
Hide ]`` and ``Convert File [ Open File ; Skip Indexes ; With dialog: On ;
“$path” ; Verify SSL Certificates ]``. It never draws Convert File's source
type, profile or import options. The catalog orders both steps' HR with
``hrSlot`` and marks those three params ``fmLineHidden``.

The cases in ``agent/fixtures/converter/bare-flag-order-fm-lines.json`` were
pasted into FileMaker Pro 26.0.3 and read back: the stored step and the line
FileMaker displays. XML -> HR must print FileMaker's line, with the hidden
tokens in their slots for Convert File, and FileMaker's line must convert back
to the stored option states. Mirrors the webviewer
``bare-flag-order-hr.test.ts``. Runs under pytest, or standalone.
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
with open(os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "bare-flag-order-fm-lines.json"), encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]

OPTIONS = ("IncludeEditRecordToolbar", "Lock", "ShowHide",
           "NoInteract", "Option", "SkipIndexes", "VerifySSLCertificates")


def _state(xml: str, el: str) -> str | None:
    m = re.search(rf'<{el} (?:state|value)="(\w+)"', xml)
    return m.group(1) if m else None


def _tokens(line: str) -> list[str]:
    m = re.search(r"\[ (.*) \]\s*$", line)
    return m.group(1).split(" ; ") if m else []


def _shown(line: str) -> list[str]:
    """The tokens of a line FileMaker would draw: no source type, profile or import options."""
    return [t for t in _tokens(line)
            if t not in ("File", "XMLSource") and not t.startswith(("Profile: ", "Import options: "))]


def _snippet(step_xml: str) -> str:
    return f'<fmxmlsnippet type="FMObjectList">{step_xml}</fmxmlsnippet>'


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def test_xml_to_hr_prints_filemakers_order():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        assert _shown(line) == _tokens(c["fmLine"]), (c["id"], line, c["fmLine"])
        if "Show/Hide Toolbars" in c["fmLine"]:
            assert line == c["fmLine"], (c["id"], line)


def test_filemakers_line_converts_back_to_the_stored_state():
    for c in CASES:
        xml = _emit(c["fmLine"])
        for el in OPTIONS:
            want = _state(c["xml"], el)
            if want is not None:
                assert _state(xml, el) == want, (c["id"], c["fmLine"], el)
        assert ("<UniversalPathList>$path</UniversalPathList>" in xml) == \
            ("<UniversalPathList>$path</UniversalPathList>" in c["xml"]), (c["id"], xml)


def test_our_line_round_trips():
    for c in CASES:
        (line,) = snippet_to_hr(_snippet(c["xml"]))
        (again,) = snippet_to_hr(_snippet(_emit(line)))
        assert again == line, (c["id"], line, again)


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
