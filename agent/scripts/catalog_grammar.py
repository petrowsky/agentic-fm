#!/usr/bin/env python3
"""catalog_grammar.py — shared intermediate representation (IR) and typed catalog
model for the OSS converters (P6.1 scaffold).

This module defines, once for the Python side:

  * ``StepInstance`` — the in-memory shape all four converters read or write, so a
    single grammar engine (P6.2) can serve every direction.
  * ``Value`` — a tagged union of the concrete values a parameter can hold.
  * The typed catalog model (``CatalogEntry`` / ``StepParam`` / ``DiscriminatorBranch``
    and the facet carriers) that the grammar engine reads its rules from.
  * ``param_key()`` — the param-key rule (a ``namedCalc`` param keys off its
    ``wrapperElement``, every other param off its ``xmlElement``), matching the
    reference converter. Every ``namedCalc`` shares ``xmlElement == "Calculation"``,
    so the wrapper is what disambiguates them.

No behaviour change ships in P6.1 — this is the type/parse scaffold that P6.2 (Python
grammar engine) and, structurally mirrored, P6.3 (TS) build on. Stdlib only; no venv.

The TS counterpart lives in ``webviewer/src/converter/catalog-types.ts`` and is kept
deliberately parallel so a facet added to one port is obviously missing from the other
(see the plan's "Python↔TS structural parity" risk).
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Union

from calc_literal import (
    CALC_BACKSLASHES,
    CALC_QUOTES,
    hr_sep,
    is_calc_escape,
    quote_calc_literal,
    reads_as_quoted_literal_text,
    skip_calc_literal,
)

# ---------------------------------------------------------------------------
# Parameter type vocabulary
# ---------------------------------------------------------------------------
# Every ``type`` value observed across the 216-step canonical catalog. The load
# test asserts no param carries a type outside this set (``unknown_typed == 0``);
# a new type appearing here is a deliberate catalog change, not a silent drop.
KNOWN_PARAM_TYPES: frozenset[str] = frozenset(
    {
        "boolean",
        "flagBoolean",
        "flagElement",
        "enum",
        "text",
        "name",
        "calculation",
        "calc",
        "namedCalc",
        "field",
        "fieldOrVariable",
        "fieldList",
        "layout",
        "script",
        "table",
        "tableOccurrence",
        "tableRef",
        "tableReference",
        "reference",
        "fileReference",
        "attrGroup",
        "repeatGroup",
        "bitmaskGroup",
        "findRequests",
        "parametersList",
        "complex",
    }
)


# ---------------------------------------------------------------------------
# Facet carriers — typed views over the advanced grammar the catalog encodes
# ---------------------------------------------------------------------------
@dataclass
class DiscriminatorBranch:
    """One branch of a ``discriminatorValues`` map (keyed by the enum value).

    ``reveal`` lists sibling param keys that become live for this branch; ``hrToken``
    substitutes a fixed HR string for the whole step slot; ``labeled`` flags a branch
    whose revealed params render with their HR labels rather than positionally.
    """

    hr_token: str | None = None
    labeled: bool | None = None
    reveal: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DiscriminatorBranch:
        return cls(
            hr_token=d.get("hrToken"),
            labeled=d.get("labeled"),
            reveal=list(d.get("reveal", [])),
        )


@dataclass
class VisibleWhen:
    """Gate: this param renders only when ``param`` holds one of ``values``."""

    param: str
    values: list[str]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisibleWhen:
        return cls(param=d["param"], values=list(d.get("values", [])))


@dataclass
class HrLabelRule:
    """One entry of ``hrLabelWhen`` — swap the HR label when a sibling param matches."""

    param: str
    values: list[str]
    hr_label: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HrLabelRule:
        return cls(
            param=d["param"],
            values=list(d.get("values", [])),
            hr_label=d.get("hrLabel"),
        )


@dataclass
class AttrField:
    """A member of an ``attrGroup`` / ``repeatGroup`` ``fields`` list.

    ``kind`` is ``"attr"`` (an XML attribute on the group element) or ``"calc"``
    (a nested ``<Calculation>``). Preserves the full source dict in ``raw``.
    """

    key: str
    kind: str | None = None
    xml_attr: str | None = None
    default_value: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AttrField:
        return cls(
            key=d.get("key", ""),
            kind=d.get("kind"),
            xml_attr=d.get("xmlAttr"),
            default_value=d.get("defaultValue"),
            raw=dict(d),
        )


# ---------------------------------------------------------------------------
# Catalog schema — the grammar the engine reads from
# ---------------------------------------------------------------------------
@dataclass
class StepParam:
    """A single parameter definition from a catalog step's ``params[]``.

    The plan-named facet fields are typed explicitly; the untyped long tail
    (bitmask sub-keys, ``notes``/``note``, ``entryElement`` and friends) is kept
    verbatim in ``raw`` so nothing is dropped on load and P6.2 can reach it.
    """

    xml_element: str
    type: str
    hr_label: str | None
    required: bool
    xml_attr: str | None = None
    wrapper_element: str | None = None
    parent_element: str | None = None
    default_value: str | None = None
    enum_values: list[str] = field(default_factory=list)
    hr_enum_values: dict[str, str] = field(default_factory=dict)
    # P7.2: enum values FileMaker renders NO HR token for. `hr_hidden` suppresses
    # a param in every state; this suppresses it in some. FileMaker shows the
    # companion such a value reveals instead of the value itself, so the value is
    # read back from that companion's `visibleWhen` gate rather than from a token
    # of its own. Emit is unaffected -- only HR rendering.
    hr_hidden_values: list[str] = field(default_factory=list)
    inverted_hr: bool | None = None
    enum_style: str | None = None
    flag_style: bool | None = None
    hr_slot: int | None = None
    hr_hidden: bool | None = None
    hr_bare: bool | None = None
    # The exact inverse of ``hr_bare``: render a ``calc``/``field``/``script``,
    # step-level ``text``/``name`` or legacy ``calculation`` value
    # WITH its ``hr_label`` prefix and match it by that label, even though those
    # types print bare/positionally by default. FileMaker labels the same value type
    # per step (Perform Script's name is bare; Configure Region Monitor Script's is
    # ``Script: "…"``; Delete File's path is ``Target file: “…”``). Generic across
    # every bare-by-default value type; needs a
    # non-empty ``hr_label``. Set only where FM renders the label (verified live).
    hr_labeled: bool | None = None
    # A literal-text param (``text``/``name``) FileMaker displays wrapped in quotes
    # (Insert Text's ``… ; “x” ]``). XML -> HR writes the value as ONE text literal
    # (``quote_calc_literal``: embedded ``"``/``“``/``”`` backslash-escaped) so the line
    # parser reads it back as one token whatever quotes, ``;`` or ``]`` it holds; HR ->
    # XML decodes it. An empty value renders no token, as FileMaker does. Set only
    # where FM quotes the value (verified live).
    hr_quoted: bool | None = None
    # The delimiter an hr_quoted param is written with: None (default) is
    # FileMaker's curly ``“…”``; ``"straight"`` is ``"…"``, where FileMaker shows
    # the literal straight-quoted (Perform AppleScript's text).
    hr_quote_style: str | None = None
    # Governing discriminator: the separator FileMaker draws BETWEEN the labeled
    # companions a value reveals when it is not the step's `` ; `` (Insert from
    # Device: ``Camera: Back; Resolution: Full; Flash: Auto``). Render-only.
    hr_reveal_separator: str | None = None
    omit_when_empty: bool | None = None
    emit_empty_default: bool | None = None
    # Governing discriminator: string form names a sibling; map form carries branches.
    discriminator: str | None = None
    discriminator_values: dict[str, DiscriminatorBranch] = field(default_factory=dict)
    visible_when: VisibleWhen | None = None
    hr_label_when: list[HrLabelRule] = field(default_factory=list)
    attr_fields: list[AttrField] = field(default_factory=list)
    # attrGroup display projection: this enum is a view of these top-level fields
    # (attr / text) of the sibling attrGroup sharing its xmlElement, rendered and
    # read as an ordinary enum (FileMaker draws Export Records' stored character
    # set ``Unicode (UTF-8)`` and Save Records as PDF's ``Save to: File`` /
    # ``Blank record, with boxes`` as tokens of their own). Several fields join
    # with ``|`` (``BlankRecord|WithBoxes``). The owning group's body leaves out
    # the fields a visible projection prints; on emit a projection's value
    # overrides those fields of the group.
    group_fields: list[str] = field(default_factory=list)
    # The param key of a sibling whose presence FileMaker draws as this param's
    # empty positional slot (Export Records / Save Records as Excel with no path
    # but a <Profile>: ``Create folders: On ; ; Unicode (UTF-8)``). An empty
    # token read at that slot makes the sibling present.
    hr_empty_slot_with: str | None = None
    description: str | None = None
    # Version data (see ``fm_min_problem``): the param's own minimum FileMaker
    # version and its source (well-formed values only; malformed reads None),
    # and an enum param's per-value minimums keyed by the XML value
    # ({value: {"fmMin": ..., "fmMinSource": ...}}, raw: read each with
    # ``fm_min_version``). Never read by the converters; ``None`` = undeclared.
    fm_min: str | None = None
    fm_min_source: str | None = None
    fm_min_values: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepParam:
        dv = {
            k: DiscriminatorBranch.from_dict(v)
            for k, v in (d.get("discriminatorValues") or {}).items()
        }
        vw = d.get("visibleWhen")
        # `flagBoolean` is the catalog's label for a plain on/off flag FM still
        # serializes as <Element state="True|False"/> (the False state is kept, not
        # omitted). It is identical to `boolean` for render/parse/emit, and only
        # `boolean` has engine branches — so normalize at load, exactly as the
        # reference converter does, or every flagBoolean param renders as nothing.
        # The original label is preserved in ``raw['type']``.
        raw_type = d.get("type", "")
        norm_type = "boolean" if raw_type == "flagBoolean" else raw_type
        return cls(
            xml_element=d.get("xmlElement", ""),
            type=norm_type,
            hr_label=d.get("hrLabel"),
            required=bool(d.get("required", False)),
            xml_attr=d.get("xmlAttr"),
            wrapper_element=d.get("wrapperElement"),
            parent_element=d.get("parentElement"),
            default_value=d.get("defaultValue"),
            enum_values=list(d.get("enumValues", [])),
            hr_enum_values=dict(d.get("hrEnumValues", {})),
            hr_hidden_values=list(d.get("hrHiddenValues", [])),
            inverted_hr=d.get("invertedHr"),
            enum_style=d.get("enumStyle"),
            flag_style=d.get("flagStyle"),
            hr_slot=d.get("hrSlot"),
            hr_hidden=d.get("hrHidden"),
            hr_bare=d.get("hrBare"),
            hr_labeled=d.get("hrLabeled"),
            hr_quoted=d.get("hrQuoted"),
            hr_quote_style=d.get("hrQuoteStyle"),
            hr_reveal_separator=d.get("hrRevealSeparator"),
            omit_when_empty=d.get("omitWhenEmpty"),
            emit_empty_default=d.get("emitEmptyDefault"),
            discriminator=d.get("discriminator"),
            discriminator_values=dv,
            visible_when=VisibleWhen.from_dict(vw) if vw else None,
            hr_label_when=[HrLabelRule.from_dict(x) for x in d.get("hrLabelWhen", [])],
            attr_fields=[AttrField.from_dict(x) for x in d.get("fields", [])],
            group_fields=list(d.get("groupFields", [])),
            hr_empty_slot_with=d.get("hrEmptySlotWith"),
            description=d.get("description"),
            fm_min=_fm_min_of(d),
            fm_min_source=_fm_min_source_of(d),
            fm_min_values=(dict(d["fmMinValues"])
                           if isinstance(d.get("fmMinValues"), dict) else {}),
            raw=dict(d),
        )

    @property
    def is_known_type(self) -> bool:
        return self.type in KNOWN_PARAM_TYPES

    @property
    def key(self) -> str:
        """The param key for this param — see ``param_key``."""
        return param_key(self)


@dataclass
class BlockPair:
    role: str  # "open" | "middle" | "close"
    partners: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BlockPair:
        return cls(role=d.get("role", ""), partners=list(d.get("partners", [])))


@dataclass
class CatalogEntry:
    name: str
    id: int | None
    category: str
    snippet_file: str
    self_closing: bool
    params: list[StepParam]
    hr_signature: str | None
    block_pair: BlockPair | None
    status: str | None
    help_url: str | None
    notes: Any = None
    # Element-alias grammar: a canonical XML element name mapped to the legacy
    # synonyms FileMaker also wrote for the same logical slot across versions
    # (e.g. FM2025 serialized <SetLLMAccout>/<AccoutName>; FM26 corrected the
    # spelling to <SetLLMAccount>/<AccountName>). Emit uses the canonical name;
    # read accepts either. Generic — benefits any drifted step, no per-step code.
    # Populated at load from each param's `wrapperElementAliases` /
    # `parentElementAliases` fields, keyed by the canonical element name (the
    # last parent-path segment for a parent).
    element_aliases: dict[str, list[str]] = field(default_factory=dict)
    # Version data: the step's minimum FileMaker version and its source
    # (see ``fm_min_problem``). ``None`` means unknown.
    fm_min: str | None = None
    fm_min_source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogEntry:
        bp = d.get("blockPair")
        ea: dict[str, list[str]] = {}
        for p in d.get("params", []):
            for alias_key, canonical in (
                ("wrapperElementAliases", p.get("wrapperElement")),
                ("parentElementAliases", p.get("parentElement")),
            ):
                syns = p.get(alias_key)
                if not canonical or not isinstance(syns, list):
                    continue
                seg = canonical.rsplit("/", 1)[-1]
                bucket = ea.setdefault(seg, [])
                for s in syns:
                    if isinstance(s, str) and s not in bucket:
                        bucket.append(s)
        return cls(
            name=d.get("name", ""),
            id=d.get("id"),
            category=d.get("category", ""),
            snippet_file=d.get("snippetFile", ""),
            self_closing=bool(d.get("selfClosing", False)),
            params=[StepParam.from_dict(p) for p in d.get("params", [])],
            hr_signature=d.get("hrSignature"),
            block_pair=BlockPair.from_dict(bp) if bp else None,
            status=d.get("status"),
            help_url=d.get("helpUrl"),
            notes=d.get("notes"),
            element_aliases=ea,
            fm_min=_fm_min_of(d),
            fm_min_source=_fm_min_source_of(d),
            raw=dict(d),
        )


# ---------------------------------------------------------------------------
# Version data: `fmMin` / `fmMinSource`
# ---------------------------------------------------------------------------
# The minimum FileMaker version of a step, a param, an enum value (a param's
# `fmMinValues` map) or a function-catalog entry. `fmMin` is a version
# ("MAJOR", "MAJOR.MINOR" or "MAJOR.MINOR.PATCH") or `FM_MIN_ANY` (present in
# every version the data covers) or `FM_MIN_UNKNOWN` (looked at and still
# undecided, or a retracted value); `fmMinSource` says how it is known. An
# absent `fmMin` is unknown; a malformed pair is unknown too and is a catalog
# defect.
FM_MIN_ANY = "any"
FM_MIN_UNKNOWN = "unknown"
FM_MIN_SOURCES: frozenset[str] = frozenset({"measured", "documented"})


def _is_fm_version(text: str) -> bool:
    """1-3 dot-separated runs of 1-3 ASCII digits, major not zero."""
    parts = text.split(".")
    return (1 <= len(parts) <= 3
            and all(p.isascii() and p.isdigit() and 1 <= len(p) <= 3 for p in parts)
            and int(parts[0]) != 0)


def _fm_min_of(obj: dict[str, Any]) -> str | None:
    """`obj`'s `fmMin` when the pair is well formed, else ``None``."""
    return obj.get("fmMin") if fm_min_problem(obj) is None else None


