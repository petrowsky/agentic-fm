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


# ---------------------------------------------------------------------------
# SL010 — pjsiwv-must-be-wrapped
# ---------------------------------------------------------------------------

@rule
class PjsiwvMustBeWrapped(LintRule):
    """Flag Perform JavaScript in Web Viewer steps that aren't wrapped
    with the canonical Set Error Capture pattern.

    PJSIWV is silent-failure-prone: if the target object name is wrong,
    if the JS function name doesn't exist, or if the WV is in a bad
    state, the step fails with no diagnostic and the caller has no way
    to know. The canonical wrapper turns Error Capture On before the
    step, captures FM errors via CreateEventObjectFmErrorsOnly, turns
    Error Capture Off after, and checks $fmErrorCode for a non-zero
    result.

    Surfaced 2026-05-15 — a helper script written during the SL
    Inventory audit missed wrapping the second of two PJSIWV calls in
    INV_StandardCost_RefreshPayload due to a substring-find bug. Trevor
    caught it on paste review. Adding this rule means the next time a
    PJSIWV slips through unwrapped (Claude-generated or human-typed),
    fmlint catches it instead of relying on human review.

    Heuristic: the step immediately before a PJSIWV (skipping
    # (comment) steps) must be Set Error Capture with state="True",
    AND the next non-comment step after must be Set Variable whose
    value calc references CreateEventObjectFmErrorsOnly.
    """

    rule_id = "SL010"
    name = "pjsiwv-must-be-wrapped"
    category = "sl_fork"
    default_severity = Severity.ERROR
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        diagnostics = []
        steps = parse_result.steps
        for idx, step in enumerate(steps):
            if step.get("name", "") != "Perform JavaScript in Web Viewer":
                continue

            # Walk backwards skipping comments to find the previous
            # executable step.
            prev_idx = idx - 1
            while prev_idx >= 0 and steps[prev_idx].get("name", "") == "# (comment)":
                prev_idx -= 1
            if prev_idx < 0:
                diagnostics.append(self._diag(sev, idx, "no preceding step"))
                continue

            prev = steps[prev_idx]
            if prev.get("name", "") != "Set Error Capture":
                diagnostics.append(self._diag(sev, idx, f"preceded by '{prev.get('name', '?')}', not Set Error Capture"))
                continue

            set_el = prev.find("Set")
            if set_el is None or set_el.get("state", "False") != "True":
                diagnostics.append(self._diag(sev, idx, "preceded by Set Error Capture but state is not True"))
                continue

            # Confirm the closing side
            next_idx = idx + 1
            while next_idx < len(steps) and steps[next_idx].get("name", "") == "# (comment)":
                next_idx += 1
            if next_idx >= len(steps):
                diagnostics.append(self._diag(sev, idx, "no closer steps follow (script ends abruptly)"))
                continue

            nxt = steps[next_idx]
            if nxt.get("name", "") != "Set Variable":
                diagnostics.append(self._diag(sev, idx, f"followed by '{nxt.get('name', '?')}', not Set Variable [CreateEventObjectFmErrorsOnly]"))
                continue

            found_closer = False
            for calc in nxt.iter("Calculation"):
                if calc.text and "CreateEventObjectFmErrorsOnly" in calc.text:
                    found_closer = True
                    break
            if not found_closer:
                diagnostics.append(self._diag(sev, idx, "followed by Set Variable, but the value calc doesn't reference CreateEventObjectFmErrorsOnly"))

        return diagnostics

    def _diag(self, severity, step_idx, reason):
        return Diagnostic(
            rule_id=self.rule_id,
            severity=severity,
            message=(
                f"Perform JavaScript in Web Viewer is not wrapped with the "
                f"canonical Set Error Capture pattern — {reason}. PJSIWV is "
                f"silent-failure-prone (wrong object name / missing JS "
                f"function / bad WV state surfaces nothing)."
            ),
            line=step_idx + 1,
            fix_hint=(
                "Insert Set Error Capture [On] immediately before the PJSIWV "
                "step. After the PJSIWV, add the canonical 5-step closer: "
                "Set Variable [$eventObjectFmErrorsOnly ; "
                "CreateEventObjectFmErrorsOnly] / Set Error Capture [Off] / "
                "Set Variable [$fmErrorCode ; GetFmErrorCode (...)] / "
                "If [$fmErrorCode <> 0] / ...build error event + "
                "Exit Loop If [True] / End If."
            ),
        )


# ---------------------------------------------------------------------------
# SL011 — no-double-blank-comments
# ---------------------------------------------------------------------------

