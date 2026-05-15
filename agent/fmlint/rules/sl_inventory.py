"""SL-specific FMLint rules (SL001+).

These rules were added by Special-Lite's internal fork of agentic-fm to
catch silent-failure patterns that have bitten our codebase repeatedly.
They are general FM correctness rules — not SL-business-logic-specific —
but they live in this file (rather than in best_practices.py) so they
remain clearly identifiable as fork additions and can be removed cleanly
if upstream ever adopts equivalent checks.

See agentic-fm/SL_FORK.md for fork rationale and divergence ledger.
"""

import re

from ..engine import rule, LintRule
from ..types import Diagnostic, Severity


# Steps that mutate records or change find/layout state in a way that can
# silently fail without dialog suppression. The canonical SL_Core wrapper
# turns Set Error Capture [On] BEFORE these steps and Off AFTER (paired
# with CreateEventObjectFmErrorsOnly). Seeing Set Error Capture [Off]
# immediately followed by one of these is the Pattern A polarity bug.
_RISKY_STEPS = frozenset({
    "Enter Find Mode",
    "Perform Find",
    "New Record/Request",
    "Set Field",
    "Set Field By Name",
    "Open Transaction",
    # NOTE: Commit Transaction is deliberately omitted. The canonical
    # TMPL_NewScript - Transactions tail places Set Error Capture [Off]
    # immediately before Commit Transaction as the final-cleanup pattern
    # (any errors at outer-commit time should surface as FM dialogs, not
    # get captured into an event object). Flagging this would generate
    # false positives on every transactional workflow.
    "Delete Record/Request",
    "Delete All Records",
    "Replace Field Contents",
    "Commit Records/Requests",
    "Insert from URL",
    "Go to Object",
    "Go to Layout",
    "Go to Related Record",
    "Perform JavaScript in Web Viewer",
    "Refresh Portal",
})

# Steps that are part of the canonical closer pattern. If a Set Error
# Capture [Off] is preceded by one of these (within 2 steps), it's the
# legitimate closer — not Pattern A.
_CANONICAL_CLOSER_HINTS = (
    "CreateEventObjectFmErrorsOnly",
)


def _is_set_error_off_xml(step) -> bool:
    if step.get("name", "") != "Set Error Capture":
        return False
    set_el = step.find("Set")
    if set_el is None:
        return False
    return set_el.get("state", "").lower() == "false"


def _is_set_error_off_hr(ln) -> bool:
    if ln.step_name != "Set Error Capture":
        return False
    bracket = (ln.bracket_content or "").lower()
    return "off" in bracket


def _previous_step_is_canonical_closer_xml(steps, idx: int) -> bool:
    """Look at the 1–2 steps immediately preceding `idx` (skipping comments).
    Return True if any of them sets a variable from CreateEventObjectFmErrorsOnly,
    which means the Set Error Capture [Off] at idx is the canonical closer."""
    looked = 0
    j = idx - 1
    while j >= 0 and looked < 2:
        name = steps[j].get("name", "")
        if name == "# (comment)":
            j -= 1
            continue
        looked += 1
        if name == "Set Variable":
            for calc in steps[j].iter("Calculation"):
                if calc.text and any(h in calc.text for h in _CANONICAL_CLOSER_HINTS):
                    return True
        j -= 1
    return False


def _next_step_xml(steps, idx: int):
    """Return the next non-comment step after `idx`, or None."""
    for j in range(idx + 1, len(steps)):
        if steps[j].get("name", "") != "# (comment)":
            return steps[j], j
    return None, -1


def _previous_step_is_canonical_closer_hr(lines, idx: int) -> bool:
    looked = 0
    j = idx - 1
    while j >= 0 and looked < 2:
        if lines[j].step_name == "# (comment)":
            j -= 1
            continue
        looked += 1
        if lines[j].step_name == "Set Variable":
            content = (lines[j].bracket_content or "") + " " + (lines[j].raw or "")
            if any(h in content for h in _CANONICAL_CLOSER_HINTS):
                return True
        j -= 1
    return False


def _next_line_hr(lines, idx: int):
    for j in range(idx + 1, len(lines)):
        if lines[j].step_name != "# (comment)":
            return lines[j], j
    return None, -1


# ---------------------------------------------------------------------------
# SL001 — set-error-capture-off-before-risky-step (Pattern A)
# ---------------------------------------------------------------------------

