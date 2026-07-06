"""Tests for param-fidelity rules X001–X003.

Two layers:
1. Synthetic cases — one per known silent-discard bug (must fire) and the
   corrected form of each (must be clean).
2. Corpus smoke test — every reference example in agent/snippet_examples/
   must produce zero X-family diagnostics (the corpus is ground truth; a
   false positive there means the rule is miscalibrated).

Run:  python3 -m unittest agent.fmlint.tests.test_param_fidelity
"""

import unittest
from pathlib import Path

from ..engine import LintRunner

REPO_ROOT = Path(__file__).resolve().parents[3]

WRAP = '<fmxmlsnippet type="FMObjectList">{}</fmxmlsnippet>'


def x_diags(result):
    """Only the param-fidelity family diagnostics."""
    return [d for d in result.diagnostics if d.rule_id.startswith("X")]


class ParamFidelityTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.runner = LintRunner(project_root=REPO_ROOT)

    def lint(self, inner_xml):
        return self.runner.lint(WRAP.format(inner_xml), fmt="xml")

    # -- X001: unknown param element ------------------------------------

    def test_x001_set_error_capture_wrong_element(self):
        # <State> instead of <Set> → error capture silently stays OFF
        result = self.lint(
            '<Step enable="True" id="86" name="Set Error Capture">'
            '<State state="True"/></Step>'
        )
        diags = x_diags(result)
        self.assertTrue(any(d.rule_id == "X001" and "<State>" in d.message.replace("&lt;", "<")
                            or d.rule_id == "X001" and "State" in d.message
                            for d in diags),
                        f"expected X001 for <State>, got: {[d.message for d in diags]}")

    def test_x001_go_to_record_wrong_elements(self):
        # <Option>/<ExitAfterLast> instead of <RowPageLocation>/<Exit>
        result = self.lint(
            '<Step enable="True" id="16" name="Go to Record/Request/Page">'
            '<Option value="First"/><ExitAfterLast state="True"/></Step>'
        )
        diags = [d for d in x_diags(result) if d.rule_id == "X001"]
        self.assertEqual(len(diags), 2,
                         f"expected 2 X001 diags, got: {[d.message for d in diags]}")

    def test_x001_clean_forms_pass(self):
        result = self.lint(
            '<Step enable="True" id="86" name="Set Error Capture">'
            '<Set state="True"/></Step>'
            '<Step enable="True" id="16" name="Go to Record/Request/Page">'
            '<NoInteract state="True"/><Exit state="True"/>'
            '<RowPageLocation value="Next"/></Step>'
        )
        self.assertEqual(x_diags(result), [],
                         f"clean steps flagged: {[d.message for d in x_diags(result)]}")

    # -- X002: missing discriminator -------------------------------------

    def test_x002_go_to_layout_without_destination(self):
        result = self.lint(
            '<Step enable="True" id="6" name="Go to Layout">'
            '<Layout id="28" name="Clientes"></Layout></Step>'
        )
        diags = [d for d in x_diags(result) if d.rule_id == "X002"]
        self.assertEqual(len(diags), 1,
                         f"expected 1 X002 diag, got: {[d.message for d in x_diags(result)]}")

    def test_x002_go_to_layout_with_destination_passes(self):
        result = self.lint(
            '<Step enable="True" id="6" name="Go to Layout">'
            '<LayoutDestination value="SelectedLayout"/>'
            '<Layout id="28" name="Clientes"></Layout></Step>'
        )
        self.assertEqual([d for d in x_diags(result) if d.rule_id == "X002"], [])

    # -- X003: known silent-discard patterns ------------------------------

    def test_x003_card_without_styles_bitmask(self):
        result = self.lint(
            '<Step enable="True" id="122" name="New Window">'
            '<NewWndStyles DimParentWindow="Yes" Toolbars="No" MenuBar="No" '
            'Style="Card" Close="Yes" Minimize="No" Maximize="No" Resize="No"/>'
            '</Step>'
        )
        diags = [d for d in x_diags(result) if d.rule_id == "X003"]
        self.assertEqual(len(diags), 1,
                         f"expected 1 X003 diag, got: {[d.message for d in x_diags(result)]}")

    def test_x003_card_with_styles_bitmask_passes(self):
        result = self.lint(
            '<Step enable="True" id="122" name="New Window">'
            '<NewWndStyles DimParentWindow="Yes" Toolbars="No" MenuBar="No" '
            'Style="Card" Close="Yes" Minimize="No" Maximize="No" Resize="No" '
            'Styles="3222339600"/></Step>'
        )
        self.assertEqual([d for d in x_diags(result) if d.rule_id == "X003"], [])

    def test_x003_fileref_nested_inside_script(self):
        result = self.lint(
            '<Step enable="True" id="1" name="Perform Script">'
            '<Calculation><![CDATA[$param]]></Calculation>'
            '<Script id="1363" name="Some Script">'
            '<FileReference id="10" name="Controlador"/></Script></Step>'
        )
        msgs = [d.message for d in x_diags(result) if d.rule_id == "X003"]
        self.assertTrue(any("SIBLING" in m for m in msgs),
                        f"expected nested-FileReference diag, got: {msgs}")

    def test_x003_fileref_without_universal_path_list(self):
        result = self.lint(
            '<Step enable="True" id="1" name="Perform Script">'
            '<FileReference id="10" name="Controlador"/>'
            '<Calculation><![CDATA[$param]]></Calculation>'
            '<Script id="1363" name="Some Script"/></Step>'
        )
        msgs = [d.message for d in x_diags(result) if d.rule_id == "X003"]
        self.assertTrue(any("UniversalPathList" in m for m in msgs),
                        f"expected missing-UniversalPathList diag, got: {msgs}")

    def test_x003_crossfile_correct_form_passes(self):
        result = self.lint(
            '<Step enable="True" id="1" name="Perform Script">'
            '<FileReference id="10" name="Controlador">'
            '<UniversalPathList>file:Borneo-Controller</UniversalPathList>'
            '</FileReference>'
            '<Calculation><![CDATA[$param]]></Calculation>'
            '<Script id="1363" name="Some Script"/></Step>'
        )
        self.assertEqual([d for d in x_diags(result) if d.rule_id == "X003"], [])


class CorpusSmokeTest(unittest.TestCase):
    """Every reference example must be clean of X-family diagnostics."""

    @classmethod
    def setUpClass(cls):
        cls.runner = LintRunner(project_root=REPO_ROOT)

    def test_snippet_examples_corpus_is_clean(self):
        corpus = REPO_ROOT / "agent" / "snippet_examples"
        files = sorted(corpus.rglob("*.xml"))
        self.assertGreater(len(files), 50, "corpus not found or too small")
        failures = []
        for path in files:
            try:
                result = self.runner.lint_file(str(path))
            except Exception as exc:  # malformed reference file — not ours
                failures.append(f"{path.relative_to(corpus)}: lint crashed: {exc}")
                continue
            for d in x_diags(result):
                failures.append(f"{path.relative_to(corpus)}: {d.rule_id} {d.message}")
        self.assertEqual(failures, [],
                         "false positives in reference corpus:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