@rule
class NoDoubleBlankComments(LintRule):
    """Flag runs of 2+ consecutive blank # (comment) steps.

    The TMPL_NewScript templates ship with double-blank lines as visual
    "insert your code here" markers between sections. A finished script
    should clean these up — at most one blank line is needed between
    sections.

    Team convention 2026-05-15.

    A "blank # (comment)" step is one with no <Text> child element
    (i.e., a self-closing <Step ... name="# (comment)"/>).
    """

    rule_id = "SL011"
    name = "no-double-blank-comments"
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
        run_start = None  # index of the first blank in the current run
        run_len = 0
        for idx, step in enumerate(steps):
            if self._is_blank_comment(step):
                if run_start is None:
                    run_start = idx
                run_len += 1
            else:
                if run_len >= 2:
                    diagnostics.append(self._diag(sev, run_start, run_len))
                run_start = None
                run_len = 0
        # Flush trailing run (script ending with consecutive blanks)
        if run_len >= 2:
            diagnostics.append(self._diag(sev, run_start, run_len))
        return diagnostics

    @staticmethod
    def _is_blank_comment(step) -> bool:
        if step.get("name", "") != "# (comment)":
            return False
        text_el = step.find("Text")
        if text_el is None:
            return True
        # Some converters / scripts emit <Text></Text> for a blank — treat
        # that as blank too.
        return not (text_el.text and text_el.text.strip())

    def _diag(self, severity, run_start_idx, run_len):
        return Diagnostic(
            rule_id=self.rule_id,
            severity=severity,
            message=(
                f"Run of {run_len} consecutive blank # (comment) steps. "
                f"The TMPL_NewScript templates ship with double-blank "
                f"markers to indicate where dev code goes — a finished "
                f"script should consolidate these to a single blank."
            ),
            line=run_start_idx + 1,
            fix_hint=(
                f"Delete {run_len - 1} of the consecutive blank # (comment) "
                "steps so only one blank separator remains between sections."
            ),
        )


# ---------------------------------------------------------------------------
# SL012 — handle-result-exit-canonical
# ---------------------------------------------------------------------------

