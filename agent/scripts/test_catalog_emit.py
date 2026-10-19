#!/usr/bin/env python3
"""test_catalog_emit.py — gate the P6.4 emit engine against the reference fixtures.

``catalog_emit.py`` is a faithful port of the reference HR→fmxmlsnippet emit path and a
line-for-line counterpart of the shipped TS ``catalog-emit.ts``. Since the reference
converter has no SaXML direction (P6.4's defining constraint), the emitter is
gated the same way the TS port was: byte-identity against the committed
``webviewer/test/fixtures/hr-to-xml.json`` — the reference ``/api/hr-to-xml`` output.

For each corpus step: parse the fixture HR into bracket params, run
``match_param_values`` → ``convert_step_with_catalog`` (empty-context resolver → the
``id="0"`` name-only refs the fixtures use), and assert the emitted ``<Step>`` block is
byte-identical to the fixture's XML. Control-flow steps (hand-coded, not the engine's
job) and the 3 FM26-AI grammar-gap steps are excluded — mirroring the TS routing.

Run: ``uvx pytest agent/scripts/test_catalog_emit.py`` (stdlib + pytest only).
"""

from __future__ import annotations

import json
import os
import re

from calc_literal import (
    CALC_CLOSE_BRACKETS,
    CALC_CLOSE_PARENS,
    CALC_OPEN_BRACKETS,
    CALC_OPEN_PARENS,
    hr_bracket_depth_delta,
    hr_calc_opaque_span_at,
    is_unescaped_calc_quote,
)
from catalog_emit import (
    EmptyResolver,
    convert_step_with_catalog,
    esc_xml,
    match_param_values,
)
from catalog_grammar import load_catalog

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_CATALOG = os.path.join(_REPO, "agent", "catalogs", "step-catalog-en.json")
_FIXTURES = os.path.join(_REPO, "webviewer", "test", "fixtures", "hr-to-xml.json")

# Control-flow steps are hand-coded in the converters (the sanctioned exception), not
# rendered by the engine — exactly as catalog-converter.ts routes them to control.ts.
CONTROL_FLOW = {
    "# (comment)", "If", "Else If", "Else", "End If",
    "Loop", "Exit Loop If", "End Loop", "Exit Script", "Set Variable",
}
# The FM26-AI grammar-gap steps stay excluded from every gate (plan §"known gap"):
# FM26 AI steps whose reference fixtures (captured from an earlier FileMaker
# serialization) no longer match the emit engine's FileMaker-26-correct output.
# Configure AI Account is NO LONGER excluded — its wrapper spelling
# (<SetLLMAccout>/<AccoutName> -> <SetLLMAccount>/<AccountName>) and its
# LLMType-governed <VerifySSLCertificates> visibility are now modeled in the
# catalog and its fixture is regenerated, so it round-trips byte-identical
# (verified against live FileMaker 26), mirroring the TS byte-identity gate which
# already includes it.
FM26_AI_SKIP = {
    "Fine-Tune Model",
    "Generate Response from Model",
}


# --- minimal HR line parser (port of parser.ts parseLine + splitParams) ---------
# Text literals and comments follow the calculation grammar (calc_literal.py), exactly
# as parser.ts does: a literal or a comment is stepped over whole; on the last line a
# ``//`` comment ends before the next ``;`` (the one-line form), and a step's own ``]``
# after a trailing comment closes it when nothing else does.
def _find_top_level_bracket(text: str) -> int:
    in_quote = False
    for i, c in enumerate(text):
        if is_unescaped_calc_quote(text, i):
            in_quote = not in_quote
        if not in_quote and c == "[":
            return i
    return -1


def _find_matching_bracket(text: str, open_idx: int) -> int:
    for yields in (False, True):
        depth = 0
        i = open_idx
        while i < len(text):
            span = hr_calc_opaque_span_at(text, i)
            if span:
                end = i + span
                if yields and text[i] == "/" and text[i + 1] == "/":
                    k = end
                    while k > i + 2 and text[k - 1] in " \t":
                        k -= 1
                    if k > i + 2 and text[k - 1] == "]":
                        end = k - 1
                i = end
                continue
            delta = hr_bracket_depth_delta(text, i, depth)
            depth += delta
            if delta < 0 and depth == 0:
                return i
            i += 1
    return -1


def _split_params(content: str) -> list[str]:
    params: list[str] = []
    cur = ""
    paren = bracket = 0
    i = 0
    while i < len(content):
        span = hr_calc_opaque_span_at(content, i, ";")
        if span:
            cur += content[i : i + span]
            i += span
            continue
        c = content[i]
        if c in CALC_OPEN_PARENS:
            paren += 1
        elif c in CALC_CLOSE_PARENS:
            paren -= 1
        elif c in CALC_OPEN_BRACKETS:
            bracket += 1
        elif c in CALC_CLOSE_BRACKETS:
            bracket -= 1
        if c == ";" and paren == 0 and bracket == 0:
            params.append(cur.strip())
            cur = ""
            i += 1
            continue
        cur += c
        i += 1
    if cur.strip():
        params.append(cur.strip())
    elif params:
        params.append("")
    return params


def _parse_hr_line(raw: str) -> tuple[bool, str, list[str]]:
    trimmed = raw.lstrip()
    if not trimmed.strip():
        return (False, "", [])
    if trimmed.startswith("#"):
        return (False, "# (comment)", [])
    disabled = False
    work = trimmed
    if trimmed.startswith("//"):
        disabled = True
        work = trimmed[2:].strip()
    bi = _find_top_level_bracket(work)
    if bi < 0:
        return (disabled, work.strip(), [])
    step_name = work[:bi].strip()
    close = _find_matching_bracket(work, bi)
    content = work[bi + 1 : close] if close >= 0 else work[bi + 1 :]
    return (disabled, step_name, _split_params(content))


