"""Live evaluation rules C004–C005 for FMLint.

These are tier-3 rules that validate calculations against a live FileMaker
engine via AGFMEvaluation through OData. They require OData connectivity
and developer confirmation before execution.
"""

import json
import urllib.request
import urllib.error
import base64

from ..engine import rule, LintRule
from ..types import Diagnostic, Severity


def _odata_call(odata_config, script_name, parameter=""):
    """Call a FileMaker script via AGFMScriptBridge over OData.

    Returns the parsed scriptResult dict, or None on failure.
    """
    base_url = odata_config.get("base_url", "")
    database = odata_config.get("database", "")
    username = odata_config.get("username", "")
    password = odata_config.get("password", "")
    bridge = odata_config.get("script_bridge", "AGFMScriptBridge")

    if not all([base_url, database, username, password]):
        return None

    # URL-encode the database name
    encoded_db = urllib.request.quote(database, safe="")
    url = f"{base_url}/{encoded_db}/Script.{bridge}"

    # Build the double-serialized payload
    inner_param = json.dumps({"script": script_name, "parameter": parameter})
    payload = json.dumps({"scriptParameterValue": inner_param})

    # Basic auth
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()

    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("scriptResult", result)
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def _evaluate_expression(odata_config, expression, layout=None):
    """Evaluate a FileMaker calculation via AGFMEvaluation.

    Returns dict with keys: success, error_code, result, expression, layout
    """
    param = {"expression": expression}
    if layout:
        param["layout"] = layout

    result = _odata_call(odata_config, "AGFMEvaluation", json.dumps(param))
    if result is None:
        return None

    # The result might be in resultParameter (JSON string)
    rp = result.get("resultParameter", "")
    if isinstance(rp, str) and rp:
        try:
            result = json.loads(rp)
        except json.JSONDecodeError:
            pass

    # Some AGFMScriptBridge builds wrap the evaluation verdict one layer
    # deeper: the outer envelope's `result` is itself a JSON string holding the
    # real {error_code, result, success}. Peel nested JSON-object string layers
    # so the caller sees the actual verdict — otherwise a genuine "?" result
    # hides behind the outer envelope's success:true and is never flagged.
    for _ in range(3):
        if not isinstance(result, dict):
            break
        inner = result.get("result")
        if isinstance(inner, str):
            stripped = inner.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    result = json.loads(stripped)
                    continue
                except json.JSONDecodeError:
                    pass
        break
        
return result


# ---------------------------------------------------------------------------
# C004 — live-eval-error
# ---------------------------------------------------------------------------

@rule
class LiveEvalError(LintRule):
    """Validate calculations against the live FileMaker engine via OData."""

    rule_id = "C004"
    name = "live-eval-error"
    category = "calculations"
    default_severity = Severity.ERROR
    formats = {"xml", "hr"}
    tier = 3
    requires_confirmation = True

    def _get_odata_config(self, context):
        """Extract OData config from context's automation.json data."""
        if not context or not context._project_root:
            return None

        auto_path = context._project_root / "agent" / "config" / "automation.json"
        if not auto_path.exists():
            return None

        try:
            with open(auto_path, "r", encoding="utf-8") as f:
                auto_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        solutions = auto_data.get("solutions", {})
        # Use the solution from context if available
        sol_name = context.solution_name
        if sol_name and sol_name in solutions:
            return solutions[sol_name].get("odata")

        # Fall back to first solution with OData config
        for sol in solutions.values():
            odata = sol.get("odata")
            if odata and odata.get("base_url"):
                return odata

        return None

    def _extract_calcs_xml(self, steps):
        """Extract (step_index, calc_text) from XML steps."""
        calcs = []
        for idx, step in enumerate(steps):
            for calc in step.iter("Calculation"):
                if calc.text and calc.text.strip():
                    calcs.append((idx + 1, calc.text.strip()))
        return calcs

    def _extract_calcs_hr(self, lines):
        """Extract (line_number, calc_text) from HR lines."""
        calcs = []
        for ln in lines:
            if ln.is_comment or not ln.bracket_content:
                continue
            # The bracket content itself is the expression for most steps
            if ln.params:
                # For Set Variable, the calc is the last param
                calc = ln.params[-1].strip()
                if calc:
                    calcs.append((ln.line_number, calc))
        return calcs

    def _evaluate_calcs(self, calcs, context):
        """Evaluate calculations and return diagnostics."""
        odata_config = self._get_odata_config(context)
        if not odata_config:
            return []

        diags = []
        cache = {}
        layout = context.layout_name if context.available else None

        for line, calc_text in calcs:
            cache_key = (calc_text, layout)
            if cache_key in cache:
                result = cache[cache_key]
            else:
                result = _evaluate_expression(odata_config, calc_text, layout)
                cache[cache_key] = result

            if result is None:
                continue  # Connection issue, skip silently

            if not result.get("success", True):
                error_code = result.get("error_code", "unknown")
                diags.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=self.default_severity,
                    message=(
                        f"Calculation invalid in FileMaker engine "
                        f"(error {error_code}): {calc_text[:80]}"
                    ),
                    line=line,
                ))
            elif str(result.get("result", "")).strip() == "?":
                # AGFMEvaluation reports success even when the engine returns "?"
                # (unknown custom function, missing reference, or a feature the
                # host server does not support). EvaluationError only catches
                # syntax errors, so guard the "?" sentinel here as a hard failure.
                diags.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=self.default_severity,
                    message=(
                        f'Calculation evaluated to "?" in FileMaker engine — '
                        f"likely an unknown custom function, missing reference, "
                        f"or unsupported feature: {calc_text[:80]}"
                    ),
                    line=line,
                ))

        return diags

    def check_xml(self, parse_result, catalog, context, config):
        if not parse_result.ok:
            return []
        calcs = self._extract_calcs_xml(parse_result.steps)
        return self._evaluate_calcs(calcs, context)

    def check_hr(self, lines, catalog, context, config):
        calcs = self._extract_calcs_hr(lines)
        return self._evaluate_calcs(calcs, context)


# ---------------------------------------------------------------------------
# C005 — live-eval-warning (reserved for non-fatal eval issues)
# ---------------------------------------------------------------------------

@rule
class LiveEvalWarning(LintRule):
    """Reserved for non-fatal issues detected by AGFMEvaluation.

    Currently a stub — C004 handles the primary eval flow. This rule
    can be enhanced to detect warnings like deprecated functions, implicit
    type coercions, or context-dependent evaluation differences.
    """

    rule_id = "C005"
    name = "live-eval-warning"
    category = "calculations"
    default_severity = Severity.WARNING
    formats = {"xml", "hr"}
    tier = 3
    requires_confirmation = True

    def check_xml(self, parse_result, catalog, context, config):
        return []

    def check_hr(self, lines, catalog, context, config):
        return []