@rule
class HandleResultExitCanonical(LintRule):
    """Flag scripts that don't end with the canonical HANDLE RESULT & EXIT
    block from TMPL_NewScript.

    The template's tail block is highly structured — the cascade shape
    and Exit Script return value are invariant; only the success-path
    JSONSetElement is allowed to vary (to return additional values
    beyond just scriptResult).

    Canonical tail (simplified):

        If [ False ]
        Else If [ not IsEmpty ( $scriptResultObject ) ]
          # Already defined.
        Else If [ IsEmpty ( $errorMessage ) ]
          Set Variable [ $scriptResult ; OK ]
          Set Variable [ $scriptResultObject ; JSONSetElement ( ... ) ]
        Else
          Set Variable [ $scriptResult ; ERROR ]
          Set Variable [ $scriptResultMessage ; $errorMessage ]
          Set Variable [ $scriptResultObject ; JSONSetElement ( ... ) ]
          Perform Script [ "COM_ScriptResultHandler" ; ... ]
        End If
        Exit Script [ $scriptResultObject ]

    Exemption: scripts with NO Loop step are treated as thin wrappers
    (e.g., INV_OpenNewItemDialog) and are not subject to this rule —
    they typically Exit Script with Get(ScriptResult) from a sub-script
    call.

    Surfaced 2026-05-15 — team convention. The HANDLE RESULT block is
    one of the most distinctive markers of a TMPL_NewScript-conformant
    script, and divergences here usually mean the caller can't reliably
    read the result.
    """

    rule_id = "SL012"
    name = "handle-result-exit-canonical"
    category = "sl_fork"
    default_severity = Severity.ERROR
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps

        # Exemption: scripts with no Loop are thin wrappers.
        has_loop = any(s.get("name", "") == "Loop" for s in steps)
        if not has_loop:
            return []

        diagnostics = []

        # Find the last non-comment step.
        last_idx = len(steps) - 1
        while last_idx >= 0 and steps[last_idx].get("name", "") == "# (comment)":
            last_idx -= 1
        if last_idx < 0:
            return []  # script is all comments — odd but not our concern

        last = steps[last_idx]
        if last.get("name", "") != "Exit Script":
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    f"Script's final executable step is '{last.get('name', '?')}', "
                    f"not Exit Script. The canonical TMPL_NewScript tail "
                    f"ends with Exit Script [ $scriptResultObject ]."
                ),
                line=last_idx + 1,
                fix_hint=(
                    "Append the canonical HANDLE RESULT & EXIT block: "
                    "If [False] / Else If [not IsEmpty($scriptResultObject)] / "
                    "Else If [IsEmpty($errorMessage)] / Else / End If / "
                    "Exit Script [$scriptResultObject]."
                ),
            ))
            return diagnostics

        # Check Exit Script's calculation is $scriptResultObject.
        exit_calc = ""
        for calc in last.iter("Calculation"):
            if calc.text:
                exit_calc = calc.text.strip()
                break
        if exit_calc != "$scriptResultObject":
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    f"Exit Script returns {exit_calc or '(empty)'}, not "
                    f"$scriptResultObject. The canonical TMPL_NewScript "
                    f"tail returns the structured result object so callers "
                    f"can read scriptResult / scriptResultMessage / "
                    f"eventObject keys."
                ),
                line=last_idx + 1,
                fix_hint=(
                    "Change Exit Script's calculation to $scriptResultObject. "
                    "The preceding HANDLE RESULT block should populate that "
                    "variable for both success and error paths."
                ),
            ))

        # Walk backwards to find the End If that should close the cascade.
        prev_idx = last_idx - 1
        while prev_idx >= 0 and steps[prev_idx].get("name", "") == "# (comment)":
            prev_idx -= 1
        if prev_idx < 0 or steps[prev_idx].get("name", "") != "End If":
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Exit Script is not immediately preceded by End If. "
                    "The canonical HANDLE RESULT cascade closes with End If "
                    "right before Exit Script."
                ),
                line=last_idx + 1,
                fix_hint="Wrap the result-building logic in an If [False] / Else If / Else / End If cascade before Exit Script.",
            ))
            return diagnostics

        # Walk backwards through balanced If/End If pairs to find the
        # matching If (or Else If at depth 0 — but we want the outer If).
        depth = 1
        cascade_open_idx = -1
        i = prev_idx - 1
        while i >= 0:
            name = steps[i].get("name", "")
            if name == "End If":
                depth += 1
            elif name == "If":
                depth -= 1
                if depth == 0:
                    cascade_open_idx = i
                    break
            i -= 1
        if cascade_open_idx < 0:
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message="Could not find the matching If for the cascade-closing End If before Exit Script.",
                line=prev_idx + 1,
                fix_hint="Verify the HANDLE RESULT cascade has a properly-balanced If / End If structure.",
            ))
            return diagnostics

        # Determine which cascade pattern is in use:
        #   Pattern A (TMPL_NewScript verbatim): If [False] / Else If [not
        #     IsEmpty ($scriptResultObject)] / Else If [IsEmpty ($errorMessage)] /
        #     Else / End If
        #   Pattern B (ActionRouter variant): If [not IsEmpty
        #     ($scriptResultObject)] / Else If [IsEmpty ($errorMessage)] /
        #     Else / End If
        # Both are functionally equivalent — Pattern B just skips the no-op
        # If [False] entry. Team ratified 2026-05-15 that both are
        # acceptable.
        if_calc = ""
        for calc in steps[cascade_open_idx].iter("Calculation"):
            if calc.text:
                if_calc = calc.text.strip()
                break
        if_calc_norm = self._normalize(if_calc)

        pattern_a = if_calc_norm == "False"
        pattern_b = if_calc_norm == self._normalize("not IsEmpty ( $scriptResultObject )")

        if not (pattern_a or pattern_b):
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    f"HANDLE RESULT cascade's opening If has calculation "
                    f"'{if_calc}', which doesn't match either canonical "
                    f"entry: 'False' (Pattern A) or 'not IsEmpty ( "
                    f"$scriptResultObject )' (Pattern B / ActionRouter)."
                ),
                line=cascade_open_idx + 1,
                fix_hint=(
                    "Set the cascade-opening If to either 'False' (Pattern A; "
                    "main branches go on the Else If's that follow) or "
                    "'not IsEmpty ( $scriptResultObject )' (Pattern B; "
                    "shorter ActionRouter form)."
                ),
            ))

        # Collect Else If conditions to verify the success-path branch.
        else_if_conditions = []
        for j in range(cascade_open_idx + 1, prev_idx):
            if steps[j].get("name", "") == "Else If":
                for calc in steps[j].iter("Calculation"):
                    if calc.text:
                        else_if_conditions.append((j, calc.text.strip()))
                        break

        # Required: an Else If branch for IsEmpty ( $errorMessage ) — the
        # success-path branch. Same in both Pattern A and Pattern B.
        success_branch = self._normalize("IsEmpty ( $errorMessage )")
        if not any(self._normalize(cond) == success_branch for _, cond in else_if_conditions):
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "HANDLE RESULT cascade is missing the canonical "
                    "Else If [ IsEmpty ( $errorMessage ) ] branch (the "
                    "success path)."
                ),
                line=cascade_open_idx + 1,
                fix_hint=(
                    "Add an Else If [ IsEmpty ( $errorMessage ) ] branch "
                    "that sets $scriptResult to OK and builds the success "
                    "$scriptResultObject."
                ),
            ))

        # For Pattern A only: also require Else If [ not IsEmpty (
        # $scriptResultObject ) ]. Pattern B's opening If already serves
        # this role, so it doesn't need the Else If duplicate.
        if pattern_a:
            already_defined = self._normalize("not IsEmpty ( $scriptResultObject )")
            if not any(self._normalize(cond) == already_defined for _, cond in else_if_conditions):
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        "HANDLE RESULT cascade (Pattern A) is missing the "
                        "canonical Else If [ not IsEmpty ( $scriptResultObject ) ] "
                        "branch (the already-defined path)."
                    ),
                    line=cascade_open_idx + 1,
                    fix_hint=(
                        "Add an Else If [ not IsEmpty ( $scriptResultObject ) ] "
                        "branch after the If [False] entry — it allows callers "
                        "to pre-populate the result and short-circuit the rest "
                        "of the cascade."
                    ),
                ))

        # Verify the error (Else) branch includes a Perform Script call to
        # COM_ScriptResultHandler.
        has_handler_call = False
        for j in range(cascade_open_idx, prev_idx):
            step = steps[j]
            if step.get("name", "") != "Perform Script":
                continue
            script_ref = step.find("Script")
            if script_ref is not None and "COM_ScriptResultHandler" in (script_ref.get("name", "") or ""):
                has_handler_call = True
                break
        if not has_handler_call:
            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "HANDLE RESULT cascade does not call COM_ScriptResultHandler. "
                    "The canonical tail invokes it on the error path so the "
                    "centralized result-handler can do dialog / notification "
                    "/ logging work based on the eventObject."
                ),
                line=cascade_open_idx + 1,
                fix_hint=(
                    "In the Else (error) branch, after building "
                    "$scriptResultObject, add: Perform Script [ "
                    "\"COM_ScriptResultHandler\" ; Parameter: "
                    "PassSubscriptParameterObject ( $scriptResultObject ) ]."
                ),
            ))

        return diagnostics

    @staticmethod
    def _normalize(s: str) -> str:
        """Normalize whitespace for comparing calculations — FM is loose
        about spaces around operators and parens."""
        return " ".join(s.split())


