#!/usr/bin/env python3
"""test_catalog_grammar_engine.py — P6.2 offline regression net for the XML→HR
grammar engine in catalog_grammar.py.

These tests lock the *port's behaviour* (that it faithfully applies the catalog
grammar the reference converter defines) so a later edit can't silently regress
it. They are NOT the acceptance oracle — HR-label presentation is arbitrary per
step and only live FileMaker adjudicates final correctness (see the plan's P6.2
[live] criteria). The expected strings here were derived from the reference
semantics and the corpus fixtures, then spot-checked.

Runs under pytest, or standalone: python3 agent/scripts/test_catalog_grammar_engine.py
"""

import glob
import os
import xml.etree.ElementTree as ET

import catalog_grammar as G

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
CATALOG = os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json")
CORPUS = os.path.join(REPO_ROOT, "agent", "snippet_examples", "steps")

ENTRIES = {e.name: e for e in G.load_catalog(CATALOG)}


def _load_step(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    end = text.find("</fmxmlsnippet>")
    if end != -1:
        text = text[: end + len("</fmxmlsnippet>")]
    return ET.fromstring(text).find("Step")


def _render(step_name):
    matches = glob.glob(
        os.path.join(CORPUS, "**", step_name + ".xml"), recursive=True
    )
    assert matches, f"corpus fixture missing for {step_name!r}"
    step = _load_step(matches[0])
    return G.render_step_hr(ENTRIES[step_name], step)


def test_engine_renders_whole_corpus_without_error():
    n = 0
    for path in glob.glob(os.path.join(CORPUS, "**", "*.xml"), recursive=True):
        step = _load_step(path)
        if step is None:
            continue
        name = step.get("name", "")
        entry = ENTRIES.get(name)
        assert entry is not None, f"no catalog entry for {name!r}"
        G.render_step_hr(entry, step)  # must not raise for any of the 216
        n += 1
    assert n == 216, f"expected 216 corpus steps, rendered {n}"


def test_inverted_hr_boolean():
    # invertedHr + state="False" → "On" (the current crude renderer shows "Off").
    assert _render("Change Password") == (
        'Change Password [ Old Password: "old" ; Password: "new" '
        "; With dialog: On ]"
    )


def test_omit_when_empty_calculation_is_labeled():
    # A `calculation` param carrying omitWhenEmpty renders WITH its hrLabel (the
    # File ID catalog fix — FM shows "File ID: $fileID", not bare "$fileID").
    assert _render("Close Data File") == "Close Data File [ File ID: $fileID ]"
    assert "File ID: $fileID" in _render("Get Data File Position")


def test_plain_calculation_renders_bare():
    # A `calculation` param WITHOUT omitWhenEmpty renders bare (Read from Data
    # File's Amount stays labeled via omitWhenEmpty; its target field is positional).
    out = _render("Read from Data File")
    assert out.startswith("Read from Data File [ File ID: $fileID ; Amount (bytes): 1024")


def test_parent_element_descent_reveals_nested_named_calcs():
    # namedCalc params nested under parentElement=SetLLMAccout must be read.
    out = _render("Configure AI Account")
    assert 'Account Name: "account_name"' in out
    assert 'API key: "api_key"' in out
    # ChatGPT is the value FileMaker WRITES; OpenAI is the label it DISPLAYS, and
    # the label is what belongs in HR (measured against FileMaker 26.0.1's Script
    # Workspace). This assertion read "ChatGPT" while the catalog held the label
    # in `enumValues` with no `hrEnumValues` to map it.
    assert "Model Provider: OpenAI" in out


def test_empty_reference_token_omitted():
    # An empty script reference contributes no token (current renderer emits '""').
    out = _render("Install OnTimer Script")
    assert '""' not in out
    assert out.startswith("Install OnTimer Script [ Interval:")


def test_flag_boolean_normalized_to_boolean():
    # The catalog's `flagBoolean` type is normalized to `boolean` at load (the
    # engine only has a `boolean` branch); the raw type is preserved. Regression
    # guard for the bug where flagBoolean params rendered as nothing.
    entry = ENTRIES["Paste"]
    flags = [p for p in entry.params if p.raw.get("type") == "flagBoolean"]
    assert flags, "Paste should carry flagBoolean params"
    assert all(p.type == "boolean" for p in flags)


def test_flag_boolean_params_render():
    assert _render("Paste") == (
        "Paste [ Select: On ; No style: On ; Link if available: Off ; <Table Missing> ]"
    )


def test_governing_discriminator_reveals_companions():
    # Close Window Window="ByName" reveals its companions; the empty Name calc is
    # dropped and the flagStyle "Current file" toggle surfaces.
    assert _render("Close Window") == "Close Window [ Current file ]"


def test_hr_param_order_stable_without_hrslot():
    entry = ENTRIES["Change Password"]
    assert G.hr_param_order(entry) == list(range(len(entry.params)))


def test_param_key_used_for_discriminator_reveal():
    # A namedCalc revealed by a discriminator is keyed by its wrapperElement.
    entry = ENTRIES["Close Window"]
    name_param = next(p for p in entry.params if p.wrapper_element == "Name")
    assert G.param_key(name_param) == "Name"


def _render_xml(step_xml):
    step = ET.fromstring(step_xml)
    return G.render_step_hr(ENTRIES[step.get("name")], step)


def test_empty_leading_positional_keeps_its_slot():
    # A bare field param before a rendered positional token keeps its slot, so the
    # value is not read back into the field's slot. An empty <Field> fills it with
    # FileMaker's own `<Table Missing>`.
    assert _render("Set Next Serial Value") == "Set Next Serial Value [ <Table Missing> ; 100 ]"
    # A governed companion (revealed by a discriminator) is positional too.
    assert _render("Replace Field Contents") == (
        'Replace Field Contents [ With dialog: Off ; <Table Missing> ; "value" ]'
    )


def test_trailing_empty_positional_is_dropped():
    out = _render_xml(
        '<Step enable="True" id="76" name="Set Field">'
        "<Calculation><![CDATA[]]></Calculation>"
        '<Field table="Invoices" id="1" name="ID"/></Step>'
    )
    assert out == "Set Field [ Invoices::ID ]"


if __name__ == "__main__":
    test_engine_renders_whole_corpus_without_error()
    test_inverted_hr_boolean()
    test_omit_when_empty_calculation_is_labeled()
    test_plain_calculation_renders_bare()
    test_parent_element_descent_reveals_nested_named_calcs()
    test_empty_reference_token_omitted()
    test_flag_boolean_normalized_to_boolean()
    test_flag_boolean_params_render()
    test_governing_discriminator_reveals_companions()
    test_hr_param_order_stable_without_hrslot()
    test_param_key_used_for_discriminator_reveal()
    test_empty_leading_positional_keeps_its_slot()
    test_trailing_empty_positional_is_dropped()
    print("all catalog_grammar engine tests passed")