def _fm_min_source_of(obj: dict[str, Any]) -> str | None:
    return obj.get("fmMinSource") if fm_min_problem(obj) is None else None


def fm_min_problem(obj: Any) -> str | None:
    """What is wrong with ``obj``'s `fmMin` / `fmMinSource` pair, or ``None``.

    Absent keys are not a problem (unknown is a legal state).
    """
    if not isinstance(obj, dict):
        return "not an object"
    if "fmMin" not in obj:
        return "fmMinSource without fmMin" if "fmMinSource" in obj else None
    value = obj["fmMin"]
    if not isinstance(value, str):
        return "fmMin is not a string"
    if value not in (FM_MIN_ANY, FM_MIN_UNKNOWN) and not _is_fm_version(value):
        return f'fmMin "{value}" is neither "{FM_MIN_ANY}", "{FM_MIN_UNKNOWN}" nor a version'
    source = obj.get("fmMinSource")
    if not isinstance(source, str):
        return f'fmMin "{value}" without a string fmMinSource'
    if source not in FM_MIN_SOURCES:
        return f'fmMinSource "{source}" is neither "measured" nor "documented"'
    return None


def fm_min_version(obj: dict[str, Any]) -> str | None:
    """The minimum version: ``None`` unknown, ``""`` none (`any`), else the version.

    Absent, ``"unknown"`` and malformed all read ``None``.
    """
    if fm_min_problem(obj) is not None or obj.get("fmMin") in (None, FM_MIN_UNKNOWN):
        return None
    return "" if obj["fmMin"] == FM_MIN_ANY else obj["fmMin"]


