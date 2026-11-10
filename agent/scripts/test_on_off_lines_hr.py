#!/usr/bin/env python3
"""test_on_off_lines_hr.py — five steps read from the bare ``On`` / ``Off``
FileMaker draws for them.

FileMaker draws the on/off option of Set Error Logging, Set AI Call Logging,
Set Layout Object Animation, Set Revert Transaction on Error and Set Use System
Formats with no label (``Set Layout Object Animation [ Off ]``). The converter's
HR labels it (``Animation: Off``), so FileMaker's own line used to read back
with the step's default state. The catalog marks each of these booleans
``fmLineBare``: HR -> XML takes a bare token naming one of its values.

The cases in ``agent/fixtures/converter/on-off-fm-lines.json`` were pasted into
FileMaker Pro 26.0.3 and read back. Mirrors the webviewer
``on-off-lines-hr.test.ts``. Runs under pytest, or standalone.
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
with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "on-off-fm-lines.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def _stored(xml: str) -> str:
    """The on/off state, the calculation and which logging flags are present."""
    m = re.search(r'state="(\w+)"', xml)
    out = m.group(1) if m else "?"
    c = re.search(r"<!\[CDATA\[(.*?)\]\]>", xml, re.S)
    if c and c.group(1):
        out += "|calc=" + c.group(1)
    if "<VerboseMode" in xml:
        out += "|verbose"
    if "<TruncateEmbeddingVectorsMode" in xml:
        out += "|truncate"
    return out


def test_filemakers_line_keeps_the_state():
    for c in CASES:
        assert _stored(_emit(c["fmLine"])) == _stored(c["xml"]), (c["id"], c["fmLine"])


def test_the_labeled_spelling_still_reads():
    assert _stored(_emit("Set Layout Object Animation [ Animation: Off ]")) == "False"
    assert _stored(_emit("Set Revert Transaction on Error [ Revert on error: On ]")) == "True"
    assert _stored(_emit('Set Error Logging [ Logging: On ; Custom debug info: "dbg" ]')) == 'True|calc="dbg"'


def test_converter_hr_round_trips_every_step():
    for c in CASES:
        (line,) = snippet_to_hr(f'<fmxmlsnippet type="FMObjectList">{c["xml"]}</fmxmlsnippet>')
        assert _stored(_emit(line)) == _stored(c["xml"]), (c["id"], line)


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