def _extract_step_block(xml: str) -> str | None:
    m = re.search(r"^  <Step\b.*?</Step>", xml, re.DOTALL | re.MULTILINE)
    if m:
        return m.group(0)
    m = re.search(r"^  <Step\b[^>]*/>", xml, re.MULTILINE)
    return m.group(0) if m else None


def _emit_self_close(entry, disabled: bool) -> str:
    step_id = entry.id if entry.id is not None else 0
    return (
        '  <Step enable="'
        + ("False" if disabled else "True")
        + f'" id="{step_id}" name="'
        + esc_xml(entry.name)
        + '"/>'
    )


def _load():
    catalog = load_catalog(_CATALOG)
    by_name = {e.name: e for e in catalog}
    for e in catalog:  # trim-tolerant: the one trailing-space name "Configure RAG Account "
        by_name.setdefault(e.name.strip(), e)
    with open(_FIXTURES, encoding="utf-8") as fh:
        fixtures = json.load(fh)
    return by_name, fixtures


def test_emit_byte_identical_to_reference():
    """Every non-control corpus step emits byte-identically to /api/hr-to-xml."""
    by_name, fixtures = _load()
    resolver = EmptyResolver()
    checked = 0
    mismatches: list[str] = []

    for relpath in sorted(fixtures):
        hr = fixtures[relpath]["hr"]
        xml = fixtures[relpath]["xml"]
        disabled, step_name, params = _parse_hr_line(hr)
        if step_name in CONTROL_FLOW or step_name in FM26_AI_SKIP:
            continue
        entry = by_name.get(step_name)
        assert entry is not None, f"{relpath}: no catalog entry for {step_name!r}"

        if entry.self_closing and not entry.params:
            got = _emit_self_close(entry, disabled)
        else:
            values = match_param_values(entry, params)
            got = convert_step_with_catalog(entry, disabled, values, resolver)
        want = _extract_step_block(xml)
        checked += 1
        if got != want:
            mismatches.append(f"{relpath} ({step_name})")

    assert not mismatches, f"{len(mismatches)} emit mismatches: {mismatches[:10]}"
    # Guard against the corpus silently shrinking (203 engine steps at time of writing).
    assert checked >= 200, f"only {checked} engine steps checked — corpus shrank?"


# ---------------------------------------------------------------------------
# Governed-visibility boolean — the hrHidden gate derived on emit.
#
# The facet: an ``hrHidden`` boolean that a sibling's ``visibleWhen`` gates on
# carries no HR token of its own, so HR→XML must DERIVE its state from whether a
# gated companion contributed a token. Letting ``defaultValue`` answer instead
# turns "no stored import order" into "restore the stored order" on every
# round-trip — and FileMaker obeys that flag, discarding what it gated.
#
# The byte-identity gate above cannot catch a regression here: every corpus step
# carrying one of these gates also carries its companion, so the derived value
# and the catalog default agree. Only the companion-absent case separates them.
#
# Mirrors webviewer/test/catalog-emit.engine.test.ts (tests 1-3) — test 4 is
# Python-only because the SaXML reader is.
# ---------------------------------------------------------------------------
def _emit_hr(by_name, hr: str) -> str:
    """HR line -> emitted <Step> block, context-free (the fixtures' id="0" shape)."""
    _disabled, step_name, params = _parse_hr_line(hr)
    entry = by_name[step_name]
    values = match_param_values(entry, params)
    return convert_step_with_catalog(entry, False, values, EmptyResolver())


def test_governed_visibility_derives_gate_open_from_companion():
    """A companion token present => the gate serializes the gate-open value."""
    by_name, _ = _load()
    assert '<Restore state="True"/>' in _emit_hr(
        by_name, "Import Records [ Table: Customers ]")
    assert '<Restore state="True"/>' in _emit_hr(
        by_name, "Export Records [ Export options: CharacterSet=UTF-8 ]")


def test_governed_visibility_derives_gate_closed_not_default_value():
    """No companion token => the closed value, NOT the catalog default of True."""
    by_name, _ = _load()
    assert '<Restore state="False"/>' in _emit_hr(
        by_name, "Import Records [ Import fields: Customers::Name ]")
    assert '<Restore state="False"/>' in _emit_hr(
        by_name, "Export Records [ Create folders: On ]")


def test_governed_visibility_survives_xml_hr_xml_round_trip():
    """The gate carries no HR token — only the companions can preserve it."""
    from snippet_to_hr import snippet_to_hr

    by_name, _ = _load()
    head = ('<fmxmlsnippet type="FMObjectList">'
            '<Step enable="True" id="35" name="Import Records">')
    tail = ('<ImportOptions CharacterSet="UTF-8" method="Add"/>'
            '<Table id="7" name="Customers"/></Step></fmxmlsnippet>')
    for state in ("True", "False"):
        hr = "\n".join(snippet_to_hr(f'{head}<Restore state="{state}"/>{tail}'))
        assert f'<Restore state="{state}"/>' in _emit_hr(by_name, hr), state


def test_hr_hidden_without_gating_sibling_keeps_catalog_default():
    """The derive rule fires ONLY for a gate something actually gates on."""
    by_name, _ = _load()
    out = _emit_hr(
        by_name, "Insert from URL [ Select ; With dialog: Off ; Target: $file ; $url ]")
    assert '<DontEncodeURL state="False"/>' in out