def fm_min_problems(steps: list[dict[str, Any]]) -> list[str]:
    """Every malformed version datum in a raw step catalog, one line each.

    `fmMinValues` belongs on a param, holds one object per value, and names only
    values the param's `enumValues` holds. `fmMinOnOlder` is "dropped" and sits
    beside an `fmMin`; `fmGateWhen` names exactly one sibling param and values.
    """
    out: list[str] = []
    for step in steps:
        name = step.get("name", "")
        problem = fm_min_problem(step)
        if problem:
            out.append(f"{name}: {problem}")
        if "fmMinValues" in step:
            out.append(f"{name}: fmMinValues belongs on a param, not a step")
        for param in step.get("params", []):
            if not isinstance(param, dict):
                continue
            where = f"{name} / {param.get('hrLabel') or param.get('xmlElement')}"
            problem = fm_min_problem(param)
            if problem:
                out.append(f"{where}: {problem}")
            if "fmMinOnOlder" in param:
                if param["fmMinOnOlder"] != "dropped":
                    out.append(f'{where}: fmMinOnOlder must be "dropped"')
                if "fmMin" not in param:
                    out.append(f"{where}: fmMinOnOlder without fmMin")
            if "fmGateWhen" in param:
                scope = param["fmGateWhen"]
                keys = [p.get("wrapperElement") or p.get("xmlElement")
                        for p in step.get("params", [])
                        if isinstance(p, dict) and p is not param and not p.get("groupFields")]
                if (not isinstance(scope, dict) or not scope.get("param")
                        or not scope.get("values")):
                    out.append(f"{where}: fmGateWhen needs a param and values")
                elif keys.count(scope["param"]) != 1:
                    out.append(f"{where}: fmGateWhen param names no single sibling param")
            values = param.get("fmMinValues")
            if values is None:
                continue
            if not isinstance(values, dict):
                out.append(f"{where}: fmMinValues is not an object")
                continue
            for value, spec in values.items():
                problem = fm_min_problem(spec)
                if problem is None and "fmMin" not in spec:
                    problem = "fmMinValues entry has no fmMin"
                if problem:
                    out.append(f"{where} = {value}: {problem}")
                if value not in (param.get("enumValues") or []):
                    out.append(f"{where} = {value}: fmMinValues names a value "
                               "that is not in enumValues")
    return out


def param_key(param: StepParam) -> str:
    """Param-key rule (matches the reference converter).

    A ``namedCalc`` param keys off its ``wrapperElement`` (every namedCalc shares
    ``xmlElement == "Calculation"``, so the wrapper disambiguates); any other param
    keys off its ``xmlElement``.
    """
    if param.group_fields:
        # An attrGroup display projection shares its group's xmlElement.
        return param.xml_element + "#" + param.group_fields[0]
    if param.type == "namedCalc" and param.wrapper_element:
        return param.wrapper_element
    return param.xml_element


def projection_owner(entry: CatalogEntry, proj: StepParam) -> StepParam | None:
    """The attrGroup an attrGroup display projection is a view of."""
    for p in entry.params:
        if (p.type == "attrGroup" and p.xml_element == proj.xml_element
                and (p.parent_element or "") == (proj.parent_element or "")):
            return p
    return None


def read_projected_value(node: ET.Element | None, owner: StepParam, proj: StepParam) -> str:
    """A projection's raw value off its owning group element: each projected
    field's attribute / child text, the non-empty ones joined with ``|``."""
    if node is None:
        return ""
    fields = {f.get("key"): f for f in owner.raw.get("fields", []) if isinstance(f, dict)}
    vals = []
    for key in proj.group_fields:
        f = fields.get(key)
        if not f:
            continue
        v = ""
        if f.get("kind") == "attr":
            v = node.get(f.get("xmlAttr", ""), "")
        elif f.get("kind") == "text":
            c = node.find(f.get("childElement", ""))
            v = (c.text or "") if c is not None else ""
        if v:
            vals.append(v)
    return "|".join(vals)


# ---------------------------------------------------------------------------
# Runtime IR — StepInstance and its Value union
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Absent:
    """The param is not present in the source (distinct from an empty scalar)."""


ABSENT = Absent()


@dataclass(frozen=True)
class Scalar:
    """A raw text / enum / boolean-state value."""

    text: str


@dataclass(frozen=True)
class Calc:
    """A calculation expression (CDATA body)."""

    text: str


@dataclass(frozen=True)
class Field:
    """A field reference."""

    table: str | None = None
    id: int | None = None
    name: str | None = None


@dataclass(frozen=True)
class Ref:
    """A script or layout reference."""

    id: int | None = None
    name: str | None = None


@dataclass
class ListValue:
    """An ordered group (repeat/find requests, parameter lists)."""

    items: list[Value] = field(default_factory=list)


@dataclass
class Group:
    """An attrGroup's attribute bag (attr name → raw string)."""

    attrs: dict[str, str] = field(default_factory=dict)


# Runtime alias (not an annotation): keep ``Union[...]`` rather than ``X | Y`` so it
# evaluates on Python 3.9, the stock macOS python3.
Value = Union[Absent, Scalar, Calc, Field, Ref, ListValue, Group]  # noqa: UP007


@dataclass
class StepInstance:
    """The shared in-memory shape every converter reads or writes.

    ``values`` is keyed by param key (see ``param_key``); a param absent from the
    source is either omitted or mapped to ``ABSENT``.
    """

    name: str
    id: int = 0
    enable: bool = True
    values: dict[str, Value] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading + a self-checking load report (the P6.1 acceptance gate)
# ---------------------------------------------------------------------------
def load_catalog(path: str) -> list[CatalogEntry]:
    """Parse the step catalog JSON into typed ``CatalogEntry`` objects."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    steps = data["steps"] if isinstance(data, dict) and "steps" in data else data
    return [CatalogEntry.from_dict(s) for s in steps]


@dataclass
class LoadReport:
    entries: int
    params_in_json: int
    params_loaded: int
    unknown_typed: list[tuple[str, str, str]]  # (step, xmlElement, type)

    @property
    def dropped(self) -> int:
        return self.params_in_json - self.params_loaded


def load_report(path: str) -> LoadReport:
    """Load the catalog and report whether every param survived typing.

    ``dropped`` counts params present in the JSON but not built into a
    ``StepParam``; ``unknown_typed`` lists params whose ``type`` is outside
    ``KNOWN_PARAM_TYPES``. Both must be zero for P6.1 acceptance.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    steps = data["steps"] if isinstance(data, dict) and "steps" in data else data
    params_in_json = sum(len(s.get("params", [])) for s in steps)

    entries = [CatalogEntry.from_dict(s) for s in steps]
    params_loaded = sum(len(e.params) for e in entries)
    unknown = [
        (e.name, p.xml_element, p.type)
        for e in entries
        for p in e.params
        if not p.is_known_type
    ]
    return LoadReport(
        entries=len(entries),
        params_in_json=params_in_json,
        params_loaded=params_loaded,
        unknown_typed=unknown,
    )


# ===========================================================================
# XML → HR grammar engine (P6.2)
# ===========================================================================
# A faithful port of the reference converter's XML→HR path: one function per
# param type computes that param's HR fragment, and an orchestrator renders the
# fragments in hrSlot order with discriminator / visibility / label handling.
#
# Reads directly from the parsed fmxmlsnippet ``<Step>`` element (an
# ElementTree Element), mirroring the reference — which threads values inline
# rather than through an intermediate struct — so the output is byte-faithful to
# the proven grammar. The complex facets the P6.1 types keep in ``StepParam.raw``
# (bitmask tables, attrGroup field specs, repeat/list entry shapes) are read from
# there; the simple values use the typed fields.
#
# The reference's per-param HR computation maps here to ``compute_param_hr`` and
# its whole-step renderer to ``render_step_hr``. Control-flow steps are NOT
# rendered here — they stay hand-coded in snippet_to_hr.py (the sanctioned
# exception).