# ---------------------------------------------------------------------------
# SL013 — no-calc-alignment-padding
# ---------------------------------------------------------------------------

@rule
class NoCalcAlignmentPadding(LintRule):
    """Flag Calculation contents with vertical-alignment padding (runs of
    2+ consecutive interior spaces).

    Some FM developers format JSONSetElement (and similar multi-line
    calcs) with extra spaces to vertically align the `;` separators and
    the `JSONString`/`JSONObject` type tokens, e.g.:

        JSONSetElement ( "{}"
            ; [ "scriptResult"        ; "ERROR"                                                              ; JSONString ]
            ; [ "scriptResultMessage" ; $errorMessage                                                        ; JSONString ]
            ; [ "eventObject"         ; If ( IsEmpty ( $eventObject ) ; CreateEventObjectFm ; $eventObject ) ; JSONObject ]
        )

    Team convention 2026-05-15 — use single spaces between tokens, no
    alignment padding:

        JSONSetElement ( "{}"
            ; [ "scriptResult" ; "ERROR" ; JSONString ]
            ; [ "scriptResultMessage" ; $errorMessage ; JSONString ]
            ; [ "eventObject" ; If ( IsEmpty ( $eventObject ) ; CreateEventObjectFm ; $eventObject ) ; JSONObject ]
        )

    Alignment padding hurts diffs (any change to a long line forces the
    alignment to re-pad the whole block), reading speed (eyes scan
    long blank spaces), and the value of the leading tab indentation
    (the actual code starts well after the visual indent).

    Heuristic: any Calculation whose CDATA text contains a line where —
    after stripping leading whitespace (tabs/spaces, legitimate
    indentation) — there are 2+ consecutive interior spaces, is
    flagged. False positives could occur on multi-space string literals
    (e.g., \"hello  world\") but those are rare; the diagnostic message
    explains the false-positive case so devs can recognize it.
    """

    rule_id = "SL013"
    name = "no-calc-alignment-padding"
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
            flagged_this_step = False
            for calc in step.iter("Calculation"):
                if calc.text is None or not calc.text:
                    continue
                # Walk lines, look for 2+ consecutive interior spaces
                for line in calc.text.split("\n"):
                    stripped = line.lstrip(" \t")
                    if "  " in stripped:
                        diagnostics.append(Diagnostic(
                            rule_id=self.rule_id,
                            severity=sev,
                            message=(
                                "Calculation contains vertical-alignment "
                                "padding (2+ consecutive interior spaces). "
                                "Team convention 2026-05-15 — use single "
                                "spaces between tokens, no alignment. "
                                "Alignment padding hurts diffs and "
                                "readability."
                            ),
                            line=idx + 1,
                            fix_hint=(
                                "Collapse runs of multiple spaces to single "
                                "spaces in the calculation. Note: legitimate "
                                "multi-space string literals (e.g., "
                                "\"hello  world\") will also trigger — if "
                                "that's the case, disable this rule on the "
                                "specific occurrence or treat the warning "
                                "as informational."
                            ),
                        ))
                        flagged_this_step = True
                        break
                if flagged_this_step:
                    break  # one diagnostic per step is enough
        return diagnostics


# ---------------------------------------------------------------------------
# SL017 — leftover-template-scaffolding
# ---------------------------------------------------------------------------