def test_saxml_reading_of_the_gate_wins_over_the_derived_value():
    """A SaXML decoder that read the gate itself keeps its value — the emitter
    derives only into an EMPTY slot.

    ``convert_step_with_catalog`` has two callers in this repo and one in the
    reference: the SaXML reader feeds it a values[] built from a <Step>, where
    the gate may already carry the source's own reading. Export Records' decoder
    sets Restore explicitly; an unconditional derive would throw that away and
    substitute a value derived from HR semantics that never applied here.
    """
    import xml.etree.ElementTree as ET

    from saxml_read import read_saxml_step

    by_name, _ = _load()
    entry = by_name["Export Records"]
    sample = os.path.join(
        _REPO, "agent", "fixtures", "converter", "saxml", "export-records.xml")
    step_el = ET.parse(sample).getroot().find(".//Step")
    assert step_el is not None

    disabled, values, resolver = read_saxml_step(entry, step_el)
    gi = next(i for i, p in enumerate(entry.params) if p.xml_element == "Restore")
    assert values[gi] == "On", "decoder no longer seeds Restore — test premise stale"

    out = convert_step_with_catalog(entry, disabled, values, resolver)
    assert '<Restore state="True"/>' in out

    # And the guard is load-bearing: with the decoder's reading blanked, the
    # derive takes over rather than the slot silently emitting a default.
    values[gi] = ""
    blanked = convert_step_with_catalog(entry, disabled, values, resolver)
    assert '<Restore state="True"/>' in blanked  # this sample DOES carry ExportOptions


# ---------------------------------------------------------------------------
# Cross-file Perform Script (id 1) — the fromFileElement grammar primitive.
#
# FM renders a call into another file as the positional token
# `"NAME" from file: "FILE"`. A script param that opts into the from-file
# grammar (``fromFileElement`` set) must split that infix clause into a
# <FileReference name="FILE"> sibling carrying a
# <UniversalPathList>file:FILE</UniversalPathList> child, emitted BEFORE a
# name-only <Script name="NAME"/>. Live-verified against FileMaker (FM26): FM
# binds the reference by the external-data-source NAME when one exists (the path
# is ignored, no dialog, no regression) and the path preserves the file name
# (`from file: "FILE"`, fail-loud + FM's normal locate prompt) when it does not
# — instead of a name-only reference's silent `from file: ""` drop.
# ---------------------------------------------------------------------------
def test_cross_file_perform_script_emits_filereference_with_universal_path_list():
    by_name, _ = _load()
    out = _emit_hr(
        by_name,
        'Perform Script [ "Sandbox" from file: "QuickStart" ; '
        "Specified: From list ; Parameter: \"hi\" ]",
    )
    # The from-file clause splits off a <FileReference> sibling with a
    # <UniversalPathList>file:NAME</> child (the display name as path — the only
    # value available offline).
    assert '<FileReference name="QuickStart">' in out
    assert "<UniversalPathList>file:QuickStart</UniversalPathList>" in out
    # The <Script> keeps ONLY the name (no ` from file:` swallowed in) and is
    # NOT resolved to a same-file id (the binding lives in the other file).
    assert '<Script name="Sandbox"/>' in out
    assert "from file:" not in out  # clause fully consumed, not leaked into a name


def test_same_file_perform_script_unaffected_by_from_file_grammar():
    by_name, _ = _load()
    out = _emit_hr(by_name, 'Perform Script [ "Sandbox" ; Parameter: "hi" ]')
    # No `from file:` clause => no FileReference; the <Script> is emitted as usual.
    assert "<FileReference" not in out
    assert "<Script " in out


# ---------------------------------------------------------------------------
# Field / variable repetitions — `[N]` in HR, `repetition="N"` in XML.
#
# FileMaker writes a repetition as an attribute on the <Field> element, after
# `id` and before `name` for a field reference (`<Field table="T" id="31"
# repetition="2" name="F"/>`) and on the bare element for a variable
# (`<Field repetition="2">$rep</Field>`), only for N > 1: repetition 1 is
# written as no attribute at all and renders with no subscript. Its Script
# Workspace line shows `T::F[2]` / `$rep[2]`. This holds for step-level field
# and fieldOrVariable params and for the same kinds inside repeatGroup /
# attrGroup entries (Show Custom Dialog inputs, Insert from Device's barcode
# scan source). Shapes captured from FileMaker Pro 26.0.2.
# ---------------------------------------------------------------------------
_SCD_HEAD = (
    '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="87" name="Show Custom Dialog">'
    '<Title><Calculation><![CDATA["T"]]></Calculation></Title>'
    '<Message><Calculation><![CDATA[""]]></Calculation></Message>'
    '<Buttons><Button CommitState="True"><Calculation><![CDATA["OK"]]></Calculation></Button>'
    '<Button CommitState="False"><Calculation><![CDATA["Cancel"]]></Calculation></Button>'
    '<Button CommitState="False"></Button></Buttons><InputFields>'
    '<InputField UsePasswordCharacter="False">'
)
_SCD_TAIL = (
    '</InputField><InputField UsePasswordCharacter="False"><Field table="" id="0" name=""></Field></InputField>'
    '<InputField UsePasswordCharacter="False"><Field table="" id="0" name=""></Field></InputField>'
    "</InputFields></Step></fmxmlsnippet>"
)


def _hr_of(xml: str) -> str:
    from snippet_to_hr import snippet_to_hr

    return "\n".join(snippet_to_hr(xml)).strip()


