"""Best-practice rules B001–B005 for FMLint.

These are tier-1 (offline) rules that check for common FileMaker scripting
best practices: error handling, commit-before-nav, parameter validation,
exit script results, and invalid ternary operators.
"""

import re

from ..engine import rule, LintRule
from ..types import Diagnostic, Severity


# ---------------------------------------------------------------------------
# B001 — error-capture-paired
# ---------------------------------------------------------------------------

@rule
class ErrorCapturePaired(LintRule):
    """Set Error Capture [On] is present but no Get(LastError) check found
    anywhere in the script.

    This is informational — many scripts intentionally enable error capture
    for silent failure (e.g. suppressing dialog boxes on missing records or
    failed operations).  The rule flags the absence of any error check so
    the developer can confirm the omission is intentional.
    """

    rule_id = "B001"
    name = "error-capture-paired"
    category = "best_practices"
    default_severity = Severity.INFO
    formats = {"xml", "hr"}
    tier = 1

    _LAST_ERROR_PATTERNS = ("Get ( LastError", "Get(LastError")

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok:
            return []

        sev = self.severity(config)

        steps = parse_result.steps
        has_error_capture_on = False
        has_last_error_check = False
        first_capture_line = 0

        for idx, step in enumerate(steps):
            name = step.get("name", "")

            if name == "Set Error Capture":
                state_el = step.find("Set")
                if state_el is None:
                    state_el = step.find("State")
                if state_el is not None and state_el.get("state") == "False":
                    continue  # [Off], skip
                if not has_error_capture_on:
                    first_capture_line = idx + 1
                has_error_capture_on = True

            # Scan all calculations for Get(LastError)
            for calc in step.iter("Calculation"):
                if calc.text and any(p in calc.text for p in self._LAST_ERROR_PATTERNS):
                    has_last_error_check = True

        if has_error_capture_on and not has_last_error_check:
            return [Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Set Error Capture [On] is enabled but no "
                    "Get(LastError) check appears anywhere in the script. "
                    "This may be intentional (silent error suppression) — "
                    "verify the omission is deliberate."
                ),
                line=first_capture_line,
                fix_hint=(
                    "If errors should be handled: add If [ Get ( LastError ) ≠ 0 ] "
                    "after error-prone steps. If silent suppression is intentional, "
                    "disable this rule or ignore this diagnostic."
                ),
            )]

        return []

    def check_hr(self, lines, catalog, context, config):
        sev = self.severity(config)

        has_error_capture_on = False
        has_last_error_check = False
        first_capture_line = 0

        for ln in lines:
            if ln.step_name == "Set Error Capture":
                bracket = ln.bracket_content or ""
                if "Off" in bracket:
                    continue
                if not has_error_capture_on:
                    first_capture_line = ln.line_number
                has_error_capture_on = True

            # Check bracket content and raw text for Get(LastError)
            content = ln.bracket_content or ""
            raw = ln.raw or ""
            if any(p in content or p in raw for p in self._LAST_ERROR_PATTERNS):
                has_last_error_check = True

        if has_error_capture_on and not has_last_error_check:
            return [Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Set Error Capture [On] is enabled but no "
                    "Get(LastError) check appears anywhere in the script. "
                    "This may be intentional (silent error suppression) — "
                    "verify the omission is deliberate."
                ),
                line=first_capture_line,
                fix_hint=(
                    "If errors should be handled: add If [ Get ( LastError ) ≠ 0 ] "
                    "after error-prone steps. If silent suppression is intentional, "
                    "disable this rule or ignore this diagnostic."
                ),
            )]

        return []


# ---------------------------------------------------------------------------
# B002 — commit-before-nav
# ---------------------------------------------------------------------------

@rule
class CommitBeforeNav(LintRule):
    """Go to Layout should ideally be preceded by Commit Records somewhere in the script."""

    rule_id = "B002"
    name = "commit-before-nav"
    category = "best_practices"
    default_severity = Severity.INFO
    formats = {"xml", "hr"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []

        sev = self.severity(config)
        has_goto_layout = False
        has_commit = False

        for step in parse_result.steps:
            name = step.get("name", "")
            if name == "Go to Layout":
                has_goto_layout = True
            if name in ("Commit Records/Requests", "Commit Records"):
                has_commit = True

        if has_goto_layout and not has_commit:
            return [Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Script navigates to a layout but never commits records. "
                    "Consider adding Commit Records/Requests before navigation "
                    "to avoid losing uncommitted edits."
                ),
                line=0,
                fix_hint="Add a Commit Records/Requests step before Go to Layout",
            )]

        return []

    def check_hr(self, lines, catalog, context, config):
        sev = self.severity(config)
        has_goto_layout = False
        has_commit = False

        for ln in lines:
            if ln.step_name == "Go to Layout":
                has_goto_layout = True
            if ln.step_name in ("Commit Records/Requests", "Commit Records"):
                has_commit = True

        if has_goto_layout and not has_commit:
            return [Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Script navigates to a layout but never commits records. "
                    "Consider adding Commit Records/Requests before navigation "
                    "to avoid losing uncommitted edits."
                ),
                line=0,
                fix_hint="Add a Commit Records/Requests step before Go to Layout",
            )]

        return []


