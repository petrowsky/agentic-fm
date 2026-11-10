#!/usr/bin/env python3
"""test_attrgroup_defaults_hr.py — an option group at its defaults prints nothing.

FileMaker never draws Speak's SpeechOptions or Create PDF's Document / Security /
View on its line: ``Speak [ "hi" ]`` and ``Create PDF [ ]`` whatever they hold.
The catalog marks those groups ``hrOmitAtDefault``: XML -> HR prints no token
while the element equals its catalog default, and HR -> XML writes that default
element back when the token is absent, so nothing is lost either way. A group
off its default still prints its whole body. The cases in
``agent/fixtures/converter/attrgroup-defaults-fm-lines.json`` were pasted into
FileMaker Pro 26.0.3 and read back: the stored step and the line FileMaker
displays. Mirrors the webviewer ``attrgroup-defaults-hr.test.ts``. Runs under
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

from catalog_emit import EmptyResolver, convert_step_with_catalog, match_param_values
from catalog_grammar import load_catalog
from snippet_to_hr import snippet_to_hr

from agent.fmlint.formats.hr_parser import parse_hr

_CATALOG = {
    e.name: e
    for e in load_catalog(os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
}
with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "attrgroup-defaults-fm-lines.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]

# Cases whose options sit at the catalog defaults: our line is FileMaker's.
AT_DEFAULT = {"S1", "S2", "S6", "C0", "C0R"}
GROUP_LABELS = ("Speech options:", "Document:", "Security:", "View:")


def _snippet(step_xml: str) -> str:
    return f'<fmxmlsnippet type="FMObjectList">{step_xml}</fmxmlsnippet>'


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def _line(xml: str) -> str:
    (line,) = snippet_to_hr(_snippet(xml))
    return line


def _canon(xml: str) -> str:
    """Element / attribute structure, whitespace and attribute order ignored, and
    the empty required calculation FileMaker drops on paste (Speak with no text)."""
    xml = re.sub(r"<DisableStepCollapsed[^>]*>(</DisableStepCollapsed>)?", "", xml)
    xml = re.sub(r"<Calculation><!\[CDATA\[\]\]></Calculation>", "", xml)
    xml = re.sub(r"<(\w+)([^>]*)></\1>", r"<\1\2/>", xml)

    def attrs(m: re.Match) -> str:
        pairs = sorted(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        return "<" + m.group(1) + "".join(f' {k}="{v}"' for k, v in pairs) + m.group(3)

    xml = re.sub(r"<(\w+)((?:\s+\w+=\"[^\"]*\")*)\s*(/?>)", attrs, xml)
    xml = re.sub(r"<\?xml[^>]*>|</?fmxmlsnippet[^>]*>", "", xml)
    return re.sub(r">\s+<", "><", xml).strip()


def test_options_at_their_defaults_print_no_token():
    for c in CASES:
        line = _line(c["xml"])
        if c["id"] in AT_DEFAULT:
            for label in GROUP_LABELS:
                assert label not in line, (c["id"], line)
            if c["fmLine"] != c["fmLine"].split(" [")[0] + " [ ]":
                assert line == c["fmLine"], (c["id"], line)


def test_options_off_their_defaults_print_their_body():
    for c in CASES:
        if c["id"] not in AT_DEFAULT:
            assert any(label in _line(c["xml"]) for label in GROUP_LABELS), c["id"]


def test_our_line_converts_back_to_filemakers_step():
    for c in CASES:
        assert _canon(_emit(_line(c["xml"]))) == _canon(c["xml"]), c["id"]


def test_no_token_writes_the_default_options():
    speak = _emit('Speak [ "hi" ]')
    assert re.search(r'<SpeechOptions WaitForCompletion="False" VoiceId="0"/>', speak), speak
    pdf = _canon(_emit("Create PDF [ Restore ]"))
    want = _canon(next(c["xml"] for c in CASES if c["id"] == "C0R"))
    assert pdf == want, pdf


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