def test_repetition_renders_as_subscript_in_group_entries():
    """XML -> HR: a repeatGroup / attrGroup entry keeps FileMaker's `[N]`."""
    assert _hr_of(_SCD_HEAD + '<Field repetition="2">$rep</Field>' + _SCD_TAIL) == (
        'Show Custom Dialog [ "T" ; "" ; $rep[2] ]')
    assert _hr_of(
        _SCD_HEAD + '<Field table="Invoices" id="31" repetition="2" name="Description"></Field>'
        + _SCD_TAIL) == 'Show Custom Dialog [ "T" ; "" ; Invoices::Description[2] ]'
    device = _hr_of(
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="161" name="Insert from Device">'
        '<InsertFrom value="Barcode"/><Text/><Field>$out</Field><DeviceOptions>'
        '<ScanFrom type="Field"><Field table="Invoices" id="31" repetition="2" name="Description"></Field>'
        '</ScanFrom><Barcodes types="4194303"/></DeviceOptions></Step></fmxmlsnippet>')
    assert "Scan from: Invoices::Description[2]" in device


def test_repetition_renders_as_subscript_at_step_level():
    """XML -> HR: step-level field and fieldOrVariable params keep `[N]`."""
    assert _hr_of(
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="76" name="Set Field">'
        '<Calculation><![CDATA[1]]></Calculation>'
        '<Field table="Invoices" id="31" repetition="2" name="Description"/></Step></fmxmlsnippet>'
    ) == "Set Field [ Invoices::Description[2] ; 1 ]"
    assert "Target: $rep[2]" in _hr_of(
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="61" name="Insert Text">'
        '<SelectAll state="True"/><Text>x</Text><Field repetition="2">$rep</Field></Step></fmxmlsnippet>')


def test_repetition_one_has_no_subscript():
    """FileMaker never writes repetition="1"; a stray one still renders bare."""
    assert _hr_of(_SCD_HEAD + '<Field repetition="1">$rep</Field>' + _SCD_TAIL) == (
        'Show Custom Dialog [ "T" ; "" ; $rep ]')


def test_repetition_subscript_emits_attribute_in_group_entries():
    """HR -> XML: the subscript leaves the name and becomes repetition="N"."""
    by_name, _ = _load()
    out = _emit_hr(by_name, 'Show Custom Dialog [ "T" ; "" ; $rep[2] ]')
    assert '<Field repetition="2">$rep</Field>' in out
    out = _emit_hr(by_name, 'Show Custom Dialog [ "T" ; "" ; Invoices::Description[2] ]')
    assert '<Field table="Invoices" id="0" repetition="2" name="Description"/>' in out
    out = _emit_hr(
        by_name,
        "Insert from Device [ $out ; Type: Barcode ; Scan from: Invoices::Description[2] ]")
    assert '<Field table="Invoices" id="0" repetition="2" name="Description"/>' in out


def test_repetition_subscript_emits_attribute_at_step_level():
    by_name, _ = _load()
    out = _emit_hr(by_name, "Set Field [ Invoices::Description[2] ; 1 ]")
    assert '<Field table="Invoices" id="0" repetition="2" name="Description"/>' in out
    out = _emit_hr(by_name, 'Insert Text [ Select ; Target: $rep[2] ; "x" ]')
    assert '<Field repetition="2">$rep</Field>' in out
    out = _emit_hr(by_name, "Insert Calculated Result [ Select ; Target: Invoices::Description[2] ; 1 ]")
    assert '<Field table="Invoices" id="0" repetition="2" name="Description"/>' in out


def test_repetition_one_and_malformed_subscripts():
    """`[1]` is the default (no attribute); `[0]` and `[]` are not a repetition."""
    by_name, _ = _load()
    out = _emit_hr(by_name, "Set Field [ Invoices::Description[1] ; 1 ]")
    assert '<Field table="Invoices" id="0" name="Description"/>' in out
    out = _emit_hr(by_name, 'Show Custom Dialog [ "T" ; "" ; $rep[1] ]')
    assert "<Field>$rep</Field>" in out
    for ref in ("$rep[0]", "$rep[]"):
        out = _emit_hr(by_name, 'Insert Text [ Select ; Target: ' + ref + ' ; "x" ]')
        assert "repetition=" not in out, ref


# ---------------------------------------------------------------------------
# Calculated repetitions — `T::F[calc]` in HR.
#
# Authored in FileMaker Pro 26.0.2's own Specify Field / Specify Target dialogs
# (Set Field, Go to Field, Insert Text field and variable targets) and copied
# out: a calculated repetition is repetition="0" on the <Field> plus a sibling
# <Repetition><Calculation> right after it; the Script Workspace line reads
# `T::F[$i]`, the calc verbatim (brackets, semicolons and quotes included).
# A Show Custom Dialog input's target dialog takes digits only, so a group entry
# has no calculated form. Mirrors the C++ FieldOps_CalcRepetition_* tests.
# ---------------------------------------------------------------------------
_FM_CALC_REP = (
    '<fmxmlsnippet type="FMObjectList">'
    '<Step enable="True" id="76" name="Set Field"><Calculation><![CDATA["x"]]></Calculation>'
    '<Field table="Invoices" id="31" repetition="0" name="Description"></Field>'
    '<Repetition><Calculation><![CDATA[Let ( [ a = "]" ; b = 2 ] ; b )]]></Calculation></Repetition></Step>'
    '<Step enable="True" id="61" name="Insert Text"><SelectAll state="False"></SelectAll><Text>t</Text>'
    '<Field repetition="0">$v</Field>'
    '<Repetition><Calculation><![CDATA[$i + 1]]></Calculation></Repetition></Step>'
    '<Step enable="True" id="17" name="Go to Field"><SelectAll state="False"></SelectAll>'
    '<Field table="Invoices" id="31" repetition="0" name="Description"></Field>'
    '<Repetition><Calculation><![CDATA[$k // ] ;]]></Calculation></Repetition></Step>'
    "</fmxmlsnippet>"
)