@rule
class LeftoverTemplateScaffolding(LintRule):
    """Flag scripts that still contain TMPL_NewScript scaffolding markers
    instructing the developer to delete unused template steps.

    The TMPL_NewScript / TMPL_NewScript - Transactions templates include
    "DELETE THE BELOW IF NO USER DIALOGS IN THIS SCRIPT" as a literal
    comment in the DISPLAY NOTIFICATION / ERROR section, followed by a
    block of disabled scaffolding steps. The marker is an instruction to
    the developer authoring the script: if the script doesn't display
    user dialogs, delete the marker AND the disabled steps below it.

    A finished, non-template script that still contains this marker is
    a finished-script smell — either:
      (a) the disabled scaffolding below was left in by accident
          (compliance failure), or
      (b) the marker itself was left in (style failure).

    The fix is mechanical:
      - If the script doesn't display user dialogs: replace the entire
        block (marker + disabled steps) with a single
        '# No user notifications in this script.' comment.
      - If the script DOES display dialogs: enable the relevant steps
        (so they're no longer commented out) and remove the marker.

    The TMPL_NewScript and TMPL_NewScript - Transactions templates
    themselves are exempt from this rule via the TMPL filename prefix.

    Team convention 2026-05-15 (after rebuilding INV_SupplierPriceDates_
    SaveAllWorker on TMPL_NewScript - Transactions surfaced the marker
    as leftover scaffolding).
    """

    rule_id = "SL017"
    name = "leftover-template-scaffolding"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    _MARKER_SUBSTRINGS = (
        "DELETE THE BELOW IF NO USER DIALOGS",
        "DELETE THE BELOW IF NO USER DIALOG",  # tolerate singular
    )

    # If the script's PURPOSE comment matches one of these template
    # signatures, it IS the template itself — exempt.
    _TEMPLATE_PURPOSE_SIGNATURES = (
        "development template to use",
    )

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps

        # Content-based exemption: the TMPL_NewScript / TMPL_NewScript -
        # Transactions templates carry a distinct PURPOSE comment that no
        # finished script reproduces. Scan the first ~8 comment steps for
        # one of the template signatures; if found, this IS the template
        # and we skip the rule.
        for step in steps[:20]:
            if step.get("name", "") != "# (comment)":
                continue
            text_el = step.find("Text")
            if text_el is None or text_el.text is None:
                continue
            if any(sig in text_el.text for sig in self._TEMPLATE_PURPOSE_SIGNATURES):
                return []

        diagnostics = []
        for i, step in enumerate(steps):
            if step.get("name", "") != "# (comment)":
                continue
            text_el = step.find("Text")
            if text_el is None or text_el.text is None:
                continue
            txt = text_el.text.upper()
            if not any(m in txt for m in self._MARKER_SUBSTRINGS):
                continue

            diagnostics.append(Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Leftover TMPL_NewScript scaffolding marker found: "
                    "'DELETE THE BELOW IF NO USER DIALOGS IN THIS SCRIPT'. "
                    "This is an instruction to the developer to remove "
                    "the disabled scaffolding steps below it (or enable "
                    "them if the script does display dialogs) and delete "
                    "the marker itself. A finished script should never "
                    "contain this marker."
                ),
                line=i + 1,
                fix_hint=(
                    "If this script doesn't display user dialogs, replace "
                    "the marker AND the disabled steps below it with a "
                    "single '# No user notifications in this script.' "
                    "comment. If it DOES display dialogs, enable the "
                    "relevant Set Variable / If / Perform Script steps "
                    "and delete the marker."
                ),
            ))
            # One diagnostic per script — the marker is a single point of fix.
            break

        return diagnostics


# ---------------------------------------------------------------------------
# SL018 — header-parameter-doc-block
# ---------------------------------------------------------------------------

@rule
class HeaderParameterDocBlock(LintRule):
    """Flag parameter / result documentation blocks in script header
    comments and disabled $README Insert Text doc steps.

    SL_Core convention: a script's own JSONGetElement calls and
    HANDLE RESULT JSON construction ARE the parameter/result contract.
    Header documentation that enumerates field names, types, or shapes
    (e.g. PARAMETER FORMAT, RESULT JSON, INPUT/OUTPUT, PARAMS, RETURNS)
    is a second source of truth that drifts the moment the script is
    modified. Same goes for disabled `Insert Text [ $README ]` doc-block
    steps — they accumulate stale prose without enforcement.

    The fix is to delete the doc block. Let the script speak for itself.

    Architectural notes that don't enumerate fields (e.g.
    "ATOMICITY: outer transaction wraps both sub-script calls" or a
    one-sentence PURPOSE line) are fine — they describe behavior, not
    contract.

    Team convention 2026-05-15.
    """

    rule_id = "SL018"
    name = "header-parameter-doc-block"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    # Substrings (uppercase) that, when starting a header # (comment) Text,
    # indicate a parameter/result enumeration that should not be there.
    _BANNED_PREFIXES = (
        "PARAMETER FORMAT",
        "PARAMETERS:",
        "PARAMS:",
        "RESULT JSON",
        "RETURN VALUE",
        "RETURN VALUES",
        "RETURNS:",
        "INPUT:",
        "INPUTS:",
        "OUTPUT:",
        "OUTPUTS:",
        "ARGUMENTS:",
        "ARGS:",
    )

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps

        # Find the boundary between header and body. Header ends at the
        # first `Loop` step (the BEGIN PSEUDO LOOP). If there's no Loop,
        # treat the entire script as header (rare — thin wrappers).
        body_start = len(steps)
        for i, s in enumerate(steps):
            if s.get("name", "") == "Loop":
                body_start = i
                break

        diagnostics = []

        for i in range(body_start):
            step = steps[i]
            name = step.get("name", "")

            # Case 1: # (comment) step with text starting with a banned prefix.
            if name == "# (comment)":
                text_el = step.find("Text")
                if text_el is None or text_el.text is None:
                    continue
                txt_stripped = text_el.text.lstrip("# ").strip()
                if not txt_stripped:
                    continue
                upper = txt_stripped.upper()
                if any(upper.startswith(p) for p in self._BANNED_PREFIXES):
                    diagnostics.append(Diagnostic(
                        rule_id=self.rule_id,
                        severity=sev,
                        message=(
                            "Script header contains a parameter/result "
                            f"documentation block ('{txt_stripped[:60]}...'). "
                            "SL_Core convention is that the script's own "
                            "JSONGetElement calls and HANDLE RESULT JSON "
                            "construction are the contract. Header "
                            "documentation drifts the moment the script "
                            "is modified — delete it and let the script "
                            "speak for itself."
                        ),
                        line=i + 1,
                        fix_hint=(
                            "Delete this comment step (and any sibling "
                            "comment steps that continue the same "
                            "PARAMETER FORMAT / RESULT JSON / INPUT / "
                            "OUTPUT block). The NOTES section ships "
                            "intentionally empty in TMPL_NewScript; "
                            "keep it that way unless there's a genuinely "
                            "architectural note that doesn't enumerate "
                            "field names, types, or shapes."
                        ),
                    ))

            # Case 2: disabled `Insert Text` step targeting $README — the
            # legacy doc-block pattern. Flag regardless of content.
            elif name == "Insert Text":
                field_el = step.find("Field")
                if field_el is not None and (field_el.text or "").strip() == "$README":
                    diagnostics.append(Diagnostic(
                        rule_id=self.rule_id,
                        severity=sev,
                        message=(
                            "Disabled Insert Text [ $README ] doc-block "
                            "step found in script header. SL_Core "
                            "convention deprecated this pattern in favor "
                            "of inline # (comment) steps. The $README "
                            "target was a convention from the upstream "
                            "agentic-fm style guide; it accumulates "
                            "stale prose because no lint or runtime "
                            "check enforces alignment with the script's "
                            "actual JSON shape."
                        ),
                        line=i + 1,
                        fix_hint=(
                            "Delete the Insert Text [ $README ] step. "
                            "If a genuinely architectural note is worth "
                            "preserving (one that doesn't enumerate "
                            "fields/types/shapes), move it to a # "
                            "(comment) step in the PURPOSE or NOTES "
                            "section."
                        ),
                    ))

        return diagnostics