@rule
class SetErrorCaptureOffBeforeRiskyStep(LintRule):
    """Flag Set Error Capture [Off] that precedes a record-mutating step.

    The SL_Core canonical wrapper turns error capture **On** BEFORE the
    risky step (so FM errors are suppressed and captured into an event
    object) and Off AFTER (paired with CreateEventObjectFmErrorsOnly).
    Seeing Off→risky-step is almost always a polarity bug — the developer
    typed Off where they meant On. With Off, FM throws its user-facing
    dialog instead of capturing the error, AND the downstream
    CreateEventObjectFmErrorsOnly call may not capture cleanly.

    This bug has bitten the SL Inventory codebase 3+ times (PR 5 audit
    2026-05-14; current audit 2026-05-15 at scripts 1041 and 958). The
    rule catches it deterministically.
    """

    rule_id = "SL001"
    name = "set-error-capture-off-before-risky-step"
    category = "sl_fork"
    default_severity = Severity.ERROR
    formats = {"xml", "hr"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []

        sev = self.severity(config)
        diagnostics = []
        steps = parse_result.steps

        for idx, step in enumerate(steps):
            if not _is_set_error_off_xml(step):
                continue
            # If the previous non-comment step looks like the canonical
            # closer (Set Variable from CreateEventObjectFmErrorsOnly),
            # this Off is the closer — not Pattern A.
            if _previous_step_is_canonical_closer_xml(steps, idx):
                continue
            # Look at the next non-comment step.
            next_step, next_idx = _next_step_xml(steps, idx)
            if next_step is None:
                continue
            next_name = next_step.get("name", "")
            if next_name in _RISKY_STEPS:
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        f"Set Error Capture [Off] is immediately followed by "
                        f"'{next_name}'. This looks like a polarity bug "
                        f"(Pattern A) — the wrapper should open with "
                        f"Set Error Capture [On] before the risky step, "
                        f"not [Off]. With [Off], FM throws its dialog AND "
                        f"the downstream CreateEventObjectFmErrorsOnly call "
                        f"may not capture cleanly."
                    ),
                    line=idx + 1,
                    fix_hint=(
                        "Change the Set Error Capture state from Off to On. "
                        "The Off step should appear AFTER the risky step "
                        "(paired with Set Variable [ $eventObjectFmErrorsOnly ; "
                        "CreateEventObjectFmErrorsOnly ]) as the canonical closer."
                    ),
                ))

        return diagnostics

    def check_hr(self, lines, catalog, context, config):
        sev = self.severity(config)
        diagnostics = []

        for idx, ln in enumerate(lines):
            if not _is_set_error_off_hr(ln):
                continue
            if _previous_step_is_canonical_closer_hr(lines, idx):
                continue
            next_line, _ = _next_line_hr(lines, idx)
            if next_line is None:
                continue
            if next_line.step_name in _RISKY_STEPS:
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        f"Set Error Capture [Off] is immediately followed by "
                        f"'{next_line.step_name}'. This looks like a polarity "
                        f"bug (Pattern A) — should be [On] before the risky step."
                    ),
                    line=ln.line_number,
                    fix_hint=(
                        "Change the Set Error Capture state from Off to On. "
                        "The Off step should appear AFTER the risky step as "
                        "the canonical closer (paired with CreateEventObjectFmErrorsOnly)."
                    ),
                ))

        return diagnostics


# ---------------------------------------------------------------------------
# SL002 — psos-self-recursion-empty-parameter
# ---------------------------------------------------------------------------

# A PSOS step that uses Get(ScriptName) as its script reference is the
# canonical self-recursion idiom for transactional workflows. If the
# parameter Calculation is empty/missing, the server-side dispatch runs
# with no parameter and the workflow silently re-runs in a broken state.
# See sl-inventory/docs/patterns/fm-fmxmlsnippet-gotchas.md §5 for the
# full bug history (surfaced 2026-05-05 and 2026-05-06).

_SCRIPT_NAME_RE = re.compile(r"Get\s*\(\s*ScriptName\s*\)", re.IGNORECASE)