def test_calculated_repetition_renders_as_subscript():
    hr = _hr_of(_FM_CALC_REP).split("\n")
    assert hr[0] == 'Set Field [ Invoices::Description[Let ( [ a = "]" ; b = 2 ] ; b )] ; "x" ]'
    assert "Target: $v[$i + 1]" in hr[1]
    # A `//` comment holding `]` or `;` breaks the line before the closing `]`.
    assert hr[2] == "Go to Field [ Invoices::Description[$k // ] ;"
    assert hr[3] == "] ]"


def test_calculated_repetition_emits_filemaker_form():
    by_name, _ = _load()
    out = _emit_hr(by_name, 'Set Field [ Invoices::Description[Let ( [ a = "]" ; b = 2 ] ; b )] ; "x" ]')
    assert (
        '<Field table="Invoices" id="0" repetition="0" name="Description"/>\n'
        "    <Repetition>\n"
        '      <Calculation><![CDATA[Let ( [ a = "]" ; b = 2 ] ; b )]]></Calculation>\n'
        "    </Repetition>"
    ) in out
    out = _emit_hr(by_name, 'Insert Text [ Target: $v[$i + 1] ; "t" ]')
    assert (
        '<Field repetition="0">$v</Field>\n'
        "    <Repetition>\n"
        "      <Calculation><![CDATA[$i + 1]]></Calculation>\n"
        "    </Repetition>"
    ) in out
    # A digits-only subscript stays the literal form.
    out = _emit_hr(by_name, "Set Field [ Invoices::Description[ 2 ] ; 1 ]")
    assert 'repetition="2" name="Description"' in out and "<Repetition>" not in out
    # A group entry has no calculated form: the subscript is dropped.
    out = _emit_hr(by_name, 'Show Custom Dialog [ "T" ; "" ; $rep[$i] ]')
    assert "<Field>$rep</Field>" in out and "<Repetition>" not in out


def _saxml_target(inner: str) -> str:
    return (
        '<Step index="0" id="76" name="Set Field" enable="True"><UUID>X</UUID><ParameterValues membercount="2">'
        '<Parameter type="FieldReference">' + inner + '</Parameter>'
        '<Parameter type="Calculation"><Calculation datatype="1" position="0"><Calculation>'
        "<Text><![CDATA[1]]></Text></Calculation></Calculation></Parameter></ParameterValues></Step>"
    )


def _saxml_rep(text: str) -> str:
    return ('<repetition><Calculation datatype="1" position="10"><Calculation>'
            "<Text><![CDATA[" + text + "]]></Text></Calculation></Calculation></repetition>")


def test_saxml_reference_repetition_becomes_attribute():
    """SaXML -> XML: every SaXML field/variable reference carries a <repetition>
    calc; a literal N > 1 becomes repetition="N", 1 becomes nothing."""
    import xml.etree.ElementTree as ET

    from saxml_read import read_saxml_step

    by_name, _ = _load()
    entry = by_name["Set Field"]
    tor = '<TableOccurrenceReference id="1" name="Invoices" UUID="T"/>'
    for text, want in (("2", ' repetition="2"'), ("1", "")):
        step = ET.fromstring(_saxml_target(
            '<FieldReference id="31" name="Description" UUID="F">' + _saxml_rep(text) + tor
            + "</FieldReference>"))
        disabled, values, resolver = read_saxml_step(entry, step)
        out = convert_step_with_catalog(entry, disabled, values, resolver)
        assert '<Field table="Invoices" id="31"' + want + ' name="Description"/>' in out, text


def test_saxml_calculated_repetition_becomes_calculated_form():
    """SaXML -> XML: a computed repetition becomes repetition="0" plus the
    <Repetition><Calculation> sibling (FileMaker's own fmxmlsnippet form)."""
    import xml.etree.ElementTree as ET

    from saxml_read import read_saxml_step

    by_name, _ = _load()
    entry = by_name["Set Field"]
    step = ET.fromstring(_saxml_target(
        '<FieldReference id="31" name="Description" UUID="F">' + _saxml_rep("$i")
        + '<TableOccurrenceReference id="1" name="Invoices" UUID="T"/></FieldReference>'))
    disabled, values, resolver = read_saxml_step(entry, step)
    out = convert_step_with_catalog(entry, disabled, values, resolver)
    assert (
        '<Field table="Invoices" id="31" repetition="0" name="Description"/>\n'
        "    <Repetition>\n"
        "      <Calculation><![CDATA[$i]]></Calculation>\n"
        "    </Repetition>"
    ) in out


