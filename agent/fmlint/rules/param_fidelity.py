"""Param-fidelity rules X001–X003 for FMLint.

FileMaker's fmxmlsnippet parser **silently discards** child elements whose
tag name does not match what the step expects: the step imports fine, but
the parameter quietly falls back to its default value. These bugs are
invisible at the XML-schema level and only manifest at runtime (e.g. a
"Go to Record" that never advances, a Card window that opens as Document).

These rules validate each <Step>'s child elements against the step catalog
(`params[].xmlElement` et al. — see agent/catalogs/CATALOG_SCHEMA.md), so
the silent-discard class of bugs is caught by the linter instead of being
memorized as prose rules.

Only catalog entries with status "complete" are enforced — per the schema
contract, only those entries are fully reliable. Unknown catalog types are
skipped rather than failed (forward-compatibility rule).
"""

import difflib

from ..engine import rule, LintRule
from ..types import Diagnostic, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _top_level_element(param: dict):
    """Return the direct-child element name a param contributes to a Step.

    Resolution order mirrors CATALOG_SCHEMA.md: a param nested under a
    parentElement or wrapped by a wrapperElement surfaces as that container;
    otherwise the base of xmlElement ("El" or "El/@attr" → "El").
    """
    parent = param.get("parentElement")
    if isinstance(parent, str) and parent:
        return parent
    wrapper = param.get("wrapperElement")
    if isinstance(wrapper, str) and wrapper:
        return wrapper
    xml_el = param.get("xmlElement")
    if isinstance(xml_el, str) and xml_el:
        return xml_el.split("/")[0]
    return None


def _allowed_children(entry: dict) -> set:
    """Build the set of expected direct-child element names for a step."""
    allowed = set()
    for param in entry.get("params", []):
        if not isinstance(param, dict):
            continue
        top = _top_level_element(param)
        if top:
            allowed.add(top)
        ptype = param.get("type", "")
        # Bare calculation params serialize as a <Calculation> child even
        # when xmlElement carries a different logical name.
        if ptype in ("calculation", "calc") :
            allowed.add("Calculation")
        # fieldOrVariable (and textMarker'd params) emit a leading <Text/>
        # marker plus a <Field> element.
        if ptype == "fieldOrVariable" or param.get("textMarker"):
            allowed.add("Text")
            allowed.add("Field")
        # findRequests params serialize as the <Query> subtree.
        if ptype == "findRequests":
            allowed.add("Query")
    return allowed


def _entry_is_enforceable(entry) -> bool:
    """Enforce only complete catalog entries (schema contract §status)."""
    return bool(entry) and entry.get("status") == "complete"


# Elements FileMaker emits/tolerates but the catalog deliberately does not
# model as params. They are never a mistyped parameter name, so flagging
# them would only produce noise:
#   - Text: bare <Text/> markers appear next to field/variable targets in
#     more step families than the catalog models with `textMarker`.
#   - Animation: FileMaker Go-only; desktop FM drops it benignly on paste
#     and at least one entry (Go to Related Record, `notesAnimation`)
#     excludes it from params by design.
GLOBAL_ALLOWED = {"Text", "Animation"}


# ---------------------------------------------------------------------------
# X001 — unknown-param-element
# ---------------------------------------------------------------------------

@rule
class UnknownParamElement(LintRule):
    """Child element not recognized by the step's catalog params.

    FileMaker silently discards it and the parameter falls back to its
    default (e.g. <State> instead of <Set> on Set Error Capture leaves
    error capture OFF; <Option>/<ExitAfterLast> on Go to Record leave the
    step with no parameters at all).
    """

    rule_id = "X001"
    name = "unknown-param-element"
    category = "param-fidelity"
    default_severity = Severity.ERROR
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if parse_result.root is None:
            return []
        sev = self.severity(config)
        diags = []
        for idx, step in enumerate(parse_result.steps):
            name = step.get("name", "")
            entry = catalog.get(name)
            if not _entry_is_enforceable(entry):
                continue
            allowed = _allowed_children(entry)
            for child in list(step):
                tag = child.tag
                if tag in allowed or tag in GLOBAL_ALLOWED:
                    continue
                suggestion = difflib.get_close_matches(tag, sorted(allowed), n=1, cutoff=0.5)
                hint = None
                if suggestion:
                    hint = f'Did you mean <{suggestion[0]}>? Check the catalog params for "{name}".'
                elif allowed:
                    hint = f'Expected elements for "{name}": {", ".join(sorted(allowed))}.'
                else:
                    hint = f'"{name}" takes no child elements.'
                diags.append(Diagnostic(
                    rule_id=self.rule_id,
                    severity=sev,
                    message=(
                        f'Step {idx + 1} "{name}": <{tag}> is not a recognized '
                        f"parameter element — FileMaker will silently discard it "
                        f"and use the parameter's default value."
                    ),
                    line=0,
                    fix_hint=hint,
                ))
        return diags