@rule
class PsosSelfRecursionEmptyParameter(LintRule):
    """Flag Perform Script on Server with Get(ScriptName) self-reference
    but a missing or empty Parameter calculation.

    Transactional workflow scripts use the canonical PSOS self-recursion
    pattern: "By name" + Get(ScriptName) + $scriptParameterObject (or
    Get(ScriptParameter)) parameter calc. If the parameter is empty, the
    server-side instance receives nothing — every required field appears
    missing on validation, and the workflow surfaces a confusing
    "Required parameter(s) missing..." error from the SERVER side even
    though the client-side caller passed a complete payload.

    This bug has bitten the SL Inventory codebase 2+ times (PR #15
    2026-05-05; PR #19 2026-05-06). Triple-checking the Parameter calc
    is non-empty whenever the PSOS reference is Get(ScriptName) closes
    the silent-failure window for good.
    """

    rule_id = "SL002"
    name = "psos-self-recursion-empty-parameter"
    category = "sl_fork"
    default_severity = Severity.ERROR
    formats = {"xml"}  # PSOS internals are hard to detect reliably from HR
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []

        sev = self.severity(config)
        diagnostics = []

        for idx, step in enumerate(parse_result.steps):
            if step.get("name", "") != "Perform Script on Server":
                continue

            # Detect self-recursion: <Calculated> child whose CDATA matches
            # Get(ScriptName).
            calculated = step.find("Calculated")
            if calculated is None:
                continue  # "From list" mode — different concern, skip
            ref_calc = calculated.find("Calculation")
            if ref_calc is None or ref_calc.text is None:
                continue
            if not _SCRIPT_NAME_RE.search(ref_calc.text):
                continue

            # Self-recursion confirmed. Now check the Parameter Calculation —
            # it's the top-level <Calculation> direct child of <Step>
            # (siblings to <Calculated> and <WaitForCompletion>).
            param_calc = None
            for child in step:
                if child.tag == "Calculation":
                    param_calc = child
                    break

            param_text = (param_calc.text or "").strip() if param_calc is not None else ""

            if not param_text:
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        "Perform Script on Server uses Get(ScriptName) "
                        "(self-recursion idiom) but the Parameter "
                        "calculation is missing or empty. The server-side "
                        "instance will receive no parameter, and the "
                        "workflow will silently re-run with all required "
                        "fields appearing missing. This is a known "
                        "silent-failure foot-gun — see "
                        "sl-inventory/docs/patterns/fm-fmxmlsnippet-gotchas.md §5."
                    ),
                    line=idx + 1,
                    fix_hint=(
                        "Set the Parameter calculation to $scriptParameterObject "
                        "(SL project convention) or Get(ScriptParameter) "
                        "(template default). Both are functionally equivalent "
                        "and either is correct."
                    ),
                ))

        return diagnostics

    def check_hr(self, lines, catalog, context, config):
        # PSOS parameter contents aren't reliably visible in the HR
        # sanitized output (renders as "⚠️ PARAMETER 'Parameter' NOT
        # PARSED ⚠️"), so we skip HR mode. XML mode is authoritative.
        return []


# ---------------------------------------------------------------------------
# SL003 — banned-readme-doc-block
# ---------------------------------------------------------------------------

@rule
class BannedReadmeDocBlock(LintRule):
    """Flag the disabled <Step name="Insert Text"> with <Field>$README</Field>
    pattern that some legacy scripts used for header documentation
    (PARAMETER FORMAT / NOTES / HISTORY duplicates).

    Banned by team convention 2026-05-13 — "no other scripts are documenting
    like this; let the code speak for itself." The legitimate header
    comments (# # PURPOSE / # # NOTES / # # HISTORY / # # TODO) are the
    canonical location.
    """

    rule_id = "SL003"
    name = "banned-readme-doc-block"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        diagnostics = []
        for idx, step in enumerate(parse_result.steps):
            if step.get("name", "") != "Insert Text":
                continue
            if step.get("enable", "True") != "False":
                continue  # only flag the disabled-step variant of the pattern
            field = step.find("Field")
            if field is None or field.text != "$README":
                continue
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Disabled Insert Text step targeting $README — banned "
                    "doc-block pattern (PARAMETER FORMAT / NOTES / HISTORY "
                    "duplicates). Banned by team convention 2026-05-13."
                ),
                line=idx + 1,
                fix_hint=(
                    "Remove the disabled <Step name=\"Insert Text\"> with "
                    "<Field>$README</Field>. Move any genuinely useful "
                    "content into the # # PURPOSE / # # NOTES comment "
                    "block at the top of the script."
                ),
            ))
        return diagnostics

    def check_hr(self, lines, catalog, context, config):
        # HR sanitized output renders the disabled Insert Text as a
        # `// Insert Text [...]` line, which is a strong textual signal.
        sev = self.severity(config)
        diagnostics = []
        for ln in lines:
            raw = (ln.raw or "").strip()
            if raw.startswith("// Insert Text") and "$README" in raw:
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        "Disabled Insert Text step targeting $README — "
                        "banned doc-block pattern. Banned by team "
                        "convention 2026-05-13."
                    ),
                    line=ln.line_number,
                    fix_hint=(
                        "Remove the disabled Insert Text step. Move any "
                        "useful content into the # # PURPOSE / # # NOTES "
                        "comment block at the top of the script."
                    ),
                ))
        return diagnostics