# ---------------------------------------------------------------------------
# Calculation text literals inside split values.
#
# The parametersList / attrGroup / repeatGroup / fieldList splitters, the HR line
# parser and the group-value quoting all read a text literal with the calculation
# literal grammar (calc_literal.py): ", “ and ” are interchangeable delimiters, a
# backslash escapes any of them, and a doubled delimiter is content. Measured in
# FileMaker Pro 26.0.2, which also stores a pasted doubled quote as \" and a pasted
# curly-delimited literal with straight quotes, so FileMaker's own copy-back is full
# of \" that a doubled-quote-only splitter cuts in half.
#
# Mirrors webviewer/test/catalog-emit.engine.test.ts ("calculation text literals").
# ---------------------------------------------------------------------------
_LQ, _RQ = "\u201c", "\u201d"
_PJS = 'Perform JavaScript in Web Viewer [ Object Name: "wv" ; Function Name: "f" ; Parameters: '


def _xml_hr(step_xml: str) -> str:
    from snippet_to_hr import snippet_to_hr

    return "\n".join(snippet_to_hr('<fmxmlsnippet type="FMObjectList">' + step_xml + "</fmxmlsnippet>"))


def test_parameters_split_honours_backslash_escape_and_curly_delimiters():
    by_name, _ = _load()
    out = _emit_hr(by_name, _PJS + '"a\\", b", ' + _LQ + "c, d" + _RQ + ', "e" ]')
    assert '<Parameters Count="3">' in out
    assert 'CDATA["a\\", b"]' in out
    assert "CDATA[" + _LQ + "c, d" + _RQ + "]" in out
    assert 'CDATA["e"]' in out


def test_parameters_split_follows_the_literal_grammar_table():
    by_name, _ = _load()
    cases = [
        ('"a""b, c"', 1),                                         # doubled escape
        ('"a\\\\", b', 2),                                        # \\ then a closing quote
        ('"x' + _LQ + ' & Upper ( "y, z" ) & ' + _RQ + 'z"', 1),  # mixed families pair
        ('"a\\nb, c", $d', 2),                                    # \ before n is content
        ("\u2018a, b\u2019", 2),                                  # ‘ ’ are not delimiters
    ]
    for params, count in cases:
        out = _emit_hr(by_name, _PJS + params + " ]")
        assert f'<Parameters Count="{count}">' in out, params


def test_semicolon_and_bracket_inside_literals_stay_in_their_param():
    by_name, _ = _load()
    out = _emit_hr(by_name, 'Show Custom Dialog [ "T\\" ; x ] y" ; ' + _LQ + "M ; ] z" + _RQ + " ]")
    assert '<Title>\n      <Calculation><![CDATA["T\\" ; x ] y"]]>' in out
    assert "<Message>\n      <Calculation><![CDATA[" + _LQ + "M ; ] z" + _RQ + "]]>" in out


def test_filemaker_copy_back_with_escaped_quote_round_trips():
    by_name, _ = _load()
    # FileMaker 26.0.2's own copy-back of the three-parameter step.
    step = (
        '<Step enable="True" id="175" name="Perform JavaScript in Web Viewer">'
        '<ObjectName><Calculation><![CDATA["wv"]]></Calculation></ObjectName>'
        '<FunctionName><Calculation><![CDATA["f"]]></Calculation></FunctionName>'
        '<Parameters Count="3">'
        '<P><Calculation><![CDATA["a\\", b"]]></Calculation></P>'
        '<P><Calculation><![CDATA["c, d"]]></Calculation></P>'
        '<P><Calculation><![CDATA["e"]]></Calculation></P>'
        "</Parameters></Step>"
    )
    hr = _xml_hr(step)
    assert 'Parameters: "a\\", b", "c, d", "e"' in hr
    back = _emit_hr(by_name, hr)
    assert '<Parameters Count="3">' in back
    assert 'CDATA["a\\", b"]' in back
    assert 'CDATA["c, d"]' in back


def test_button_labels_with_escaped_and_curly_quotes_round_trip():
    by_name, _ = _load()
    step = (
        '<Step enable="True" id="87" name="Show Custom Dialog">'
        '<Title><Calculation><![CDATA["T"]]></Calculation></Title>'
        '<Message><Calculation><![CDATA["M"]]></Calculation></Message>'
        '<Buttons><Button CommitState="True"><Calculation><![CDATA["O\\", K"]]></Calculation></Button>'
        '<Button CommitState="False"><Calculation><![CDATA[' + _LQ + "x | y" + _RQ + "]]></Calculation></Button>"
        '<Button CommitState="False"><Calculation><![CDATA["Z"]]></Calculation></Button></Buttons>'
        "</Step>"
    )
    hr = _xml_hr(step)
    assert ('Buttons: commit=True, label="O\\", K" | commit=False, label=' + _LQ + "x | y" + _RQ
            + ' | commit=False, label="Z"') in hr
    back = _emit_hr(by_name, hr)
    assert '<Button CommitState="True">' in back
    assert '<![CDATA["O\\", K"]]>' in back
    assert "<![CDATA[" + _LQ + "x | y" + _RQ + "]]>" in back
    assert '<![CDATA["Z"]]>' in back


def test_quoted_group_text_with_curly_quotes_and_backslashes_round_trips():
    """Execute SQL's literal query is a quoted group text value: the encoder must
    write a calculation literal the splitters read back intact."""
    by_name, _ = _load()
    sqls = [
        "SELECT 'a " + _LQ + "b', c FROM t",       # curly + comma
        "SELECT " + _RQ + "x" + _LQ + " FROM t",   # curly alone forces quoting
        "SELECT 'C:\\' , \"q\" FROM t",           # backslash before a quote
        "SELECT a FROM t WHERE p = 'C:\\dir\\', b",  # ordinary backslashes + comma
        "SELECT a, b FROM t WHERE x = 'end\\'",   # trailing backslash
    ]
    for sql in sqls:
        q = esc_xml(sql)
        step = (
            '<Step enable="True" id="117" name="Execute SQL"><NoInteract state="True"/>'
            '<Profile QueryType="Query" flags="0" password="" UserName="" dsn="" FieldDelimiter=","'
            ' IsPredefined="-1" FieldNameRow="0" DataType="ODBC"><Query>' + q + "</Query></Profile></Step>"
        )
        back = _emit_hr(by_name, _xml_hr(step))
        assert "<Query>" + q + "</Query>" in back, sql
        assert 'FieldDelimiter=","' in back, sql


