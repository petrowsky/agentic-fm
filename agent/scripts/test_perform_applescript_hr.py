#!/usr/bin/env python3
"""test_perform_applescript_hr.py — Perform AppleScript's mode read from the token's shape.

FileMaker prints no ContentType token for Perform AppleScript. Text mode shows the
literal straight-quoted and unescaped (``Perform AppleScript [ "a "b";c] d" ]``),
Calculation mode shows the calculation as written, and an empty step of either mode
shows ``[ ]`` and copies back with no ``<Text>`` or ``<Calculation>``. The catalog
models ContentType as a governing discriminator whose two values each reveal one
bare companion (``shape_routed_values``); the text is ``hrQuoted`` with
``hrQuoteStyle: "straight"``. The (text, FileMaker's line) pairs below were pasted
into FileMaker Pro 26.0.3 and read back. Mirrors the webviewer
``perform-applescript-hr.test.ts``. Runs under pytest, or standalone.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from calc_literal import reads_as_quoted_literal_text
from catalog_emit import EmptyResolver, convert_step_with_catalog, esc_xml, match_param_values
from catalog_grammar import load_catalog
from snippet_to_hr import snippet_to_hr

from agent.fmlint.formats.hr_parser import parse_hr

_CATALOG = {
    e.name: e
    for e in load_catalog(os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json"))
}
_LQ = "“"

# (stored text, FileMaker's displayed line, our line)
CASES = [
    ('tell app "Finder" to activate', 'Perform AppleScript [ "tell app "Finder" to activate" ]',
     'Perform AppleScript [ "tell app \\"Finder\\" to activate" ]'),
    ('a "b".txt', 'Perform AppleScript [ "a "b".txt" ]', 'Perform AppleScript [ "a \\"b\\".txt" ]'),
    ("file:a;b.txt", 'Perform AppleScript [ "file:a;b.txt" ]', 'Perform AppleScript [ "file:a;b.txt" ]'),
    ("file:a]b.txt", 'Perform AppleScript [ "file:a]b.txt" ]', 'Perform AppleScript [ "file:a]b.txt" ]'),
    ("file:a " + _LQ + "b.txt", 'Perform AppleScript [ "file:a ' + _LQ + 'b.txt" ]',
     'Perform AppleScript [ "file:a \\' + _LQ + 'b.txt" ]'),
    ('a "b', 'Perform AppleScript [ "a "b" ]', 'Perform AppleScript [ "a \\"b" ]'),
    ('a "b";c] d', 'Perform AppleScript [ "a "b";c] d" ]', 'Perform AppleScript [ "a \\"b\\";c] d" ]'),
    ("a\\b", 'Perform AppleScript [ "a\\b" ]', 'Perform AppleScript [ "a\\b" ]'),
    ('say "hi"', 'Perform AppleScript [ "say "hi"" ]', 'Perform AppleScript [ "say \\"hi\\"" ]'),
    ("  x  ", 'Perform AppleScript [ "  x  " ]', 'Perform AppleScript [ "  x  " ]'),
]


def _text_xml(text: str) -> str:
    body = "<Text>" + esc_xml(text) + "</Text>" if text else ""
    return (
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="67" name="Perform AppleScript">'
        '<ContentType value="Text"/>' + body + "</Step></fmxmlsnippet>"
    )


def _calc_xml(calc: str) -> str:
    return (
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="67" name="Perform AppleScript">'
        '<ContentType value="Calculation"/><Calculation><![CDATA[' + calc + "]]></Calculation></Step></fmxmlsnippet>"
    )


def _emit(hr: str) -> str:
    (pl,) = parse_hr(hr)
    entry = _CATALOG[pl.step_name]
    return convert_step_with_catalog(entry, pl.disabled, match_param_values(entry, pl.params), EmptyResolver())


def test_text_mode_renders_straight_quoted_and_round_trips():
    for text, _fm, ours in CASES:
        assert snippet_to_hr(_text_xml(text)) == [ours], text
        out = _emit(ours)
        assert '<ContentType value="Text"/>' in out and "<Text>" + esc_xml(text) + "</Text>" in out, out
        assert "<Calculation>" not in out, out


def test_filemakers_line_reads_as_the_stored_text():
    for text, fm_line, _ours in CASES:
        out = _emit(fm_line)
        assert '<ContentType value="Text"/>' in out and "<Text>" + esc_xml(text) + "</Text>" in out, (fm_line, out)
        assert "<Calculation>" not in out, out


def test_calculation_mode_renders_bare_and_round_trips():
    for calc in ['"say " & Quote ( $x ) & " ; done"', "$script", 'Upper ( "a" ) & "b"']:
        assert snippet_to_hr(_calc_xml(calc)) == ["Perform AppleScript [ " + calc + " ]"]
        out = _emit("Perform AppleScript [ " + calc + " ]")
        assert '<ContentType value="Calculation"/>' in out and "<![CDATA[" + calc + "]]>" in out, out
        assert "<Text>" not in out, out


def test_single_literal_calculation_names_its_mode():
    calc = '"tell application \\"Notes\\" to open"'
    assert snippet_to_hr(_calc_xml(calc)) == ["Perform AppleScript [ Calculation ; " + calc + " ]"]
    out = _emit("Perform AppleScript [ Calculation ; " + calc + " ]")
    assert '<ContentType value="Calculation"/>' in out and "<![CDATA[" + calc + "]]>" in out, out
    fm = _emit("Perform AppleScript [ " + calc + " ]")
    assert "<Text>tell application &quot;Notes&quot; to open</Text>" in fm, fm


def test_empty_step():
    assert snippet_to_hr(_text_xml("")) == ["Perform AppleScript [ ]"]
    out = _emit("Perform AppleScript [ ]")
    assert '<ContentType value="Calculation"/>' in out, out
    assert "<Calculation>" not in out and "<Text>" not in out, out


def test_reads_as_quoted_literal_text():
    for t in ['"x"', '"a \\"b\\""', '"a "b";c] d"', '"say "hi""', '"a "b"', '"x"  "y"',
              _LQ + "x”", '"x" ; "y"']:
        assert reads_as_quoted_literal_text(t), t
    for t in ["$x", '"a" & $x', '"say " & $x & " done"', '"a" & Upper ( "b" ) & "c"',
              '"a" and "b"', 'Upper ( "a" )', '"a" ＆ "b"', '"a" & List ( "b" ; "c" )', ""]:
        assert not reads_as_quoted_literal_text(t), t


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