# ---------------------------------------------------------------------------
# SL004 — boilerplate-placeholder
# ---------------------------------------------------------------------------

_BOILERPLATE_RE = re.compile(
    r"YYYY-MM-DD\s+by\s+FName\s+LName",
    re.IGNORECASE,
)


@rule
class BoilerplatePlaceholder(LintRule):
    """Flag the uncleaned template-boilerplate "Modified" placeholder
    line that TMPL_NewScript ships with: "# Modified: YYYY-MM-DD by
    FName LName [REF#]".

    Should be replaced with a real history entry (or deleted) when the
    script is first modified after creation. Bit the 2026-05-15 audit
    where it appeared in 5 scripts that had been edited since creation
    but never had this line cleaned up.
    """

    rule_id = "SL004"
    name = "boilerplate-placeholder"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml", "hr"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        diagnostics = []
        for idx, step in enumerate(parse_result.steps):
            if step.get("name", "") != "# (comment)":
                continue
            text_el = step.find("Text")
            if text_el is None or text_el.text is None:
                continue
            if _BOILERPLATE_RE.search(text_el.text):
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        "Uncleaned template-boilerplate Modified line "
                        "(\"YYYY-MM-DD by FName LName [REF#]\"). Replace "
                        "with a real history entry or delete."
                    ),
                    line=idx + 1,
                    fix_hint=(
                        "Replace this # (comment) step's text with a real "
                        "Modified entry: "
                        "\"Modified: 2026-MM-DD by <name> / Claude — "
                        "<what changed and why>.\""
                    ),
                ))
        return diagnostics

    def check_hr(self, lines, catalog, context, config):
        sev = self.severity(config)
        diagnostics = []
        for ln in lines:
            raw = ln.raw or ""
            if _BOILERPLATE_RE.search(raw):
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        "Uncleaned template-boilerplate Modified line. "
                        "Replace with a real history entry or delete."
                    ),
                    line=ln.line_number,
                    fix_hint=(
                        "Replace with a real Modified entry or remove the "
                        "comment step entirely."
                    ),
                ))
        return diagnostics


# ---------------------------------------------------------------------------
# SL005 — missing-pseudo-loop-markers
# ---------------------------------------------------------------------------

@rule
class MissingPseudoLoopMarkers(LintRule):
    """Flag scripts that have a top-level Loop step but lack the
    canonical BEGIN PSEUDO LOOP / END PSEUDO LOOP marker comments.

    The TMPL_NewScript and TMPL_NewScript - Transactions templates both
    wrap the script body in a "pseudo-loop" — a Loop step that runs once
    and uses Exit Loop If steps for early-exit control flow. The marker
    comment blocks make this structure scannable in Script Workspace:

        # =====================================================
        # BEGIN PSEUDO LOOP
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Loop [ ... ]
           ...body...
        End Loop
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # END PSEUDO LOOP
        # =====================================================

    Missing markers usually means the script is using a Loop but not
    following the template convention (or was hand-rolled before the
    convention existed). Surfaced 2026-05-15 in the RefreshPayload
    rebuild pass.
    """

    rule_id = "SL005"
    name = "missing-pseudo-loop-markers"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)

        # Does the script have any Loop step?
        has_loop = any(s.get("name", "") == "Loop" for s in parse_result.steps)
        if not has_loop:
            return []

        # Search comment text for the BEGIN/END markers
        has_begin_marker = False
        has_end_marker = False
        for step in parse_result.steps:
            if step.get("name", "") != "# (comment)":
                continue
            text_el = step.find("Text")
            if text_el is None or text_el.text is None:
                continue
            txt = text_el.text.strip().upper()
            # Accept the typo "PSUEDO" too — older scripts have it
            if txt.startswith("BEGIN PSEUDO LOOP") or txt.startswith("BEGIN PSUEDO LOOP"):
                has_begin_marker = True
            elif txt.startswith("END PSEUDO LOOP") or txt.startswith("END PSUEDO LOOP"):
                has_end_marker = True

        diagnostics = []
        if not has_begin_marker:
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Script has a Loop step but no \"BEGIN PSEUDO LOOP\" "
                    "marker comment block. The TMPL_NewScript templates "
                    "wrap the pseudo-loop in marker comments for "
                    "scannability."
                ),
                line=0,
                fix_hint=(
                    "Add three # (comment) steps immediately before the "
                    "Loop step: a row of \"=\" characters, the text "
                    "\"BEGIN PSEUDO LOOP\", and a row of \"~\" characters."
                ),
            ))
        if not has_end_marker:
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Script has a Loop step but no \"END PSEUDO LOOP\" "
                    "marker comment block."
                ),
                line=0,
                fix_hint=(
                    "Add three # (comment) steps immediately after the "
                    "matching End Loop step: a row of \"~\" characters, "
                    "the text \"END PSEUDO LOOP\", and a row of \"=\" "
                    "characters."
                ),
            ))
        return diagnostics