def test_hr_param_splitter_honours_the_literal_grammar():
    """The FMLint HR parser splits step params with the same grammar."""
    import sys

    repo = os.path.dirname(os.path.dirname(_HERE))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from agent.fmlint.formats.hr_parser import parse_hr

    line = parse_hr('Show Custom Dialog [ "T\\" ; x ] y" ; ' + _LQ + "M ; ] z" + _RQ + " ]")[0]
    assert line.step_name == "Show Custom Dialog"
    assert line.params == ['"T\\" ; x ] y"', _LQ + "M ; ] z" + _RQ]


# --- hrQuoted: a literal text param FileMaker displays quoted ----------------
# FileMaker shows Insert Text's value as `“x”` and never escapes it. The value used
# to render bare, so an odd quote opened a literal that never closed and swallowed
# the step's `]` (and every later line), and `;`/`]` in the text split or ended the
# step. Mirrors tests/test_catalog_converter_text_literal.cpp in the plug-in.
_INSERT_TEXT_VALUES = [
    "x", "He said " + _LQ + "hi", 'He said "hi', _LQ + "x" + _RQ, '"x"', "a\\b", 'a\\"b',
    "a;b ] c", "it's ‘q’", _LQ, '""', "  sp  ", "a\\", "a¶b",
]


def _insert_text_xml(text: str) -> str:
    return (
        '<Step enable="True" id="61" name="Insert Text"><SelectAll state="True"/><Text>'
        + esc_xml(text) + "</Text><Field>$x</Field></Step>"
    )


def test_insert_text_renders_quoted_and_round_trips():
    by_name, _ = _load()
    for text in _INSERT_TEXT_VALUES:
        hr = _xml_hr(_insert_text_xml(text))
        assert hr.startswith("Insert Text [ Select ; Target: $x ; " + _LQ), (text, hr)
        assert "<Text>" + esc_xml(text) + "</Text>" in _emit_hr(by_name, hr), (text, hr)
    # Identical to FileMaker's own line where FileMaker's line is unambiguous.
    assert _xml_hr(_insert_text_xml("a;b ] c")) == (
        "Insert Text [ Select ; Target: $x ; " + _LQ + "a;b ] c" + _RQ + " ]")
    assert _xml_hr(_insert_text_xml("")) == "Insert Text [ Select ; Target: $x ]"


def test_insert_text_accepts_bare_quoted_and_display_forms():
    by_name, _ = _load()
    cases = [
        ("Insert Text [ Target: $x ; hello ]", "hello"),
        ('Insert Text [ Target: $x ; "hello" ]', "hello"),
        ('Insert Text [ Target: $x ; "<a class=\\"b\\">" ]', '<a class="b">'),
        ("Insert Text [ Target: $x ; " + _LQ + '"x"' + _RQ + " ]", '"x"'),
        ('Insert Text [ Select ; Target: $x ; {"sut":"S","cases":[]} ]', '{"sut":"S","cases":[]}'),
    ]
    for hr, text in cases:
        assert "<Text>" + esc_xml(text) + "</Text>" in _emit_hr(by_name, hr), hr


# File paths take the same facet. FileMaker shows every UniversalPathList path
# curly-quoted (`Get File Exists [ “$path” ; Target: $v ]`) and stores `;` as a path
# separator (`file:a;b.txt` copies back as `file:a;file:b.txt`). Rendered bare, the
# `;` split the param, a `]` ended the step and an odd quote swallowed it. Values are
# FileMaker's own copy-back. Mirrors webviewer/test/catalog-emit.engine.test.ts.
def _get_file_exists_xml(xml_value: str) -> str:
    return (
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="188" name="Get File Exists">'
        "<UniversalPathList>" + xml_value + "</UniversalPathList><Text/><Field>$v</Field></Step></fmxmlsnippet>"
    )


def test_file_paths_render_quoted_and_round_trip():
    by_name, _ = _load()
    cases = [
        ("$path", _LQ + "$path" + _RQ, "$path"),
        ("file:a.txt", _LQ + "file:a.txt" + _RQ, "file:a.txt"),
        ("file:a;file:b.txt", _LQ + "file:a;file:b.txt" + _RQ, "file:a;file:b.txt"),
        ("file:a]b.txt", _LQ + "file:a]b.txt" + _RQ, "file:a]b.txt"),
        ("file:a &quot;b&quot;.txt", _LQ + 'file:a \\"b\\".txt' + _RQ, "file:a &quot;b&quot;.txt"),
        ("file:a " + _LQ + "b.txt", _LQ + "file:a \\" + _LQ + "b.txt" + _RQ, "file:a " + _LQ + "b.txt"),
        # A return is written back as FileMaker writes it (``&#13;``; a raw CR pastes as LF).
        ("file:a.txt&#13;file:b.txt", _LQ + "file:a.txt\rfile:b.txt" + _RQ, "file:a.txt&#13;file:b.txt"),
        ("file:a.txt&#10;file:b.txt", _LQ + "file:a.txt\nfile:b.txt" + _RQ, "file:a.txt\nfile:b.txt"),
    ]
    for xml_value, token, emitted in cases:
        hr = _hr_of(_get_file_exists_xml(xml_value))
        assert hr == "Get File Exists [ " + token + " ; Target: $v ]", (xml_value, hr)
        out = _emit_hr(by_name, hr)
        assert "<UniversalPathList>" + emitted + "</UniversalPathList>" in out, (xml_value, out)
        assert "<Field>$v</Field>" in out, out


