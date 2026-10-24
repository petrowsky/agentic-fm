#!/usr/bin/env python3
"""test_trigger_claris_connect_flow_hr.py — Trigger Claris Connect Flow converts to FileMaker's step.

FileMaker's line draws only ``Flow: <name> ; JSON Data: <calc> ; Target:
<target>``; the URL (step-level ``<Calculation>``), the AppID:APIKey (``<Auth>``)
and the cURL options it writes on insert are stored but never drawn, ``<Text>``
is its copy of ``<Flow>``, and the four option flags are not in its dialog. The
catalog prints the stored values as labeled tokens, the cURL options only off
FileMaker's default (``hrOmitAtDefault``), and writes ``<Text>`` from the flow
(``mirrorOf: "Flow"``). The cases in
``agent/fixtures/converter/trigger-claris-connect-flow-fm.json`` were pasted
into FileMaker Pro 26.0.3 and read back. Mirrors the webviewer
``trigger-claris-connect-flow-hr.test.ts``. Runs under pytest, or standalone.
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

from test_attrgroup_defaults_hr import _emit, _line  # noqa: E402

with open(
    os.path.join(REPO_ROOT, "agent", "fixtures", "converter", "trigger-claris-connect-flow-fm.json"),
    encoding="utf-8",
) as fh:
    CASES = json.load(fh)["cases"]

_FLAGS = r"<(NoInteract|DontEncodeURL|SelectAll|VerifySSLCertificates) state=\"\w+\"\s*/?>(</\1>)?"
_DROP = {"exact": (), "exceptFlags": ("flags",), "exceptCurl": ("curl",),
         "exceptFlagsAndCurl": ("flags", "curl"), "hrOnly": None}


def _canon(xml: str, drop: tuple = ()) -> str:
    """Element / attribute structure and whitespace; field ids are not compared
    (converted without a solution context)."""
    xml = re.sub(r"<DisableStepCollapsed[^>]*>(</DisableStepCollapsed>)?", "", xml)
    if "flags" in drop:
        xml = re.sub(_FLAGS, "", xml)
    if "curl" in drop:
        xml = re.sub(r"<CURLOptions>.*?</CURLOptions>", "", xml, flags=re.S)
    xml = re.sub(r'(<Field table="[^"]*") id="\d+"', r"\1", xml)
    xml = re.sub(r"<(\w+)([^>]*)></\1>", r"<\1\2/>", xml)
    xml = re.sub(r"\s*/>", "/>", xml)
    xml = re.sub(r"<\?xml[^>]*>|</?fmxmlsnippet[^>]*>", "", xml)
    return re.sub(r">\s+<", "><", xml).strip()


def test_prints_filemakers_stored_step():
    for c in CASES:
        assert _line(c["fm"]) == c["hr"], (c["id"], _line(c["fm"]))


def test_hr_converts_back_to_filemakers_step():
    for c in CASES:
        drop = _DROP[c["roundTrip"]]
        if drop is None:
            continue
        assert _canon(_emit(c["hr"]), drop) == _canon(c["fm"], drop), c["id"]


def test_filemakers_own_line_converts_to_its_step():
    for c in CASES:
        if c["fmLineConverts"]:
            assert _canon(_emit(c["fmLine"])) == _canon(c["fm"]), c["id"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