# ---------------------------------------------------------------------------
# X002 — missing-discriminator
# ---------------------------------------------------------------------------

@rule
class MissingDiscriminator(LintRule):
    """A param governed by a discriminator appears without it.

    Catalog params may declare `discriminator`: a sibling param (typically
    an enum) that governs their form/presence. Without the discriminator
    element FileMaker falls back to its default and silently ignores the
    governed param — e.g. <Layout> without <LayoutDestination> on
    New Window / Go to Layout is ignored and the window opens on the
    current layout.
    """

    rule_id = "X002"
    name = "missing-discriminator"
    category = "param-fidelity"
    default_severity = Severity.ERROR
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if parse_result.root is None:
            return []
        sev = self.severity(config)
        diags = []
        for idx, step in enumerate(parse_result.steps):
            name = step.get("name", "")
            entry = catalog.get(name)
            if not _entry_is_enforceable(entry):
                continue
            child_tags = {child.tag for child in list(step)}
            for param in entry.get("params", []):
                if not isinstance(param, dict):
                    continue
                disc = param.get("discriminator")
                if not isinstance(disc, str) or not disc:
                    continue
                governed = _top_level_element(param)
                disc_el = disc.split("/")[0]
                if governed and governed in child_tags and disc_el not in child_tags:
                    diags.append(Diagnostic(
                        rule_id=self.rule_id,
                        severity=sev,
                        message=(
                            f'Step {idx + 1} "{name}": <{governed}> is present but its '
                            f"discriminator <{disc_el}> is missing — FileMaker will "
                            f"silently ignore <{governed}> and use the default behavior."
                        ),
                        line=0,
                        fix_hint=(
                            f'Add <{disc_el} value="..."/> before <{governed}> '
                            f"(e.g. <{disc_el} value=\"SelectedLayout\"/> for layout refs)."
                        ),
                    ))
        return diags


# ---------------------------------------------------------------------------
# X003 — known-silent-discard-patterns
# ---------------------------------------------------------------------------

@rule
class KnownSilentDiscardPatterns(LintRule):
    """Hand-verified silent-discard combinations the generic rules can't see.

    1. New Window with NewWndStyles Style="Card" but no Styles bitmask
       attribute → FileMaker ignores Style and opens a Document window.
    2. Perform Script with <FileReference> nested inside <Script> → the
       cross-file script reference does not resolve ("unknown script").
    3. Perform Script cross-file <FileReference> without a
       <UniversalPathList> child → same unresolved-reference symptom.
    """

    rule_id = "X003"
    name = "known-silent-discard-patterns"
    category = "param-fidelity"
    default_severity = Severity.ERROR
    formats = {"xml"}
    tier = 1

    def check_xml(self, parse_result, catalog, context, config):
        if parse_result.root is None:
            return []
        sev = self.severity(config)
        diags = []
        for idx, step in enumerate(parse_result.steps):
            name = step.get("name", "")

            # -- Pattern 1: Card window without the Styles bitmask ---------
            if name == "New Window":
                for styles_el in step.iter("NewWndStyles"):
                    if styles_el.get("Style") == "Card" and not styles_el.get("Styles"):
                        diags.append(Diagnostic(
                            rule_id=self.rule_id,
                            severity=sev,
                            message=(
                                f'Step {idx + 1} "New Window": Style="Card" without the '
                                f'numeric Styles bitmask attribute — FileMaker ignores '
                                f"Style and opens the window as Document."
                            ),
                            line=0,
                            fix_hint=(
                                'Add the Styles bitmask, e.g. Styles="3222339600" '
                                "(Card, dim parent, no chrome except Close)."
                            ),
                        ))

            # -- Patterns 2 & 3: Perform Script cross-file reference -------
            if name in ("Perform Script", "Perform Script on Server"):
                for script_el in step.findall("Script"):
                    if script_el.find("FileReference") is not None:
                        diags.append(Diagnostic(
                            rule_id=self.rule_id,
                            severity=sev,
                            message=(
                                f'Step {idx + 1} "{name}": <FileReference> is nested inside '
                                f"<Script> — it must be a SIBLING of <Script>, or the "
                                f"cross-file script reference will not resolve."
                            ),
                            line=0,
                            fix_hint=(
                                "Move <FileReference id name> up to be a direct child of "
                                "the Step, before <Script>."
                            ),
                        ))
                for fileref_el in step.findall("FileReference"):
                    if fileref_el.find("UniversalPathList") is None:
                        diags.append(Diagnostic(
                            rule_id=self.rule_id,
                            severity=sev,
                            message=(
                                f'Step {idx + 1} "{name}": <FileReference> without a '
                                f"<UniversalPathList> child — the external file reference "
                                f"will not resolve (script shows as unknown)."
                            ),
                            line=0,
                            fix_hint=(
                                "Add <UniversalPathList>file:FilenameWithoutExtension"
                                "</UniversalPathList> inside <FileReference>."
                            ),
                        ))
        return diags