def _ci_equals(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _child_text(parent: ET.Element | None, name: str) -> str:
    if parent is None:
        return ""
    c = parent.find(name)
    return c.text if (c is not None and c.text) else ""


def _repetition_calc_of(fld: ET.Element, parent: ET.Element | None) -> str:
    """The calculation of FileMaker's calculated-repetition form for ``fld``:
    ``repetition="0"`` with a ``<Repetition><Calculation>`` as its next element
    sibling under ``parent``. Empty otherwise (FileMaker ignores such a sibling
    unless the ``<Field>`` says ``"0"``)."""
    if parent is None or fld.get("repetition") != "0":
        return ""
    kids = list(parent)
    for i, k in enumerate(kids):
        if k is fld:
            if i + 1 < len(kids) and kids[i + 1].tag == "Repetition":
                return _child_text(kids[i + 1], "Calculation")
            return ""
    return ""


# FileMaker's drawing of a field reference that names a table occurrence but no
# field: it stores <Field table="T" id="0" name=""/> and draws `T::<Field Missing>`.
# The emit side reads the sentinel back to the empty name.
FIELD_MISSING_SENTINEL = "<Field Missing>"

# FileMaker's drawing of a field reference with no table and no field: it stores
# <Field table="" id="0" name=""/> and draws `<Table Missing>` in the slot of a
# single-field param (params marked hrNoTableBlank draw nothing). An absent
# <Field> draws no field token. The emit side reads the sentinel back to the
# empty reference.
TABLE_MISSING_SENTINEL = "<Table Missing>"


def _field_ref_hr(table: str, name: str) -> str:
    """The HR token of a stored ``<Field table id name/>``, drawn the way FileMaker
    draws it: ``Table::Field``, the bare name when there is no table, and
    ``Table::<Field Missing>`` when there is a table but no field. No table and no
    field is the unconfigured default and yields no token."""
    if name:
        return name if not table else table + "::" + name
    if table:
        return table + "::" + FIELD_MISSING_SENTINEL
    return ""


def _field_param_ref_hr(param: StepParam, table: str, name: str) -> str:
    """A single-field param's stored reference, drawn as FileMaker draws it:
    ``_field_ref_hr``, plus ``<Table Missing>`` for no table and no field unless
    the catalog marks the param ``hrNoTableBlank``. Field lists, find requests and
    group fields keep ``_field_ref_hr``: their unconfigured entries draw nothing."""
    if not table and not name and not param.raw.get("hrNoTableBlank"):
        return TABLE_MISSING_SENTINEL
    return _field_ref_hr(table, name)


def _with_repetition_suffix(ref: str, fld: ET.Element, parent: ET.Element | None = None) -> str:
    """Append FileMaker's ``[N]`` repetition subscript to a field/variable ref.

    FileMaker serializes a repetition as ``repetition="N"`` on the ``<Field>``
    (``<Field table="T" id="1" repetition="2" name="F"/>``, or
    ``<Field repetition="2">$var</Field>``) and writes the attribute only for
    N > 1; its Script Workspace line shows ``T::F[2]`` / ``$var[2]``. Repetition 1
    is the default and carries no subscript. A calculated repetition is
    ``repetition="0"`` plus a ``<Repetition><Calculation>`` sibling (``parent`` is
    the ``<Field>``'s parent), shown as ``T::F[calc]``; a calc ending in a ``//``
    comment holding ``]`` or ``;`` gets a line break before the closing ``]``.
    """
    if not ref:
        return ref
    calc = _repetition_calc_of(fld, parent)
    if calc:
        return ref + "[" + calc + hr_sep(calc, "]")
    try:
        rep = int(fld.get("repetition", "0") or "0")
    except ValueError:
        rep = 0
    if rep > 1:
        return ref + "[" + str(rep) + "]"
    return ref


def _child_attr(parent: ET.Element | None, name: str, attr: str) -> str:
    if parent is None:
        return ""
    c = parent.find(name)
    return c.get(attr, "") if c is not None else ""


def _nested_text(parent: ET.Element | None, child: str, grand: str) -> str:
    if parent is None:
        return ""
    c = parent.find(child)
    if c is None:
        return ""
    g = c.find(grand)
    return g.text if (g is not None and g.text) else ""


def _aliased_child(
    parent: ET.Element | None, name: str, aliases: dict[str, list[str]] | None
) -> ET.Element | None:
    """Find a child by its canonical ``name``; if absent and the name carries
    declared aliases, retry each legacy synonym in order (element-alias grammar).
    Returns the first match, or ``None``."""
    if parent is None:
        return None
    c = parent.find(name)
    if c is not None:
        return c
    for syn in (aliases or {}).get(name, ()):
        c = parent.find(syn)
        if c is not None:
            return c
    return None


def _descend_path(
    step: ET.Element | None, path: str, aliases: dict[str, list[str]] | None = None
) -> ET.Element | None:
    """Follow a '/'-delimited child path from ``step`` (direct children only).
    Each segment is resolved alias-tolerantly (element-alias grammar)."""
    n = step
    for seg in path.split("/"):
        if not seg:
            continue
        if n is None:
            return None
        n = _aliased_child(n, seg, aliases)
    return n


def _split_element_attr(xml_element: str) -> tuple[bool, str, str]:
    """G11 'Elem/@attr' notation → (True, 'Elem', 'attr'); else (False, '', '')."""
    pos = xml_element.find("/@")
    if pos == -1:
        return False, "", ""
    return True, xml_element[:pos], xml_element[pos + 2 :]


def _hr_join(items: list[str], sep: str) -> str:
    """Join HR tokens with ``sep``, breaking the line before a separator that a
    trailing ``//`` comment would otherwise swallow (``calc_literal.hr_sep``)."""
    out = ""
    for i, item in enumerate(items):
        if i:
            out += hr_sep(items[i - 1], sep)
        out += item
    return out


def _join_with_comma(items: list[str]) -> str:
    return _hr_join(items, ", ")


# --- group-value quoting (attr / text kinds) --------------------------------
# A group body (``k1=v1, k2=v2`` / ``entry | entry``) is split back by splitters
# that step over calculation text literals (``calc_literal``), so a quoted value is
# written as one: an embedded ``"`` is doubled, any other delimiter (``“ ” „ ‟ ＂``)
# and any backslash form (``\ ＼ ﹨``) that would otherwise escape the next character,
# or the closing quote, are backslash-escaped. A value needs quoting when it carries
# a top-level comma, any of the six delimiters, or leading/trailing whitespace
# (Execute SQL's tab ``FieldDelimiter``). ``calc`` children are never quoted: a
# well-formed calculation is already literal/paren-nested.
def _needs_group_quote(v: str) -> bool:
    if not v:
        return False
    ws = " \t\n\r"
    if v[0] in ws or v[-1] in ws:
        return True
    return "," in v or any(c in CALC_QUOTES for c in v)


def _group_quote_value(v: str) -> str:
    if not _needs_group_quote(v):
        return v
    out = ['"']
    last = len(v) - 1
    for i, c in enumerate(v):
        if c == '"':
            out.append('""')
        elif c in CALC_QUOTES or (c in CALC_BACKSLASHES and (i == last or is_calc_escape(v, i))):
            out.append("\\" + c)
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _group_unquote_value(v: str) -> str:
    """Read back a ``_group_quote_value`` token: one well-formed calculation literal
    spanning the whole token is decoded (doubled delimiters and backslash escapes
    resolved); any other ``"``-wrapped token keeps the historical reading (strip the
    outer quotes, undouble ``""``); anything else is returned as is."""
    if len(v) < 2 or v[0] != '"' or v[-1] != '"':
        return v
    end, closed, decoded = skip_calc_literal(v, 0)
    if closed and end == len(v):
        return decoded
    return v[1:-1].replace('""', '"')


# --- discriminator / visibility predicates (mirror the reference) -----------
def is_governing_discriminator(param: StepParam) -> bool:
    return param.type == "enum" and bool(param.discriminator_values)


def is_driven_discriminator(entry: CatalogEntry, param: StepParam) -> bool:
    if not param.xml_element:
        return False
    return any(
        q.type == "layout" and q.discriminator == param.xml_element
        for q in entry.params
    )


def value_reveals_companion(discrim: StepParam, value: str, elem: str) -> bool:
    """Whether governing discriminator ``discrim``'s XML ``value`` reveals ``elem``.

    Ports the reference converter's companion-reveal predicate (TS
    ``valueRevealsCompanion``); shared by the HR→XML emit engine.
    """
    branch = discrim.discriminator_values.get(value)
    if branch is None:
        return False
    return elem in branch.reveal


def candidate_hr_labels(param: StepParam) -> list[str]:
    """Every label a param's HR token may carry — base ``hr_label`` plus any
    ``hr_label_when`` variant labels — longest first (lexicographic tiebreak),
    duplicates removed. Ports the reference converter's candidate-label rule (TS
    ``candidateHrLabels``); the HR parse matcher tries each so a variant-labeled
    value round-trips.
    """
    labels: list[str] = []
    if param.hr_label:
        labels.append(param.hr_label)
    for v in param.hr_label_when:
        if v.hr_label:
            labels.append(v.hr_label)
    labels.sort(key=lambda s: (-len(s), s))
    out: list[str] = []
    for i, lbl in enumerate(labels):
        if i == 0 or lbl != labels[i - 1]:
            out.append(lbl)
    return out


def governing_discriminator_for(
    entry: CatalogEntry, companion: StepParam
) -> StepParam | None:
    if not companion.xml_element:
        return None
    ck = param_key(companion)
    for p in entry.params:
        if p is companion or not is_governing_discriminator(p):
            continue
        for branch in p.discriminator_values.values():
            if ck in branch.reveal:
                return p
    return None


def read_enum_raw_value(
    step: ET.Element, p: StepParam, aliases: dict[str, list[str]] | None = None
) -> str:
    base = step if not p.parent_element else _descend_path(step, p.parent_element, aliases)
    el = _aliased_child(base, p.xml_element, aliases)
    if el is None:
        return ""
    if p.enum_style == "text":
        return el.text or ""
    attr = p.xml_attr or "value"
    return el.get(attr, "")


def read_gate_raw_value(entry: CatalogEntry, step: ET.Element, p: StepParam) -> str:
    """A gate's raw value: ``read_enum_raw_value``, or a projection's fields."""
    if not p.group_fields:
        return read_enum_raw_value(step, p, entry.element_aliases)
    owner = projection_owner(entry, p)
    if owner is None:
        return ""
    base = step if not p.parent_element else _descend_path(step, p.parent_element, entry.element_aliases)
    return read_projected_value(base.find(p.xml_element) if base is not None else None, owner, p)


def effective_hr_label(entry: CatalogEntry, step: ET.Element, param: StepParam) -> str:
    for variant in param.hr_label_when:
        for q in entry.params:
            if param_key(q) != variant.param:
                continue
            v = read_gate_raw_value(entry, step, q) or (q.default_value or "")
            if v in variant.values:
                return variant.hr_label or ""
            break
    return param.hr_label or ""


def param_visible(entry: CatalogEntry, step: ET.Element, param: StepParam) -> bool:
    vw = param.visible_when
    if vw is None or not vw.param:
        return True
    for q in entry.params:
        if param_key(q) != vw.param:
            continue
        v = read_gate_raw_value(entry, step, q) or (q.default_value or "")
        return v in vw.values
    return True


def hr_param_order(entry: CatalogEntry) -> list[int]:
    """Indices in HR render order — catalog order unless some param sets hrSlot."""
    order = list(range(len(entry.params)))
    if not any(p.hr_slot is not None and p.hr_slot >= 0 for p in entry.params):
        return order

    def key(i: int) -> int:
        s = entry.params[i].hr_slot
        return s if (s is not None and s >= 0) else i

    return sorted(order, key=key)  # Python's sort is stable


# --- group / repeat / list renderers ----------------------------------------
def _group_at_default(node: ET.Element, fields: list[dict]) -> bool:
    """Whether a group element is exactly its catalog default (``hrOmitAtDefault``).

    Every non-optional attr present at its ``defaultValue``, every optional attr
    absent, a calc / text child present holding its ``defaultValue`` when it has
    one and absent otherwise (a calc's ``requireAttr`` reading its presence), no
    field / script child, every sub-group present and at default. The element
    the emitter writes back for an absent token.
    """
    for f in fields:
        kind = f.get("kind", "")
        default = f.get("defaultValue") or ""
        if kind == "attr":
            a = node.get(f.get("xmlAttr", ""))
            if f.get("optional"):
                if a is not None:
                    return False
                continue
            if a is None or a != default:
                return False
        elif kind == "text":
            child = node.find(f.get("childElement", ""))
            if not default:
                if child is not None:
                    return False
                continue
            if child is None or (child.text or "") != default:
                return False
        elif kind == "calc":
            child_el = f.get("childElement", "")
            calc = node.find("Calculation") if not child_el else node.find(child_el)
            has = calc is not None
            if has != bool(default):
                return False
            if has:
                v = (calc.text or "") if not child_el else _child_text(calc, "Calculation")
                if v != default:
                    return False
            req = f.get("requireAttr", "")
            if req and node.get(req) != ("True" if has else "False"):
                return False
        elif kind == "group":
            sub = node.find(f.get("element", ""))
            if sub is None or not _group_at_default(sub, f.get("fields", [])):
                return False
        elif kind == "field":
            if node.find("Field") is not None:
                return False
        elif kind == "fieldOrVariable":
            fld = node.find("Field")
            if fld is None or (fld.text or "").strip() or fld.get("name", ""):
                return False
        elif kind == "script":
            if node.find(f.get("element", "")) is not None:
                return False
    return True


def _render_group_element(node: ET.Element, fields: list[dict]) -> str:
    """Render one attrGroup element to its HR body ('k=v, k=v'); mirrors the ref."""
    parts: list[str] = []
    for f in fields:
        kind = f.get("kind", "")
        key = f.get("key", "")
        if kind == "attr":
            a = node.get(f.get("xmlAttr", ""))
            if a is not None:
                parts.append(key + "=" + _group_quote_value(a))
        elif kind == "text":
            child = node.find(f.get("childElement", ""))
            if child is not None:
                parts.append(key + "=" + _group_quote_value(child.text or ""))
        elif kind == "calc":
            child_el = f.get("childElement", "")
            if not child_el:
                c = node.find("Calculation")
                if c is not None:
                    parts.append(key + "=" + (c.text or ""))
            else:
                child = node.find(child_el)
                if child is not None:
                    parts.append(key + "=" + _child_text(child, "Calculation"))
        elif kind == "field":
            fld = node.find("Field")
            if fld is not None:
                table = fld.get("table", "")
                name = fld.get("name", "")
                ref = _field_ref_hr(table, name)
                if ref:
                    parts.append(key + "=" + _with_repetition_suffix(ref, fld))
        elif kind == "script":
            sc = node.find(f.get("element", ""))
            if sc is not None:
                name = sc.get("name", "")
                if name:
                    parts.append(key + "=" + _group_quote_value(name))
        elif kind == "fieldOrVariable":
            fld = node.find("Field")
            if fld is not None:
                text = (fld.text or "").strip()
                if text:
                    parts.append(key + "=" + _group_quote_value(_with_repetition_suffix(text, fld)))
                else:
                    table = fld.get("table", "")
                    name = fld.get("name", "")
                    ref = _field_ref_hr(table, name)
                    if ref:
                        parts.append(
                            key + "=" + _group_quote_value(_with_repetition_suffix(ref, fld))
                        )
        elif kind == "group":
            sub = node.find(f.get("element", ""))
            if sub is not None:
                parts.append(
                    key + "=(" + _render_group_element(sub, f.get("fields", [])) + ")"
                )
    return _join_with_comma(parts)


def _render_repeat_group(container: ET.Element, param: StepParam) -> str:
    entry_el = param.raw.get("entryElement", "")
    fields = param.raw.get("fields", [])
    entries = [
        _render_group_element(e, fields) for e in container.findall(entry_el)
    ]
    return _hr_join(entries, " | ")


def _render_repeat_group_positional(container: ET.Element, param: StepParam) -> str | None:
    """``hrPositionalKey``: FileMaker's own form for a repeatGroup -- one BARE
    token per non-empty entry (that entry's positional field), joined ' ; '
    (Show Custom Dialog ``... ; $a ; Invoices::Number``). ``None`` when that form
    would lose information (another entry field off its default, an empty entry
    before a filled one, or an entry count re-emit would not reproduce), so the
    caller keeps the labeled body form. Mirrors the reference converter."""
    key = param.raw.get("hrPositionalKey", "")
    fields = param.raw.get("fields", [])
    pos = next((f for f in fields if f.get("key") == key), None)
    if pos is None:
        return None
    tokens: list[str] = []
    count = 0
    saw_empty = False
    for e in container.findall(param.raw.get("entryElement", "")):
        count += 1
        for f in fields:
            if f is pos:
                continue
            got = _render_group_element(e, [f])
            dflt = (f.get("key", "") + "=" + _group_quote_value(f.get("defaultValue", "") or "")
                    if f.get("kind") == "attr" else "")
            if got != dflt:
                return None
        body = _render_group_element(e, [pos])
        prefix = key + "="
        v = _group_unquote_value(body[len(prefix):]) if body.startswith(prefix) else ""
        if not v.strip():
            saw_empty = True
            continue
        if saw_empty:
            return None
        tokens.append(v)
    if count == 0:
        return ""
    if not tokens:
        return None
    rc = int(param.raw.get("repeatCount", 0) or 0)
    expected = max(rc, len(tokens)) if rc > 0 else len(tokens)
    if count != expected:
        return None
    return _hr_join(tokens, " ; ")


def _render_field_list(container: ET.Element, param: StepParam) -> str:
    entry_el = param.raw.get("entryElement", "")
    entry_attr = param.raw.get("entryAttr", "")
    field_wrapper = param.raw.get("fieldWrapper", "")
    tokens: list[str] = []

    def emit(entry_node: ET.Element | None, field_node: ET.Element | None) -> None:
        if field_node is None:
            return
        table = field_node.get("table", "")
        name = field_node.get("name", "")
        fieldref = _field_ref_hr(table, name)
        token = _group_quote_value(fieldref)
        if entry_attr:
            attr_node = field_node if not entry_el else entry_node
            if attr_node is not None:
                token += "=" + (attr_node.get(entry_attr, "") or "")
        tokens.append(token)

    if entry_el:
        for e in container.findall(entry_el):
            if field_wrapper:
                w = e.find(field_wrapper)
                fld = w.find("Field") if w is not None else None
            else:
                fld = e.find("Field")
            emit(e, fld)
    else:
        for fld in container.findall("Field"):
            emit(None, fld)
    return ", ".join(tokens)


# --- bitmask helpers --------------------------------------------------------
def _bitmask_style_by_xml(param: StepParam, v: str) -> dict | None:
    for s in param.raw.get("bitmaskStyles", []):
        if s.get("xmlValue") == v:
            return s
    return None


def _bitmask_mask_for_flags(param: StepParam, labels: list[str]) -> int:
    m = 0
    for lbl in labels:
        for f in param.raw.get("bitmaskFlags", []):
            if _ci_equals(f.get("hrLabel", ""), lbl):
                m |= f.get("bit", 0)
                break
    return m


# --- per-param HR fragment ------------------------------------------------
def compute_param_hr(entry: CatalogEntry, step: ET.Element, param: StepParam) -> str:
    """Compute one param's HR fragment ('' = no token), as the reference does."""
    val = ""
    base = (
        step
        if not param.parent_element
        else _descend_path(step, param.parent_element, entry.element_aliases)
    )
    if base is None:
        base = step  # a missing wrapper reads nothing; keep base usable
    is_elem_attr, g11_elem, g11_attr = _split_element_attr(param.xml_element)
    label = param.hr_label or ""
    ptype = param.type

    if ptype == "boolean":
        battr = g11_attr if is_elem_attr else (param.xml_attr or "state")
        belem = g11_elem if is_elem_attr else param.xml_element
        state = _child_attr(base, belem, battr)
        if state:
            state_true = state == "True"
            if param.inverted_hr:
                state_true = not state_true
            if param.flag_style:
                if state_true and label:
                    val = label
            elif param.hr_enum_values:
                val = param.hr_enum_values.get(state, "On" if state_true else "Off")
                if label:
                    val = label + ": " + val
            else:
                val = "On" if state_true else "Off"
                if label:
                    val = label + ": " + val
    elif ptype == "enum" and is_driven_discriminator(entry, param):
        pass  # silent companion of a layout param
    elif ptype == "enum":
        if param.group_fields:
            owner = projection_owner(entry, param)
            if owner is not None:
                val = read_projected_value(base.find(param.xml_element), owner, param)
        elif param.enum_style == "text":
            val = _child_text(base, param.xml_element)
        else:
            eattr = g11_attr if is_elem_attr else (param.xml_attr or "value")
            eelem = g11_elem if is_elem_attr else param.xml_element
            val = _child_attr(base, eelem, eattr)
        # P7.2: FileMaker renders no token at all for some of an enum's values --
        # it shows the companion the value reveals, in that companion's own slot
        # (`Go to Record/Request/Page [ With dialog: Off ; 1 ]` is the
        # ByCalculation form, and `1` is the row calculation, not the location).
        if (val or param.default_value) in param.hr_hidden_values:
            val = ""
        if not param.flag_style and param.hr_enum_values:
            mapped = param.hr_enum_values.get(val)
            if mapped:
                val = mapped
        if param.flag_style:
            val = (
                label
                if (val and val != (param.default_value or "") and label)
                else ""
            )
        elif val and label:
            # P5.2d: the label may vary with a sibling enum's value (hrLabelWhen;
            # Insert from Device's ``Flash:`` / ``Flashlight:``).
            val = effective_hr_label(entry, step, param) + ": " + val
    elif ptype == "calculation":
        val = _child_text(base, "Calculation")
        # Optional labeled calcs keep their label; legacy positional calcs stay bare
        # unless the catalog opts one in with hr_labeled (Rename File's "New name:").
        if val and (param.omit_when_empty or param.hr_labeled) and label:
            val = label + ": " + val
    elif ptype == "attrGroup":
        group_node = base.find(param.xml_element)
        # hrOmitAtDefault: a group at its catalog default prints nothing
        # (FileMaker draws `Speak [ ]` / `Create PDF [ ]`); the emitter writes
        # the default element back.
        if (group_node is not None and param.raw.get("hrOmitAtDefault")
                and _group_at_default(group_node, param.raw.get("fields", []))):
            group_node = None
        if group_node is not None:
            # A field a visible display projection prints as its own token is
            # left out of the body; one whose projection is hidden stays.
            shown = {
                k
                for q in entry.params
                if q.group_fields and q.xml_element == param.xml_element
                and (q.parent_element or "") == (param.parent_element or "")
                and param_visible(entry, step, q)
                for k in q.group_fields
            }
            fields = [f for f in param.raw.get("fields", []) if f.get("key") not in shown]
            inner = _render_group_element(group_node, fields)
            if inner:
                val = inner if not label else (label + ": " + inner)
    elif ptype == "bitmaskGroup":
        g = base.find(param.xml_element)
        styles = param.raw.get("bitmaskStyles", [])
        if g is not None and styles:
            xml_style = g.get(param.raw.get("bitmaskStyleAttr", ""), "")
            st = _bitmask_style_by_xml(param, xml_style) or styles[0]
            chrome = 0
            for f in param.raw.get("bitmaskFlags", []):
                yn = g.get(f.get("xmlAttr", ""), "No")
                if _ci_equals(yn, "Yes"):
                    chrome |= f.get("bit", 0)
            chrome &= _bitmask_mask_for_flags(param, st.get("legalFlags", []))
            parts: list[str] = []
            if label:
                parts.append(label + ": " + st.get("hrToken", ""))
            if chrome != _bitmask_mask_for_flags(param, st.get("defaultFlags", [])):
                lst = [
                    f.get("hrLabel", "")
                    for f in param.raw.get("bitmaskFlags", [])
                    if chrome & f.get("bit", 0)
                ]
                controls_label = param.raw.get("hrControlsLabel", "")
                parts.append(controls_label + ": " + (", ".join(lst) if lst else "None"))
            val = " ; ".join(parts)
    elif ptype == "repeatGroup":
        container = base.find(param.xml_element)
        positional = None
        if container is not None and param.raw.get("hrPositionalKey"):
            positional = _render_repeat_group_positional(container, param)
        if positional is not None:
            val = positional  # FileMaker's own bare form (lossless here)
        elif container is not None:
            inner = _render_repeat_group(container, param)
            # FileMaker's default content prints no token; emit restores it.
            if param.raw.get("hrDefault") and inner == param.raw.get("hrDefault"):
                inner = ""
            if inner:
                val = inner if not label else (label + ": " + inner)
    elif ptype == "fieldList":
        container = base.find(param.xml_element)
        if container is not None:
            inner = _render_field_list(container, param)
            if inner:
                val = inner if not label else (label + ": " + inner)
    elif ptype == "namedCalc":
        wrapper = param.wrapper_element or param.xml_element
        # Element-alias grammar: match the wrapper by its canonical name,
        # falling back to any declared legacy synonym (e.g. AccoutName).
        wnode = _aliased_child(base, wrapper, entry.element_aliases)
        val = _child_text(wnode, "Calculation") if wnode is not None else ""
        # hr_bare params (Show Custom Dialog's Title/Message) print positionally
        # with no label, mirroring FileMaker; every other namedCalc keeps its label.
        if val and not param.hr_bare:
            lbl = effective_hr_label(entry, step, param)
            if lbl:
                val = lbl + ": " + val
    elif ptype == "parametersList":
        wrapper_name = param.xml_element or "Parameters"
        wrapper = base.find(wrapper_name)
        if wrapper is not None:
            items = [
                _child_text(p, "Calculation")
                for p in wrapper.findall("P")
                if _child_text(p, "Calculation")
            ]
            if items:
                val = _join_with_comma(items)
                if label:
                    val = label + ": " + val
    elif ptype == "findRequests":
        query = base.find("Query")
        if query is not None:
            rows: list[str] = []
            for rr in query.findall("RequestRow"):
                op = rr.get("operation", "")
                crits: list[str] = []
                for cr in rr.findall("Criteria"):
                    fld = cr.find("Field")
                    table = fld.get("table", "") if fld is not None else ""
                    name = fld.get("name", "") if fld is not None else ""
                    text = _child_text(cr, "Text")
                    fieldref = _field_ref_hr(table, name)
                    crits.append(text if not fieldref else (fieldref + ": " + text))
                joined = " & ".join(crits)
                if op == "Exclude":
                    joined = "Omit " + joined
                rows.append(joined)
            val = " | ".join(rows)
            if val and label:
                val = label + ": " + val
    elif ptype == "fieldOrVariable":
        # A typed same-element child (typeAttr/typeValue) is located by its
        # discriminator attribute rather than by position, so two typed <Field>
        # siblings under one wrapper never read each other's node.
        type_attr = param.raw.get("typeAttr")
        if type_attr:
            field_node = base.find(
                "Field[@{}='{}']".format(type_attr, param.raw.get("typeValue", ""))
            )
        else:
            field_node = base.find(param.xml_element)
            if field_node is None:
                field_node = base.find("Field")
        if field_node is not None:
            table = field_node.get("table", "")
            name = field_node.get("name", "")
            text = field_node.text or ""
            if text and text[0] == "$":
                val = text
            elif name:
                val = _field_ref_hr(table, name)
            elif text:
                val = text
            else:
                # Table::<Field Missing>, <Table Missing>, or none
                val = _field_param_ref_hr(param, table, name)
            val = _with_repetition_suffix(val, field_node, base)
            if val and label:
                val = label + ": " + val
    elif ptype == "flagElement":
        if base.find(param.xml_element) is not None and label:
            val = label
    elif ptype == "calc":
        val = _child_text(base, "Calculation")
        # hr_labeled: FileMaker labels this calc for some steps (inverse of bare).
        if val and param.hr_labeled and label:
            val = label + ": " + val
    elif ptype == "field":
        type_attr = param.raw.get("typeAttr")
        if type_attr:
            field_node = base.find(
                "Field[@{}='{}']".format(type_attr, param.raw.get("typeValue", ""))
            )
        else:
            field_node = base.find(param.xml_element)
            if field_node is None:
                field_node = base.find("Field")
        if field_node is not None:
            table = field_node.get("table", "")
            name = field_node.get("name", "")
            val = _with_repetition_suffix(
                _field_param_ref_hr(param, table, name), field_node, base
            )
            # hr_labeled: label the field where FileMaker does (inverse of bare).
            if val and param.hr_labeled and label:
                val = label + ": " + val
    elif ptype in ("tableRef", "tableOccurrence"):
        table_node = base.find("Table")
        if table_node is not None:
            name = table_node.get("name", "")
            if name:
                # Labeled (Go to Related Record's "From table") renders
                # "Label: name"; label-less (Fine-Tune Model's positional
                # training table) renders the quoted TO name — the quoted
                # positional spelling FM uses, mirroring `layout`. hr_quoted: FileMaker draws
                # the labeled name quoted (Save Records as JSONL's
                # ``Table: “Invoices”``); write it as one escaped literal.
                if param.hr_quoted:
                    q = quote_calc_literal(name)
                    val = q if not label else (label + ": " + q)
                else:
                    val = ('"' + name + '"') if not label else (label + ": " + name)
    elif ptype == "fileReference":
        fr_node = base.find(param.xml_element)
        if fr_node is not None:
            path = _child_text(fr_node, "UniversalPathList")
            if path:
                val = path if not label else (label + ": " + path)
    elif ptype == "reference":
        ref_node = base.find(param.xml_element)
        if ref_node is not None:
            name = ref_node.get("name", "")
            # A fixed-id built-in reads by its id (FM's [File Default] is id="0" name="").
            ref_id = ref_node.get("id")
            if ref_id is not None and param.hr_enum_values.get(ref_id):
                name = param.hr_enum_values[ref_id]
            if name:
                val = ('"' + name + '"') if not label else (label + ": " + name)
    elif ptype == "layout":
        layout_node = base.find("Layout")
        if param.discriminator:
            dest = _child_attr(base, param.discriminator, "value")
            if _ci_equals(dest, "OriginalLayout"):
                val = "original layout"
            elif _ci_equals(dest, "CurrentLayout"):
                # FileMaker's own spelling, angle-bracketed (measured 26.0.1).
                val = "<Current Layout>"
            elif _ci_equals(dest, "LayoutNameByCalc"):
                # FileMaker prints the calculation bare, with no keyword.
                val = _child_text(layout_node, "Calculation")
            elif _ci_equals(dest, "LayoutNumberByCalc"):
                # Deliberately NOT FileMaker's spelling: FM renders this exactly
                # like LayoutNameByCalc, so mirroring it would collapse the two.
                val = "by number: " + _child_text(layout_node, "Calculation")
            else:
                name = layout_node.get("name", "") if layout_node is not None else ""
                val = ('"' + name + '"') if name else "<unknown>"
            # The label belongs to THIS param ("Using layout: <token>"); the
            # sibling discriminator carries none, or the parse reads label plus
            # payload as a literal layout name.
            if val and label:
                val = label + ": " + val
        elif layout_node is not None or param.required:
            name = layout_node.get("name", "") if layout_node is not None else ""
            tok = ""
            if name:
                tok = '"' + name + '"'
            elif param.required:
                tok = "<unknown>"
            if tok:
                val = tok if not label else (label + ": " + tok)
    elif ptype == "script":
        script_node = base.find("Script")
        if script_node is not None:
            name = script_node.get("name", "")
            if name:
                val = '"' + name + '"'
        # hr_labeled: FileMaker labels the script token for some steps (Configure
        # Region Monitor Script's "Script:") while others print it bare (Perform
        # Script). Prefix the label when the catalog opts in. Inverse of the default.
        if val and param.hr_labeled and label:
            val = label + ": " + val
    elif ptype in ("text", "name"):
        val = _child_text(base, param.xml_element)
        # hr_quoted: FileMaker displays this literal text quoted (``“x”``). Write it
        # as one escaped literal so an embedded quote, ``;`` or ``]`` cannot open a
        # literal or end the param/step on the way back.
        if val and param.hr_quoted:
            val = quote_calc_literal(val, '"', '"') if param.hr_quote_style == "straight" else quote_calc_literal(val)
        # A grouped text param carries its label; step-level free text stays bare
        # unless the catalog opts it in with hr_labeled (Delete File's "Target file:").
        if val and (param.parent_element or param.hr_labeled) and label:
            val = label + ": " + val

    return val


# --- empty positional slots (HR render) ----------------------------------------
# The HR->XML parser hands bare tokens to bare-rendering params by POSITION, so a
# bare param whose value is empty must still hold its slot whenever a later
# positional token is rendered -- otherwise that token slides into the empty
# param's slot (Set Next Serial Value with no field rendered ``[ 100 ]`` and read
# back as ``<Field name="100"/>`` with an empty calculation). A trailing empty
# slot is still dropped. Mirrors the reference converter.


def renders_bare_in_hr(param: StepParam) -> bool:
    """Whether a param renders bare (positional) in HR -- the pass-2 eligibility test."""
    if param.hr_bare:  # per-param opt-in: FileMaker prints it bare
        return True
    if not param.hr_label:
        return True
    # Per-param opt-in (inverse of hr_bare): a calc/field/script, step-level text or
    # legacy calculation FileMaker renders WITH its label is matched by that label,
    # never positionally.
    if param.hr_labeled:
        return False
    if param.type in ("calc", "field", "script"):
        return True
    if param.type == "layout":
        return not param.hr_label
    if param.type in ("text", "name") and not param.parent_element:
        return True
    return param.type == "calculation" and not param.omit_when_empty


def reads_positionally(entry: CatalogEntry, param: StepParam) -> bool:
    """Whether the HR parser reads ``param`` by position: pass-2 eligibility minus
    every param pass 1 resolves without a positional token."""
    if param.hr_hidden or param.type in ("complex", "bitmaskGroup"):
        return False
    if param.flag_style or param.type == "flagElement":
        return False
    if is_driven_discriminator(entry, param) or is_governing_discriminator(param):
        return False
    return renders_bare_in_hr(param)


def closed_vocab_enum(param: StepParam) -> bool:
    """An enum that declares its vocabulary (``enumValues``): the only kind the
    positional pass checks a token against, so a token naming one of its values
    identifies it. Port of ClosedVocabEnum."""
    return param.type == "enum" and bool(param.enum_values)


def positional_token_fits_param(param: StepParam, token: str) -> bool:
    """Whether a positional (bare) token may be assigned to ``param``. A
    closed-vocabulary enum only claims a token naming one of its values (or its
    ``hrEnumValues`` display spelling), so a token meant for a later bare param is
    not swallowed by position. Port of PositionalTokenFitsParam."""
    if not closed_vocab_enum(param):
        return True
    t = token.lower()
    if any(t == v.lower() for v in param.enum_values):
        return True
    return any(label and t == label.lower() for label in param.hr_enum_values.values())


def empty_holds_slot(entry: CatalogEntry, param: StepParam) -> bool:
    """Whether an EMPTY value of ``param`` renders as an empty slot when a later
    positional token follows. A closed-vocabulary enum is excluded: the parser only
    hands it a token naming one of its values, so an empty token would not fit."""
    if not reads_positionally(entry, param):
        return False
    return not closed_vocab_enum(param)


# A rendered token: (text, positional, holds_slot, closed_vocab).
HrToken = tuple[str, bool, bool, bool]


def _param_token(entry: CatalogEntry, param: StepParam, val: str) -> HrToken:
    positional = reads_positionally(entry, param)
    if param.type == "repeatGroup" and param.raw.get("hrPositionalKey") and val:
        label = param.hr_label or ""
        positional = not label or not val.lower().startswith((label + ":").lower())
    return (val, positional, empty_holds_slot(entry, param), closed_vocab_enum(param))


def shape_routed_values(entry: CatalogEntry, discrim: StepParam) -> tuple[str, str] | None:
    """A SHAPE-ROUTED governing discriminator: exactly two of its values render no
    token of their own and each reveal ONE bare companion, one of them
    ``hrQuoted``. FileMaker's line then shows only the companion (Perform
    AppleScript: Text mode prints the quoted literal, Calculation mode the
    calculation), so the reader tells the values apart by the token's shape
    (``reads_as_quoted_literal_text``). Returns ``(quoted_value, other_value)``,
    or None. Port of ShapeRoutedValues."""
    if not is_governing_discriminator(discrim):
        return None
    quoted = other = ""
    routed = 0
    for value, spec in discrim.discriminator_values.items():
        if spec.hr_token or spec.labeled or len(spec.reveal) != 1:
            continue
        c = next((q for q in entry.params if param_key(q) == spec.reveal[0]), None)
        if c is None or not renders_bare_in_hr(c):
            continue
        routed += 1
        if c.hr_quoted:
            quoted = value
        else:
            other = value
    if routed != 2 or not quoted or not other:
        return None
    return quoted, other


def render_discriminator_group_tokens(
    entry: CatalogEntry, step: ET.Element, param: StepParam
) -> list[HrToken]:
    """A governing discriminator's HR tokens. Port of RenderDiscriminatorGroupTokens."""
    value = read_enum_raw_value(step, param, entry.element_aliases) or (param.default_value or "")
    branch = param.discriminator_values.get(value)
    if branch is None:
        mapped = param.hr_enum_values.get(value) or value
        label = param.hr_label or ""
        # Unlabeled, pass 1 cannot claim it, so the parser reads it by position.
        return [(mapped, True, False, False)] if not label else [(label + ": " + mapped, False, False, False)]
    if branch.hr_token:
        return [(branch.hr_token, False, False, False)]
    parts: list[HrToken] = []
    if branch.labeled and param.hr_label:
        mapped = param.hr_enum_values.get(value) or value
        parts.append((param.hr_label + ": " + mapped, False, False, False))
    # A revealed companion is positional like any bare param, so it keeps its
    # empty slot (the caller drops it unless a later positional follows).
    # hr_reveal_separator: consecutive labeled (non-positional) companion tokens
    # merge into one, joined the way FileMaker draws them; the parser splits on
    # ``;`` regardless, so the merge is render-only.
    first_companion = len(parts)
    sep = param.hr_reveal_separator or ""

    def push_companion(tok: HrToken) -> None:
        # An empty token that holds no slot renders nothing; dropping it keeps the
        # companions on either side adjacent for the merge.
        if not tok[0] and not tok[2]:
            return
        if (sep and tok[0] and not tok[1] and len(parts) > first_companion
                and parts[-1][0] and not parts[-1][1]):
            last = parts[-1]
            parts[-1] = (last[0] + hr_sep(last[0], sep) + tok[0], last[1], last[2], last[3])
            return
        parts.append(tok)

    for elem in branch.reveal:
        for c in entry.params:
            if param_key(c) != elem:
                continue
            # An internal flag renders no token, revealed or not; its value is
            # derived on HR -> XML.
            if c.hr_hidden:
                break
            tok = _param_token(entry, c, compute_param_hr(entry, step, c))
            # Shape-routed: when the companion's token would read back as the other
            # value (a calculation that is one text literal reads as quoted text),
            # name the value explicitly before it. FileMaker's own line prints no
            # such token; it is ambiguous there.
            if (
                tok[0]
                and shape_routed_values(entry, param) is not None
                and reads_as_quoted_literal_text(tok[0].strip()) != bool(c.hr_quoted)
            ):
                parts.append((value, False, False, False))
            push_companion(tok)
            break
    return parts


def resolve_mirror_source(
    entry: CatalogEntry, param: StepParam
) -> tuple[StepParam, list[dict]] | None:
    """The source of a ``mirrorOf`` param: its owning attrGroup and the field chain.

    ``mirrorOf`` is ``"<group param key>/<field key>[/<field key>…]"``: a sibling
    attrGroup, then group fields ending at a ``calc`` field. FileMaker writes the
    param as a copy of that field (Create PDF's step-level ``<Calculation>`` is its
    Document Title). ``None`` when the path does not resolve.
    """
    segs = [s for s in str(param.raw.get("mirrorOf") or "").split("/") if s]
    if len(segs) < 2:
        return None
    owner = next((q for q in entry.params if q.type == "attrGroup" and not q.group_fields
                  and param_key(q) == segs[0]), None)
    if owner is None:
        return None
    fields = owner.raw.get("fields", []) or []
    chain: list[dict] = []
    for i, seg in enumerate(segs[1:]):
        hit = next((f for f in fields if _ci_equals(str(f.get("key", "")), seg)), None)
        last = i == len(segs) - 2
        if hit is None or hit.get("kind") != ("calc" if last else "group"):
            return None
        chain.append(hit)
        fields = hit.get("fields", []) or []
    return owner, chain


def _mirror_source_node(entry: CatalogEntry, step: ET.Element, owner: StepParam,
                        chain: list[dict], create: bool) -> ET.Element | None:
    """The element holding a mirrorOf source field's ``<Calculation>`` (its child
    element, or the group element for a bare calc); with ``create`` the missing
    path is appended."""
    def child_of(n: ET.Element, name: str) -> ET.Element | None:
        c = n.find(name)
        if c is None and create:
            c = ET.SubElement(n, name)
        return c

    n: ET.Element | None = step
    for seg in [s for s in (owner.parent_element or "").split("/") if s]:
        c = _aliased_child(n, seg, entry.element_aliases)
        n = c if c is not None else child_of(n, seg)
        if n is None:
            return None
    n = child_of(n, owner.xml_element)
    for f in chain:
        if n is None:
            return None
        if f.get("kind") == "group":
            n = child_of(n, f.get("element", ""))
        elif f.get("childElement"):
            n = child_of(n, f["childElement"])
    return n


def apply_mirror_fallback(entry: CatalogEntry, step: ET.Element) -> ET.Element | None:
    """A hidden ``mirrorOf`` copy prints nothing, so a step holding the copy but
    not its source field would lose the value. FileMaker fills the field from the
    copy on paste (Create PDF pasted with only the step-level ``<Calculation>``
    comes back with that Document Title; the field wins when both are set, FM
    26.0.3). Returns a copy of ``step`` with the field written from the copy, or
    ``None`` when nothing applies."""
    out: ET.Element | None = None
    for p in entry.params:
        if not p.raw.get("mirrorOf"):
            continue
        src = resolve_mirror_source(entry, p)
        if src is None:
            continue
        owner, chain = src
        s = out if out is not None else step
        base = s if not p.parent_element else _descend_path(s, p.parent_element, entry.element_aliases)
        own = base.find(p.xml_element) if base is not None else None
        v = "".join(own.itertext()) if own is not None else ""
        if not v.strip():
            continue
        have = _mirror_source_node(entry, s, owner, chain, False)
        if have is not None and have.find("Calculation") is not None:
            continue
        if out is None:
            out = copy.deepcopy(step)
        target = _mirror_source_node(entry, out, owner, chain, True)
        if target is not None:
            ET.SubElement(target, "Calculation").text = v
    return out


def render_step_hr(entry: CatalogEntry, step: ET.Element) -> str:
    """Render a full step to its HR bracket line, as the reference does.

    Returns ``entry.name`` alone when no param contributes a token, else
    ``Name [ tok ; tok ; … ]``. An empty positional slot followed by a later
    positional token renders as an empty token (``[  ; 100 ]``). Does not handle
    control-flow steps.
    """
    # A step holding only a mirrorOf copy renders as FileMaker stores it after
    # paste: with the source field filled from the copy.
    mirrored = apply_mirror_fallback(entry, step)
    if mirrored is not None:
        return render_step_hr(entry, mirrored)
    parts: list[str] = []
    fm_empty: set[int] = set()  # indexes of hrEmptySlotWith empty slots
    pending_empty = 0

    def add(tok: HrToken) -> None:
        nonlocal pending_empty
        text, positional, holds_slot, closed_vocab = tok
        if not text:
            if holds_slot:
                pending_empty += 1
            return
        if positional:
            # A closed-vocabulary enum's value names its own slot: the parser skips
            # the free params left out ahead of it (the missing-slot rule in
            # match_param_values), so those empty slots are not written, as on
            # FileMaker's line (Save a Copy as ``[ copy of current file ; … ]``).
            if closed_vocab:
                pending_empty = 0
            parts.extend([""] * pending_empty)
            pending_empty = 0
        parts.append(text)

    for pi in hr_param_order(entry):
        param = entry.params[pi]
        if param.hr_hidden:
            continue
        if governing_discriminator_for(entry, param):
            continue
        if not param_visible(entry, step, param):
            continue
        if is_governing_discriminator(param):
            for tok in render_discriminator_group_tokens(entry, step, param):
                add(tok)
        else:
            tok = _param_token(entry, param, compute_param_hr(entry, step, param))
            if not tok[0] and parts and _empty_slot_sibling_present(entry, step, param):
                # FileMaker draws this param's empty slot, even last, when the
                # sibling it stands for is present (Export Records with no path
                # but a <Profile>: ``Create folders: On ; ]``).
                parts.extend([""] * pending_empty)
                pending_empty = 0
                fm_empty.add(len(parts))
                parts.append("")
                continue
            add(tok)
    if not parts:
        # FileMaker draws the option brackets of every step that has options,
        # even when none of them shows (``Perform Find [ ]``, ``Go to Field [ ]``);
        # only a step with no options at all draws bare (``Beep``). HR->XML reads
        # both forms the same.
        return entry.name + " [ ]" if entry.params else entry.name
    # An hrEmptySlotWith slot draws as FileMaker draws it: a bare ``;``.
    out = entry.name + " [ "
    for i, item in enumerate(parts):
        if i:
            out += " ;" if i in fm_empty else hr_sep(parts[i - 1], " ; ")
        out += item
    if len(parts) - 1 in fm_empty:
        return out + " ]"
    return out + hr_sep(parts[-1], " ]")


def _empty_slot_sibling_present(entry: CatalogEntry, step: ET.Element, param: StepParam) -> bool:
    """hrEmptySlotWith: whether the sibling an empty param's slot stands for is present."""
    if not param.hr_empty_slot_with:
        return False
    for q in entry.params:
        if param_key(q) != param.hr_empty_slot_with:
            continue
        base = step if not q.parent_element else _descend_path(step, q.parent_element, entry.element_aliases)
        return base is not None and _aliased_child(base, q.xml_element, entry.element_aliases) is not None
    return False


if __name__ == "__main__":
    import os
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    catalog = os.path.join(repo_root, "agent", "catalogs", "step-catalog-en.json")
    rep = load_report(catalog)
    print(f"entries          : {rep.entries}")
    print(f"params in JSON   : {rep.params_in_json}")
    print(f"params loaded    : {rep.params_loaded}")
    print(f"dropped          : {rep.dropped}")
    print(f"unknown-typed    : {len(rep.unknown_typed)}")
    for step, el, ty in rep.unknown_typed:
        print(f"  ! {step} / {el} : {ty!r}")
    ok = rep.dropped == 0 and not rep.unknown_typed
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
