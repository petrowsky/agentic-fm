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