# ---------------------------------------------------------------------------
# SL006 — script-top-global-allow-or-capture
# ---------------------------------------------------------------------------

@rule
class ScriptTopGlobalAllowOrCapture(LintRule):
    """Flag Allow User Abort or Set Error Capture steps placed at the
    script top level (before the outer Loop), in scripts that also have
    a Loop. The TMPL_NewScript convention places these steps INSIDE the
    pseudo-loop where they apply to specific risky steps — not as
    "global preamble" before the loop starts.

    Top-level globals were the old hand-rolled pattern from before the
    template existed. Bit the 2026-05-15 RefreshPayload rebuild pass.

    Scripts that have NO Loop (e.g., thin Open*Dialog wrappers like
    INV_OpenNewItemDialog) are exempt — for those, top-level
    Allow User Abort / Set Error Capture is the intentional pattern.
    """

    rule_id = "SL006"
    name = "script-top-global-allow-or-capture"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)

        # Find the index of the first Loop step. If none, the script is
        # a thin wrapper and this rule doesn't apply.
        loop_idx = None
        for idx, step in enumerate(parse_result.steps):
            if step.get("name", "") == "Loop":
                loop_idx = idx
                break
        if loop_idx is None:
            return []

        # Scan steps BEFORE the Loop for Allow User Abort / Set Error Capture
        diagnostics = []
        for idx in range(loop_idx):
            name = parse_result.steps[idx].get("name", "")
            if name in ("Allow User Abort", "Set Error Capture"):
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        f"'{name}' step appears at script top level "
                        f"(before the outer Loop). The TMPL_NewScript "
                        f"templates don't use top-level globals — these "
                        f"steps belong INSIDE the pseudo-loop, paired with "
                        f"specific risky steps that need them."
                    ),
                    line=idx + 1,
                    fix_hint=(
                        f"Remove this top-level {name} step. If a risky "
                        f"step inside the loop needs error suppression, "
                        f"wrap THAT step with a Set Error Capture [On] / "
                        f"CreateEventObjectFmErrorsOnly / [Off] pair."
                    ),
                ))
        return diagnostics


# ---------------------------------------------------------------------------
# SL009 — redundant-else-label
# ---------------------------------------------------------------------------

_ELSE_LABEL_RE = re.compile(r"^//\s*Else\s*$", re.IGNORECASE)


@rule
class RedundantElseLabel(LintRule):
    """Flag a # (comment) step with text "// Else" immediately preceding
    a bare Else step.

    Banned by team convention 2026-05-14 ("the Else step itself is the
    label; the comment adds nothing"). The blank-line-above-Else
    convention (FM script style Rule 1) provides the visual separation
    the // Else label was meant to add.
    """

    rule_id = "SL009"
    name = "redundant-else-label"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        diagnostics = []
        steps = parse_result.steps
        for idx in range(len(steps) - 1):
            cur = steps[idx]
            nxt = steps[idx + 1]
            if cur.get("name", "") != "# (comment)":
                continue
            if nxt.get("name", "") != "Else":
                continue
            text_el = cur.find("Text")
            if text_el is None or text_el.text is None:
                continue
            if _ELSE_LABEL_RE.match(text_el.text.strip()):
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        "Redundant \"// Else\" comment immediately above "
                        "a bare Else step. Banned by team convention "
                        "2026-05-14 — the Else step itself is the label."
                    ),
                    line=idx + 1,
                    fix_hint=(
                        "Remove the # (comment) step. If visual separation "
                        "is wanted, the blank-line-above-Else convention "
                        "(empty # (comment) step) is the canonical way."
                    ),
                ))
        return diagnostics