# ---------------------------------------------------------------------------
# B003 — param-validation
# ---------------------------------------------------------------------------

@rule
class ParamValidation(LintRule):
    """Scripts that use Get(ScriptParameter) should validate the parameter.

    Stub — returns empty for now. Full implementation would check whether
    the script validates the parameter value early in the script flow.
    """

    rule_id = "B003"
    name = "param-validation"
    category = "best_practices"
    default_severity = Severity.INFO
    formats = {"xml", "hr"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        return []

    def check_hr(self, lines, catalog, context, config):
        return []


# ---------------------------------------------------------------------------
# B004 — exit-script-result
# ---------------------------------------------------------------------------

@rule
class ExitScriptResult(LintRule):
    """Scripts should Exit Script with a result."""

    rule_id = "B004"
    name = "exit-script-result"
    category = "best_practices"
    default_severity = Severity.INFO
    formats = {"xml", "hr"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok or not parse_result.steps:
            return []

        sev = self.severity(config)
        for step in parse_result.steps:
            if step.get("name", "") == "Exit Script":
                return []

        return [Diagnostic(
            rule_id=self.rule_id,
            severity=sev,
            message=(
                "Script has no Exit Script step. Consider adding "
                "Exit Script with a result to communicate success/failure "
                "to calling scripts."
            ),
            line=0,
            fix_hint="Add an Exit Script [ Result: ... ] step",
        )]

    def check_hr(self, lines, catalog, context, config):
        sev = self.severity(config)
        for ln in lines:
            if ln.step_name == "Exit Script":
                return []

        # Only flag if there are actual steps
        has_steps = any(ln.step_name for ln in lines)
        if not has_steps:
            return []

        return [Diagnostic(
            rule_id=self.rule_id,
            severity=sev,
            message=(
                "Script has no Exit Script step. Consider adding "
                "Exit Script with a result to communicate success/failure "
                "to calling scripts."
            ),
            line=0,
            fix_hint="Add an Exit Script [ Result: ... ] step",
        )]


# ---------------------------------------------------------------------------
# B005 — no-ternary
# ---------------------------------------------------------------------------

@rule
class NoTernary(LintRule):
    """FileMaker does not support the ternary ? : operator in calculations."""

    rule_id = "B005"
    name = "no-ternary"
    category = "best_practices"
    default_severity = Severity.ERROR
    formats = {"xml", "hr"}
    tier = 1

    # Match a ? that is not inside a string literal, preceded by non-? and
    # followed by non-?. This is a heuristic — the ? character has no valid
    # use in FileMaker calculations outside of string literals.
    _TERNARY_RE = re.compile(r'\?')

    def _strip_strings(self, text):
        """Remove quoted string literals to avoid false positives."""
        # Remove "..." strings (FM uses double-quote for strings)
        result = re.sub(r'"[^"]*"', '""', text)
        return result

    def _has_ternary(self, text):
        """Check if text contains a likely ternary ? operator."""
        stripped = self._strip_strings(text)
        # Look for ? character — FM calcs never use ? outside strings
        return bool(self._TERNARY_RE.search(stripped))

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok:
            return []

        sev = self.severity(config)
        diags = []
        for idx, step in enumerate(parse_result.steps):
            for calc in step.iter("Calculation"):
                if calc.text and self._has_ternary(calc.text):
                    diags.append(Diagnostic(
                        rule_id=self.rule_id,
                        severity=sev,
                        message=(
                            'Calculation contains "?" — FileMaker does not '
                            "support the ternary ? : operator"
                        ),
                        line=idx + 1,
                        fix_hint="Use If ( condition ; trueValue ; falseValue ) instead",
                    ))
                    break  # One diagnostic per step is enough

        return diags

    def check_hr(self, lines, catalog, context, config):
        from .calculations import NON_CALC_STEPS

        sev = self.severity(config)
        diags = []
        for ln in lines:
            content = ln.bracket_content or ""
            if not content or ln.step_name in NON_CALC_STEPS:
                continue
            if self._has_ternary(content):
                diags.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        'Calculation contains "?" — FileMaker does not '
                        "support the ternary ? : operator"
                    ),
                    line=ln.line_number,
                    fix_hint="Use If ( condition ; trueValue ; falseValue ) instead",
                ))

        return diags