# ---------------------------------------------------------------------------
# Helpers for SL014/SL015 (post-Loop section detection)
# ---------------------------------------------------------------------------

def _find_last_end_loop(steps) -> int:
    """Return the index of the LAST End Loop step, or -1 if none."""
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].get("name", "") == "End Loop":
            return i
    return -1


def _has_section_marker_after(steps, after_idx: int, *marker_texts: str) -> bool:
    """Return True if any # (comment) step after `after_idx` has Text
    that starts with any of the given marker_texts (case-insensitive)."""
    upper_markers = tuple(m.upper() for m in marker_texts)
    for j in range(after_idx + 1, len(steps)):
        if steps[j].get("name", "") != "# (comment)":
            continue
        text_el = steps[j].find("Text")
        if text_el is None or text_el.text is None:
            continue
        txt = text_el.text.strip().upper()
        if any(txt.startswith(m) for m in upper_markers):
            return True
    return False


# ---------------------------------------------------------------------------
# SL014 — missing-cleanup-section
# ---------------------------------------------------------------------------

@rule
class MissingCleanupSection(LintRule):
    """Flag scripts (with a Loop) that don't have a # CLEANUP section
    header between End Loop and Exit Script.

    The TMPL_NewScript template includes a CLEANUP section after the
    pseudo-loop for any post-loop teardown work (closing files,
    releasing locks, restoring window state, etc.). The section header
    should always exist even if the section body is empty — it's the
    scannable indicator that the developer considered cleanup and
    found nothing needed (vs. forgot the section entirely).

    Team convention 2026-05-15.
    """

    rule_id = "SL014"
    name = "missing-cleanup-section"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps

        # Only fires on scripts that have a Loop (excludes thin wrappers).
        if not any(s.get("name", "") == "Loop" for s in steps):
            return []

        end_loop_idx = _find_last_end_loop(steps)
        if end_loop_idx < 0:
            return []  # has Loop but no matching End Loop — different problem

        if _has_section_marker_after(steps, end_loop_idx, "CLEANUP"):
            return []

        return [Diagnostic(
            rule_id=self.rule_id,
            severity=sev,
            message=(
                "Script has a pseudo-loop but no '# CLEANUP' section "
                "header after End Loop. The TMPL_NewScript template "
                "always includes the section header (even if the section "
                "body is empty) so reviewers can confirm cleanup was "
                "considered."
            ),
            line=end_loop_idx + 1,
            fix_hint=(
                "Add three # (comment) steps after the END PSEUDO LOOP "
                "marker: '===' row, 'CLEANUP' label, '---' row. Leave "
                "the section body empty if no post-loop cleanup is "
                "needed."
            ),
        )]


# ---------------------------------------------------------------------------
# SL015 — missing-display-notification-section
# ---------------------------------------------------------------------------