def test_carriage_return_in_text_value_emits_char_ref():
    """A CR in a text value is written ``&#13;``, FileMaker's own copy-back: XML
    parsing turns a raw CR into LF, so a raw CR pasted back holding LF (FileMaker Pro
    26.0.2). An LF value stays raw. Mirrors webviewer/test/catalog-emit.engine.test.ts."""
    by_name, _ = _load()
    send_event = (
        '<Step enable="True" id="57" name="Send Event"><ContentType value="Text"/><Text>a{0}b</Text>'
        '<Event CopyResultToClipboard="False" WaitForCompletion="True" BringTargetToForeground="False" '
        'TargetType="NUTD" TargetName="&lt;unknown&gt;" id="dosc" class="misc"/></Step>'
    )
    shapes = [
        ('<Step enable="True" id="61" name="Insert Text"><SelectAll state="True"/>'
         "<Text>line1{0}line2</Text><Field>$v</Field></Step>", "<Text>line1{0}line2</Text>"),
        ('<Step enable="True" id="188" name="Get File Exists"><UniversalPathList>file:a.txt{0}file:b.txt'
         "</UniversalPathList><Text/><Field>$v</Field></Step>", "<UniversalPathList>file:a.txt{0}file:b.txt</UniversalPathList>"),
        (send_event, "<Text>a{0}b</Text>"),
    ]
    for step, want in shapes:
        out = _emit_hr(by_name, _hr_of('<fmxmlsnippet type="FMObjectList">' + step.format("&#13;") + "</fmxmlsnippet>"))
        assert want.format("&#13;") in out, out
        assert "\r" not in out, out
        out = _emit_hr(by_name, _hr_of('<fmxmlsnippet type="FMObjectList">' + step.format("&#10;") + "</fmxmlsnippet>"))
        assert want.format("\n") in out, out


def test_saxml_comment_with_return_emits_char_ref():
    """SaXML -> XML: a comment's return (``&#13;`` in its ``value`` attribute) is
    written back ``&#13;``, not a raw CR that a paste reads as LF."""
    import xml.etree.ElementTree as ET

    from fm_xml_to_snippet import tx_comment

    step = ET.fromstring(
        '<Step enable="True" id="89" name="# (comment)"><ParameterValues>'
        '<Parameter type="Comment"><Comment value="a&#13;b &amp; c"/></Parameter>'
        "</ParameterValues></Step>"
    )
    out = tx_comment(step)
    assert "<Text>a&#13;b &amp; c</Text>" in out, out
    assert "\r" not in out, out


def test_file_path_reads_filemakers_quoted_line_unquoted():
    by_name, _ = _load()
    cases = [
        ("Get File Exists [ " + _LQ + "a.txt" + _RQ + " ; Target: $v ]", "a.txt"),
        ("Get File Exists [ " + _LQ + "$path" + _RQ + " ; Target: $v ]", "$path"),
        ('Get File Exists [ "$path" ; Target: $v ]', "$path"),
        ("Get File Exists [ $path ; Target: $v ]", "$path"),
        ("Delete File [ Target file: " + _LQ + "a.txt" + _RQ + " ]", "a.txt"),
    ]
    for hr, path in cases:
        assert "<UniversalPathList>" + path + "</UniversalPathList>" in _emit_hr(by_name, hr), hr


def test_send_event_text_renders_quoted():
    by_name, _ = _load()
    xml = (
        '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="57" name="Send Event">'
        '<ContentType value="Text"/><Text>open a;b ] c</Text><Event CopyResultToClipboard="False" '
        'WaitForCompletion="True" BringTargetToForeground="False" TargetType="NUTD" '
        'TargetName="&lt;unknown&gt;" id="dosc" class="misc"/></Step></fmxmlsnippet>'
    )
    hr = _hr_of(xml)
    assert "Send Event [ Text ; " + _LQ + "open a;b ] c" + _RQ + " ; Event: " in hr, hr
    assert "<Text>open a;b ] c</Text>" in _emit_hr(by_name, hr)


def test_saxml_insert_text_with_quotes_keeps_its_value(tmp_path):
    """The SaXML reader hands an hrQuoted value to the emitter as the escaped
    literal the HR renderer writes, so a value holding quotes is not unwrapped."""
    from pathlib import Path

    from fm_xml_to_snippet import translate_script

    for raw, want in [("&quot;x&quot;", "&quot;x&quot;"), ("He said " + _LQ + "hi", "He said " + _LQ + "hi")]:
        src = Path(tmp_path) / "insert-text.xml"
        src.write_text(
            '<?xml version="1.0"?><Script><ObjectList><Step id="61" name="Insert Text" enable="True">'
            '<ParameterValues membercount="2"><Parameter type="Boolean">'
            '<Boolean type="Select" id="4096" value="True" /></Parameter>'
            '<Parameter type="Text"><Text value="' + raw + '" /></Parameter>'
            "</ParameterValues></Step></ObjectList></Script>",
            encoding="utf-8",
        )
        out = translate_script(src)
        assert "<Text>" + want + "</Text>" in out, out
