#!/usr/bin/env python3
"""test_catalog_grammar.py — P6.1 IR load test.

Parses all catalog entries into the ``catalog_grammar`` IR and asserts that no
parameter is dropped and none carries an unknown type. Runs under pytest, or
standalone: ``python3 agent/scripts/test_catalog_grammar.py``.
"""

import json
import os

from catalog_grammar import (
    ABSENT,
    fm_min_problem,
    fm_min_problems,
    fm_min_version,
    KNOWN_PARAM_TYPES,
    CatalogEntry,
    ListValue,
    Scalar,
    StepInstance,
    load_report,
    param_key,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
CATALOG = os.path.join(REPO_ROOT, "agent", "catalogs", "step-catalog-en.json")
FUNCTION_CATALOG = os.path.join(REPO_ROOT, "agent", "catalogs", "function-catalog.json")


def test_no_params_dropped():
    rep = load_report(CATALOG)
    assert rep.entries == 216, f"expected 216 entries, got {rep.entries}"
    assert rep.dropped == 0, f"{rep.dropped} params dropped on load"


def test_no_unknown_param_types():
    rep = load_report(CATALOG)
    assert not rep.unknown_typed, (
        f"{len(rep.unknown_typed)} params carry a type outside KNOWN_PARAM_TYPES: "
        f"{rep.unknown_typed[:5]}"
    )


def test_every_type_seen_is_declared():
    # Guards the vocabulary: if the catalog introduces a new type, this fails
    # loudly rather than silently classifying it as unknown at conversion time.
    rep = load_report(CATALOG)
    assert not rep.unknown_typed


def test_param_key_rule():
    from catalog_grammar import StepParam

    named = StepParam.from_dict(
        {"xmlElement": "Calculation", "type": "namedCalc", "wrapperElement": "Name"}
    )
    plain = StepParam.from_dict({"xmlElement": "SelectAll", "type": "boolean"})
    assert param_key(named) == "Name"
    assert param_key(plain) == "SelectAll"
    # A namedCalc with no wrapper falls back to its xmlElement.
    bare = StepParam.from_dict({"xmlElement": "Calculation", "type": "namedCalc"})
    assert param_key(bare) == "Calculation"


def test_step_instance_ir_roundtrips_values():
    inst = StepInstance(
        name="Set Variable",
        values={"Name": Scalar("$x"), "Value": ListValue([Scalar("42")])},
    )
    assert inst.name == "Set Variable"
    assert inst.enable is True
    assert isinstance(inst.values["Value"], ListValue)
    assert inst.values.get("Missing", ABSENT) is ABSENT


def test_discriminator_branches_parse():
    entries = [
        CatalogEntry.from_dict(s)
        for s in _load_steps()
        if s["name"] == "Close Window"
    ]
    assert entries, "Close Window should be in the catalog"
    window = next(p for p in entries[0].params if p.xml_element == "Window")
    assert set(window.discriminator_values) == {"ByName", "Current"}
    assert window.discriminator_values["ByName"].reveal == ["Name", "LimitToWindowsOfCurrentFile"]
    assert window.discriminator_values["Current"].hr_token == "Current Window"


def test_element_alias_reads_both_spellings():
    # Configure AI Account (id 212): FileMaker 2025 serialized the wrapper with a
    # misspelling (<SetLLMAccout>/<AccoutName>); FileMaker 26 corrected it to
    # <SetLLMAccount>/<AccountName>. The catalog records the canonical spelling
    # plus the legacy synonyms under `elementAliases`, so the reader must accept
    # either and never drop the account name / endpoint / API key.
    import xml.etree.ElementTree as ET

    from catalog_grammar import load_catalog, render_step_hr

    entry = next(e for e in load_catalog(CATALOG) if e.id == 212)
    assert entry.element_aliases.get("SetLLMAccount") == ["SetLLMAccout"]
    assert entry.element_aliases.get("AccountName") == ["AccoutName"]

    canon = (
        '<Step enable="True" id="212" name="Configure AI Account">'
        '<VerifySSLCertificates state="True"/><LLMType value="Other"/><SetLLMAccount>'
        '<AccountName><Calculation><![CDATA["my_account"]]></Calculation></AccountName>'
        '<Endpoint><Calculation><![CDATA["ep"]]></Calculation></Endpoint>'
        '<AccessAPIKey><Calculation><![CDATA["key"]]></Calculation></AccessAPIKey>'
        "</SetLLMAccount></Step>"
    )
    hr = render_step_hr(entry, ET.fromstring(canon))
    assert '"my_account"' in hr and '"ep"' in hr and '"key"' in hr, hr

    legacy = (
        '<Step enable="True" id="212" name="Configure AI Account">'
        '<LLMType value="ChatGPT"/><SetLLMAccout>'
        '<AccoutName><Calculation><![CDATA["acct"]]></Calculation></AccoutName>'
        '<AccessAPIKey><Calculation><![CDATA["key"]]></Calculation></AccessAPIKey>'
        "</SetLLMAccout></Step>"
    )
    hr2 = render_step_hr(entry, ET.fromstring(legacy))
    assert '"acct"' in hr2 and '"key"' in hr2, hr2


def _load_steps():
    import json

    with open(CATALOG, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["steps"] if isinstance(data, dict) and "steps" in data else data


def test_fm_min_values_read():
    assert fm_min_version({}) is None
    assert fm_min_version({"fmMin": "any", "fmMinSource": "measured"}) == ""
    assert fm_min_version({"fmMin": "21.0", "fmMinSource": "documented"}) == "21.0"
    assert fm_min_version({"fmMin": "26.0.2", "fmMinSource": "measured"}) == "26.0.2"
    declared_unknown = {"fmMin": "unknown", "fmMinSource": "measured"}
    assert fm_min_problem(declared_unknown) is None
    assert fm_min_version(declared_unknown) is None
    for bad in ({"fmMin": 21, "fmMinSource": "measured"},
                {"fmMin": "", "fmMinSource": "measured"},
                {"fmMin": "21.x", "fmMinSource": "measured"},
                {"fmMin": "21.0.0.1", "fmMinSource": "measured"},
                {"fmMin": "ANY", "fmMinSource": "measured"},
                {"fmMin": "Unknown", "fmMinSource": "measured"},
                {"fmMin": "0", "fmMinSource": "measured"},
                {"fmMin": "0.5", "fmMinSource": "measured"},
                {"fmMin": None, "fmMinSource": "measured"},
                {"fmMin": "21.0"},
                {"fmMin": "21.0", "fmMinSource": "guessed"},
                {"fmMinSource": "measured"}):
        assert fm_min_problem(bad), bad
        assert fm_min_version(bad) is None, bad


def test_fm_min_problems_name_step_param_and_value():
    steps = [
        {"name": "Good", "fmMin": "21.0", "fmMinSource": "measured", "params": [
            {"xmlElement": "Mode", "hrLabel": "Mode", "enumValues": ["A", "B"],
             "fmMinValues": {"B": {"fmMin": "22.0", "fmMinSource": "documented"}}}]},
        {"name": "Bad", "fmMin": "soon", "fmMinSource": "measured", "params": [
            {"xmlElement": "Mode", "hrLabel": "Mode", "enumValues": ["A"],
             "fmMinValues": {"Z": {"fmMin": "22.0", "fmMinSource": "measured"}}}]},
    ]
    problems = fm_min_problems(steps)
    assert not [p for p in problems if p.startswith("Good")]
    assert 'Bad: fmMin "soon" is neither "any", "unknown" nor a version' in problems
    assert ("Bad / Mode = Z: fmMinValues names a value that is not in enumValues"
            in problems)
    assert len(problems) == 2


def test_shipped_catalogs_version_data_well_formed():
    with open(CATALOG, encoding="utf-8") as fh:
        assert fm_min_problems(json.load(fh)) == []
    with open(FUNCTION_CATALOG, encoding="utf-8") as fh:
        functions = json.load(fh)["functions"]
    assert [f["name"] for f in functions if fm_min_problem(f)] == []
    names = [f["name"] for f in functions]
    assert len(names) == len(set(n.lower() for n in names)), "duplicate function"



def test_fm_min_reader_tolerates_malformed_data():
    from catalog_grammar import CatalogEntry, StepParam

    p = StepParam.from_dict({"xmlElement": "Mode", "type": "enum", "hrLabel": None,
                             "fmMin": "soon", "fmMinSource": "measured",
                             "fmMinValues": ["not", "a", "map"]})
    assert p.fm_min is None and p.fm_min_source is None and p.fm_min_values == {}
    p = StepParam.from_dict({"xmlElement": "Mode", "type": "enum", "hrLabel": None,
                             "fmMin": "unknown", "fmMinSource": "documented"})
    assert p.fm_min == "unknown" and p.fm_min_source == "documented"
    e = CatalogEntry.from_dict({"name": "S", "fmMin": "21.0", "fmMinSource": "measured"})
    assert e.fm_min == "21.0"
    assert fm_min_problems([{"name": "S", "params": [
        {"xmlElement": "M", "enumValues": ["A"], "fmMinValues": "bad"}]}]) == [
        "S / M: fmMinValues is not an object"]


if __name__ == "__main__":
    test_no_params_dropped()
    test_no_unknown_param_types()
    test_every_type_seen_is_declared()
    test_param_key_rule()
    test_step_instance_ir_roundtrips_values()
    test_discriminator_branches_parse()
    test_element_alias_reads_both_spellings()
    test_fm_min_values_read()
    test_fm_min_problems_name_step_param_and_value()
    test_shipped_catalogs_version_data_well_formed()
    test_fm_min_reader_tolerates_malformed_data()
    print(f"all catalog_grammar tests passed ({len(KNOWN_PARAM_TYPES)} known types)")