@rule
class MissingDisplayNotificationSection(LintRule):
    """Flag scripts (with a Loop) that don't have a # DISPLAY NOTIFICATION
    / ERROR section header between End Loop and Exit Script.

    The TMPL_NewScript template includes a DISPLAY NOTIFICATION /
    ERROR section after CLEANUP for user-facing dialog / notification
    output. Two valid populations:

    1. Scripts that DO display dialogs directly: the section contains
       the canonical If [not IsEmpty ($errorMessage)] dialog-build +
       If [not IsEmpty ($displayMessageOrNotificationObject)] +
       Perform Script COM_DisplayMessageDialogOrNotification logic.

    2. Scripts that do NOT display dialogs (sub-scripts, server-side
       workers, etc.): the section contains a single
       '# No user notifications in this script.' comment.

    Either way, the section header should exist. Missing the section
    means the developer didn't consider whether the script needs user
    feedback.

    Team convention 2026-05-15.
    """

    rule_id = "SL015"
    name = "missing-display-notification-section"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps

        if not any(s.get("name", "") == "Loop" for s in steps):
            return []

        end_loop_idx = _find_last_end_loop(steps)
        if end_loop_idx < 0:
            return []

        # Look for the DISPLAY NOTIFICATION section header. Accept variants:
        # "DISPLAY NOTIFICATION", "DISPLAY NOTIFICATION / ERROR",
        # "DISPLAY NOTIFICATIONS / ERROR".
        if _has_section_marker_after(steps, end_loop_idx, "DISPLAY NOTIFICATION"):
            return []

        return [Diagnostic(
            rule_id=self.rule_id,
            severity=sev,
            message=(
                "Script has a pseudo-loop but no '# DISPLAY NOTIFICATION / "
                "ERROR' section header after End Loop. The TMPL_NewScript "
                "template always includes this section — either populated "
                "with the canonical dialog-build logic (for scripts that "
                "display user feedback directly) or with a single '# No "
                "user notifications in this script.' comment (for "
                "sub-scripts / server-side workers)."
            ),
            line=end_loop_idx + 1,
            fix_hint=(
                "Add three # (comment) steps after the CLEANUP section: "
                "'===' row, 'DISPLAY NOTIFICATION / ERROR' label, '---' "
                "row. Then either: (a) the canonical If "
                "[not IsEmpty ($errorMessage)] build-display-object + "
                "If [not IsEmpty ($displayMessageOrNotificationObject)] "
                "Perform Script COM_DisplayMessageDialogOrNotification "
                "logic, OR (b) a single '# No user notifications in this "
                "script.' comment."
            ),
        )]


# ---------------------------------------------------------------------------
# SL016 — post-loop-section-ordering
# ---------------------------------------------------------------------------

@rule
class PostLoopSectionOrdering(LintRule):
    """Flag post-pseudo-loop sections that appear out of canonical order.

    The TMPL_NewScript canonical order after End Loop is:

        CLEANUP → DISPLAY NOTIFICATION / ERROR → HANDLE RESULT & EXIT

    Why this matters: cleanup work (closing extra windows, returning to
    the original layout, restoring window state) MUST happen before any
    user-facing dialog is shown, so the dialog appears with the right
    visual context. Likewise, the dialog must be shown BEFORE the script
    exits, since Exit Script tears down the runtime state.

    Team convention 2026-05-15.

    SL014 / SL015 enforce that the section headers exist; this rule
    enforces they appear in the canonical order. The three rules can
    fire independently — a script can pass SL014 + SL015 (markers exist)
    but fail SL016 (order is wrong).

    Sections that are absent from the script don't break ordering — only
    the ones that ARE present must be in the right order relative to
    each other.
    """

    rule_id = "SL016"
    name = "post-loop-section-ordering"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    # Canonical order of post-loop sections. Each tuple is (display
    # name, list of accepted Text-startswith prefixes).
    _CANONICAL_ORDER = (
        ("CLEANUP", ("CLEANUP",)),
        ("DISPLAY NOTIFICATION / ERROR", ("DISPLAY NOTIFICATION",)),
        ("HANDLE RESULT & EXIT", ("HANDLE RESULT",)),
    )

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps

        if not any(s.get("name", "") == "Loop" for s in steps):
            return []  # thin wrapper exempt

        end_loop_idx = _find_last_end_loop(steps)
        if end_loop_idx < 0:
            return []

        # Find the FIRST occurrence of each section marker after End Loop.
        section_idx = {name: None for name, _ in self._CANONICAL_ORDER}
        for j in range(end_loop_idx + 1, len(steps)):
            if steps[j].get("name", "") != "# (comment)":
                continue
            text_el = steps[j].find("Text")
            if text_el is None or text_el.text is None:
                continue
            txt = text_el.text.strip().upper()
            for name, prefixes in self._CANONICAL_ORDER:
                if section_idx[name] is not None:
                    continue  # already recorded
                if any(txt.startswith(p) for p in prefixes):
                    section_idx[name] = j
                    break  # this comment step matched one section; move on

        # Walk the canonical order and verify each present marker's
        # position is greater than the previous present marker's position.
        present = [(name, section_idx[name]) for name, _ in self._CANONICAL_ORDER if section_idx[name] is not None]
        diagnostics = []
        for i in range(len(present) - 1):
            cur_name, cur_idx = present[i]
            nxt_name, nxt_idx = present[i + 1]
            if cur_idx > nxt_idx:
                # 'cur' is supposed to come BEFORE 'nxt' but appears AFTER
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        f"Post-loop section '{cur_name}' appears AFTER "
                        f"'{nxt_name}'. Canonical order is "
                        f"CLEANUP → DISPLAY NOTIFICATION / ERROR → "
                        f"HANDLE RESULT & EXIT. Cleanup work (closing "
                        f"extra windows, returning to the original "
                        f"layout, restoring window state) must happen "
                        f"BEFORE any user-facing dialog is shown; "
                        f"dialogs must be shown BEFORE the script exits."
                    ),
                    line=cur_idx + 1,
                    fix_hint=(
                        f"Move the '{cur_name}' section to before the "
                        f"'{nxt_name}' section in the script."
                    ),
                ))
                # One diagnostic per inversion is enough — keep going
                # to catch additional misorderings.
        return diagnostics


