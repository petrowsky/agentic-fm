#!/usr/bin/env python3
"""test_convert_file_xml_source_hr.py — Convert File keeps an XML source and Keep original data.

With DataSourceType XMLSource FileMaker keeps the File attributes of
``<Profile>``, sets DataType to ``"XML "``, adds XMLType / XSLType and one child
per source (``<XMLFile>`` / ``<XMLHttp>`` / ``<XMLCalc>``, ``<XSLFile>`` /
``<XSLHttp>`` / ``<XSLCalc>``), and writes an XML calculation a second time as
the step-level ``<Calculation>``, the catalog's ``mirrorOf`` param. Every
``<ImportOptions>`` carries KeepOriginalData. The cases in
``agent/fixtures/converter/convert-file-xml-source-fm.json`` were pasted into
FileMaker Pro 26.0.3 (one authored in its Specify XML and XSL Options dialog)
and read back. Mirrors the webviewer ``convert-file-xml-source-hr.test.ts``.
Runs under pytest, or standalone.
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

from test_attrgroup_defaults_hr import _emit, _line  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "convert-file-xml-source-fm.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]


def _canon(xml: str) -> tuple:
    """The step's element tree: attribute order, character references and the
    editor-only DisableStepCollapsed ignored."""
    xml = re.sub(r"<\?xml[^>]*>", "", xml).strip()
    if not xml.startswith("<fmxmlsnippet"):
        xml = '<fmxmlsnippet type="FMObjectList">' + xml + "</fmxmlsnippet>"

    def tree(e: ET.Element) -> tuple:
        text = e.text or ""
        return (e.tag, tuple(sorted(e.attrib.items())), text.strip() if len(e) else text,
                tuple(tree(c) for c in e if c.tag != "DisableStepCollapsed"))

    return tree(ET.fromstring(xml).find("Step"))


def test_filemakers_copy_back_round_trips():
    for c in CASES:
        assert _canon(_emit(_line(c["fm"]))) == _canon(c["fm"]), c["id"]


def test_pasted_step_converts_to_filemakers_copy_back():
    for c in CASES:
        if c["pasted"] and not c.get("pastedDiffers"):
            assert _canon(_emit(_line(c["pasted"]))) == _canon(c["fm"]), c["id"]


def test_xml_calculation_prints_once():
    for c in CASES:
        calc = re.search(r"<XMLCalc><Calculation><!\[CDATA\[(.*?)\]\]>", c["fm"])
        if calc:
            line = _line(c["fm"])
            assert line.count("XMLCalc=" + calc.group(1)) == 1, (c["id"], line)
            assert line.count(calc.group(1)) == 1, (c["id"], line)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