# ---------------------------------------------------------------------------
# B006 — record-lock-unchecked
# ---------------------------------------------------------------------------

@rule
class RecordLockUnchecked(LintRule):
    """Set Error Capture [On] is present with record-modifying steps but no
    Get(LastError) check.  In multi-user environments, steps like Set Field
    implicitly acquire a write lock that can fail with error 301 (record
    locked by another user).  With error capture on and no error check, the
    failure is completely silent — data is never written and the script
    continues as though it succeeded.
    """

    rule_id = "B006"
    name = "record-lock-unchecked"
    category = "best_practices"
    default_severity = Severity.WARNING
    formats = {"xml", "hr"}
    tier = 1

    _LAST_ERROR_PATTERNS = ("Get ( LastError", "Get(LastError")

    # Steps that implicitly or explicitly open a record for editing.
    # Insert steps that modify field data also acquire a lock.
    _RECORD_MODIFYING_STEPS = frozenset({
        "Set Field",
        "Set Field By Name",
        "Insert Calculated Result",
        "Insert Current Date",
        "Insert Current Time",
        "Insert Current User Name",
        "Insert from Index",
        "Insert Text",
        "Replace Field Contents",
    })

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok:
            return []

        sev = self.severity(config)
        steps = parse_result.steps

        has_error_capture_on = False
        has_last_error_check = False
        first_modify_line = 0
        has_record_modify = False

        for idx, step in enumerate(steps):
            # Disabled steps never execute — they cannot acquire a record lock,
            # enable error capture, or satisfy an error check.
            if step.get("enable") == "False":
                continue

            name = step.get("name", "")

            # Detect Set Error Capture [On]
            if name == "Set Error Capture":
                state_el = step.find("Set")
                if state_el is None:
                    state_el = step.find("State")
                if state_el is not None and state_el.get("state") == "False":
                    continue
                has_error_capture_on = True

            # Detect record-modifying steps
            if name in self._RECORD_MODIFYING_STEPS:
                if not has_record_modify:
                    first_modify_line = idx + 1
                has_record_modify = True

            # Detect Get(LastError) in any calculation
            for calc in step.iter("Calculation"):
                if calc.text and any(
                    p in calc.text for p in self._LAST_ERROR_PATTERNS
                ):
                    has_last_error_check = True

        if has_error_capture_on and has_record_modify and not has_last_error_check:
            return [Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Set Error Capture [On] is enabled and record-modifying "
                    "steps are present (e.g. Set Field) but no Get(LastError) "
                    "check was found. In multi-user environments, these steps "
                    "can fail silently with error 301 (record locked) or "
                    "error 306 (modification ID mismatch), causing data loss "
                    "with no indication to the user or calling script."
                ),
                line=first_modify_line,
                fix_hint=(
                    "Add a Get ( LastError ) check immediately after each "
                    "record-modifying step. See agent/docs/knowledge/"
                    "record-locking.md for the recommended pattern."
                ),
            )]

        return []

    def check_hr(self, lines, catalog, context, config):
        sev = self.severity(config)

        has_error_capture_on = False
        has_last_error_check = False
        first_modify_line = 0
        has_record_modify = False

        for ln in lines:
            # Detect Set Error Capture [On]
            if ln.step_name == "Set Error Capture":
                bracket = ln.bracket_content or ""
                if "Off" in bracket:
                    continue
                has_error_capture_on = True

            # Detect record-modifying steps
            if ln.step_name in self._RECORD_MODIFYING_STEPS:
                if not has_record_modify:
                    first_modify_line = ln.line_number
                has_record_modify = True

            # Detect Get(LastError) in any content
            content = ln.bracket_content or ""
            raw = ln.raw or ""
            if any(p in content or p in raw for p in self._LAST_ERROR_PATTERNS):
                has_last_error_check = True

        if has_error_capture_on and has_record_modify and not has_last_error_check:
            return [Diagnostic(
                rule_id=self.rule_id,
                severity=sev,
                message=(
                    "Set Error Capture [On] is enabled and record-modifying "
                    "steps are present (e.g. Set Field) but no Get(LastError) "
                    "check was found. In multi-user environments, these steps "
                    "can fail silently with error 301 (record locked) or "
                    "error 306 (modification ID mismatch), causing data loss "
                    "with no indication to the user or calling script."
                ),
                line=first_modify_line,
                fix_hint=(
                    "Add a Get ( LastError ) check immediately after each "
                    "record-modifying step. See agent/docs/knowledge/"
                    "record-locking.md for the recommended pattern."
                ),
            )]

        return []