# ---------------------------------------------------------------------------
# SL019 — json-key-no-leading-underscore
# ---------------------------------------------------------------------------

@rule
class JsonKeyNoLeadingUnderscore(LintRule):
    """Flag JSONSetElement calls that emit JSON keys with leading
    underscores (e.g., `[ "_id_inventoryItem" ; $val ; JSONString ]`).

    SL_Core JSON key convention (per CLAUDE.md):
        camelCase, no leading underscores, no double-underscore suffixes.
        `_id_InventoryLocation__to` field → `id_inventoryLocationTo` JSON key.

    **Exception — log object construction:**
    Keys passed to LOG_Create MUST match the log table's field names, which
    DO have leading underscores (`_id_appProcess`, `_id_inventoryItemUom`,
    etc.). To exempt a JSONSetElement from this rule, the target Set Variable
    name must contain "log" or "Log" (heuristic — e.g., `$logObject`,
    `$logArray`, `$logEntry`, `$logRevert`).

    Why the rule exists: the convention drift came from devs copying FM
    field names verbatim into JSON keys. Producer + consumer scripts then
    propagate the leading underscore as a contract. SL_Core's intent is
    that field names and JSON keys are distinct namespaces with different
    conventions.

    Team convention 2026-05-19 (drift discovered during UOM v2 Phase 3
    audit on INV_NewInventoryItemUom — ~60+ occurrences across ~18
    workflow scripts).
    """

    rule_id = "SL019"
    name = "json-key-no-leading-underscore"
    category = "sl_fork"
    default_severity = Severity.WARNING
    formats = {"xml"}
    tier = 1

    # If the target Set Variable name contains any of these substrings,
    # treat as log-object construction (exempt). Case-sensitive intentional —
    # FM convention is $logObject, $LogArray, etc. — both forms exempt.
    _LOG_VAR_PATTERNS = ("log", "Log", "LOG")

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []
        sev = self.severity(config)
        steps = parse_result.steps
        diagnostics = []

        import re
        # Quoted JSON key starting with underscore. Will match the key text
        # inside JSONSetElement calls; we filter further by context.
        key_re = re.compile(r'"(_[A-Za-z_][A-Za-z0-9_]*)"')

        for i, step in enumerate(steps):
            if step.get("name", "") != "Set Variable":
                continue

            # Identify target variable name; skip if it looks like a log object.
            name_el = step.find("Name")
            var_name = (name_el.text or "") if name_el is not None else ""
            if any(p in var_name for p in self._LOG_VAR_PATTERNS):
                continue

            # Find the Calculation element inside Value
            calc_el = step.find("Value/Calculation")
            if calc_el is None or not calc_el.text:
                continue
            calc = calc_el.text

            # Must reference JSONSetElement (otherwise leading-underscore
            # strings are just data, not JSON keys).
            if "JSONSetElement" not in calc:
                continue

            seen_keys = set()
            for m in key_re.finditer(calc):
                key = m.group(1)
                if key in seen_keys:
                    continue

                # Is this match in a commented-out (//...) calc line?
                line_start = calc.rfind("\n", 0, m.start()) + 1
                line_prefix = calc[line_start:m.start()]
                if "//" in line_prefix:
                    continue

                # Is this match in JSON key position?
                # Two valid key positions in JSONSetElement:
                #   (a) Scalar form: `JSONSetElement ( JSON ; "key" ; value ; type )`
                #       → the key is preceded by ; and followed by ;
                #   (b) Array form:  `[ "key" ; value ; type ]`
                #       → the key is preceded by [ (with optional whitespace) and followed by ;
                #
                # Quick filter: check char after the closing quote — must be
                # `;` (with whitespace) to be a key.
                after = calc[m.end():m.end()+5].lstrip()
                if not after.startswith(";"):
                    continue

                seen_keys.add(key)

                suggested = key.lstrip("_")
                diagnostics.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        f"JSONSetElement key '{key}' has a leading underscore. "
                        f"SL_Core JSON key convention is camelCase with no "
                        f"leading underscores (suggest '{suggested}'). "
                        f"Exception: keys in log-object construction (passed "
                        f"to LOG_Create) MUST match the log table's field "
                        f"names — for those, the target Set Variable name "
                        f"must contain 'log' / 'Log' / 'LOG' to exempt this "
                        f"rule. Current Set Variable target: "
                        f"{var_name or '(unnamed)'}."
                    ),
                    line=i + 1,
                    fix_hint=(
                        f"Change key '{key}' to '{suggested}'. If this "
                        f"script's $scriptResultObject is consumed by "
                        f"another script or web viewer (grep "
                        f"'JSONGetElement.*\"{key}\"'), update the consumer's "
                        f"JSONGetElement call in the same change to avoid "
                        f"contract breakage."
                    ),
                ))

        return diagnostics
