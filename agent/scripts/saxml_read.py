#!/usr/bin/env python3
"""saxml_read.py — read a FileMaker "Save as XML" (SaXML) <Step> into the shared
per-param ``values[]`` token array the catalog emit engine consumes (P6.4).

This is the SaXML *front-end* for the catalog-driven converters: SaXML's nested
``<ParameterValues><Parameter type="X">`` structure is decoded, per catalog param, into
the same token form ``catalog_emit.match_param_values`` produces, so the proven emit
grammar (``catalog_emit.convert_step_with_catalog``) serves both HR→XML and SaXML→XML.

    SaXML <Step>  ──[read_saxml_step]──▶  (disabled, values[], resolver)
                                              │
                                    [convert_step_with_catalog]  →  fmxmlsnippet <Step>

Unlike the fmxmlsnippet↔HR directions, the SaXML direction has **no reference
converter** (the reference covers only fmxmlsnippet↔HR), so this reader is verified by
coverage + regression against the prior converter + live round-trips, not byte-identity
against a reference. The structural decoding of SaXML mirrors the OSS Rust exploder
(``external_tools/fm-xml-export-exploder``), the closest SaXML-side reference.

Object references (field/script/layout/table) carry real IDs in SaXML; the reader seeds
a resolver from them so those IDs are **preserved** on emit (no regression vs the prior
converter), while the emit grammar stays a faithful, id-agnostic port.

Stdlib only; no venv. Control-flow steps are handled by the converter's hand-coders (the
sanctioned exception), not here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from calc_literal import hr_sep, quote_calc_literal
from catalog_grammar import (
    CatalogEntry,
    StepParam,
    is_governing_discriminator,
    param_key,
)

# Composite facets whose SaXML shape needs a dedicated decoder (filled in incrementally;
# a param of one of these types with no decoder yet is reported, never silently wrong).
_COMPOSITE_TYPES = frozenset(
    {"attrGroup", "bitmaskGroup", "findRequests", "repeatGroup", "fieldList", "parametersList"}
)


class UnsupportedSaXML(Exception):
    """Raised when a step's SaXML shape isn't decodable yet — fail loud (constraint #3)."""


# ---------------------------------------------------------------------------
# Seeded resolver — carries SaXML's own object IDs through emit (id preservation)
# ---------------------------------------------------------------------------
class SeededResolver:
    """An emit resolver seeded from a SaXML step's own field/script/layout/table refs.

    Keys fields by ``Table::Field`` (and bare field name as a fallback), scripts and
    layouts by name. Unknown names fall back to id 0 with the name passed through,
    matching the empty-context behaviour of the reference fixtures.
    """

    def __init__(self) -> None:
        self._fields: dict[str, tuple[str, int, str]] = {}
        self._scripts: dict[str, int] = {}
        self._layouts: dict[str, int] = {}

    def add_field(self, table: str, fid: int, fname: str) -> None:
        ref = fname if not table else table + "::" + fname
        self._fields.setdefault(ref, (table, fid, fname))
        self._fields.setdefault(fname, (table, fid, fname))

    def add_script(self, name: str, sid: int) -> None:
        self._scripts.setdefault(name, sid)

    def add_layout(self, name: str, lid: int) -> None:
        self._layouts.setdefault(name, lid)

    def resolve_field(self, table_field: str) -> tuple[str, int, str]:
        hit = self._fields.get(table_field)
        if hit is not None:
            return hit
        sep = table_field.find("::")
        if sep < 0:
            return "", 0, table_field
        return table_field[:sep].strip(), 0, table_field[sep + 2 :].strip()

    def resolve_script(self, name: str) -> tuple[int, str]:
        return self._scripts.get(name, 0), name

    def resolve_layout(self, name: str) -> tuple[int, str]:
        return self._layouts.get(name, 0), name


def _seed_resolver(step_el: ET.Element) -> SeededResolver:
    """Collect every field/script/layout reference in the step into a resolver."""
    r = SeededResolver()
    for fr in step_el.iter("FieldReference"):
        tor = fr.find("TableOccurrenceReference")
        table = tor.get("name", "") if tor is not None else ""
        try:
            fid = int(fr.get("id", "0"))
        except ValueError:
            fid = 0
        r.add_field(table, fid, fr.get("name", ""))
    for sr in step_el.iter("ScriptReference"):
        try:
            sid = int(sr.get("id", "0"))
        except ValueError:
            sid = 0
        r.add_script(sr.get("name", ""), sid)
    for lr in step_el.iter("LayoutReference"):
        try:
            lid = int(lr.get("id", "0"))
        except ValueError:
            lid = 0
        r.add_layout(lr.get("name", ""), lid)
    return r


# ---------------------------------------------------------------------------
# Calc extraction — the nested SaXML calculation shape
# ---------------------------------------------------------------------------
# Subtrees a value calc never lives under: an object reference carries its OWN
# ``<repetition><Calculation>`` (the repetition index), which is NOT the param's
# value calc — descending into these is the deep-search greediness bug.
_CALC_SKIP = frozenset(
    {"FieldReference", "Variable", "Target", "repetition", "TableOccurrenceReference",
     "ScriptReference", "LayoutReference", "DataSourceReference"}
)

# The object-reference half of ``_CALC_SKIP`` — a subtree whose calcs belong to the
# reference itself, never to a param. ``<repetition>`` is absent because it is BOTH:
# a field reference's repetition is not a param value, while ``Go to Object``'s is.
# The addresses below separate the two; ``_find_value_calc``'s positional callers
# cannot, so they keep the blunter ``_CALC_SKIP``.
_CALC_REF_SKIP = frozenset(_CALC_SKIP - {"repetition"})


def _find_value_calc(el: ET.Element, depth: int = 0) -> ET.Element | None:
    """First ``<Calculation>`` reachable from ``el`` through value-wrapper elements
    (``<URL>``, ``<value>``, ``<Options>``, …) WITHOUT crossing an object-reference
    or ``<repetition>`` subtree. Returns the outer ``<Calculation>`` node or None."""
    for ch in el:
        if ch.tag == "Calculation":
            return ch
        if ch.tag in _CALC_SKIP:
            continue
        if depth < 4:
            r = _find_value_calc(ch, depth + 1)
            if r is not None:
                return r
    return None


def _calc_of(outer: ET.Element | None) -> str:
    """Text of a ``<Calculation>`` node (the nested ``<Calculation><Text>`` form)."""
    if outer is None:
        return ""
    inner = outer.find("Calculation")
    if inner is not None:
        t = inner.find("Text")
        if t is not None:
            return t.text or ""
    t = outer.find("Text")
    return (t.text or "") if t is not None else ""


def _calc_text(container: ET.Element | None) -> str:
    """Extract a param's value calc. The ``<Calculation>`` may sit under a semantic
    wrapper (``<URL>`` for Open URL, ``<value>``/``<Options>``, …), but NOT inside an
    object reference's own ``<repetition>``. Returns '' when absent."""
    if container is None:
        return ""
    return _calc_of(_find_value_calc(container))


# ---------------------------------------------------------------------------
# SaXML <Parameter> helpers
# ---------------------------------------------------------------------------
def _params(step_el: ET.Element) -> list[ET.Element]:
    return step_el.findall("ParameterValues/Parameter")


def _field_ref_token(fr: ET.Element) -> str:
    """A ``<FieldReference>`` → ``Table::Field`` (or bare field name)."""
    tor = fr.find("TableOccurrenceReference")
    table = tor.get("name", "") if tor is not None else ""
    name = fr.get("name", "")
    if not name:
        return ""
    return name if not table else table + "::" + name


def _repetition_suffix(ref_el: ET.Element, allow_calc: bool = True) -> str:
    """The ``[N]`` / ``[calc]`` HR subscript for a ``<FieldReference>`` / ``<Variable>``
    target.

    SaXML gives every field and variable reference a ``<repetition>`` calc; the
    fmxmlsnippet form carries a literal as ``repetition="N"``, only for N > 1, so
    ``1`` (the default) yields no subscript. A computed repetition (``$i``) is
    fmxmlsnippet's ``repetition="0"`` plus a ``<Repetition><Calculation>`` sibling,
    which the emit's field and fieldOrVariable params write from ``[calc]``. A
    Show Custom Dialog input (``allow_calc=False``) has no calculated form --
    FileMaker's input-target dialog takes digits only -- so one is refused there
    rather than dropped.
    """
    rep = ref_el.find("repetition")
    if rep is None:
        return ""
    text = _calc_of(rep.find("Calculation")).strip()
    if text in ("", "1"):
        return ""
    if text.isascii() and text.isdigit() and int(text) > 1:
        return "[" + str(int(text)) + "]"
    if allow_calc:
        return "[" + text + hr_sep(text, "]")
    raise UnsupportedSaXML(
        f"calculated repetition {text!r} on {ref_el.tag} "
        f"{ref_el.get('name') or ref_el.get('value') or ''!r} has no fmxmlsnippet form"
    )


def _target_token(ref_el: ET.Element, allow_calc: bool = True) -> str:
    """A field/variable TARGET reference -> its HR token with any subscript
    (``Table::Field[2]`` / ``$var[$i]``) — the form the emit's field and
    fieldOrVariable params parse back into ``repetition="N"`` or the calculated
    form. Field references elsewhere (sort / field lists) keep the bare
    ``_field_ref_token``."""
    if ref_el.tag == "Variable":
        tok = ref_el.get("value", "")
    else:
        tok = _field_ref_token(ref_el)
    return tok + _repetition_suffix(ref_el, allow_calc) if tok else ""


def _layout_token_from_lrc(lrc: ET.Element) -> str:
    """A ``<LayoutReferenceContainer value="N">`` → the emit's layout HR token.

    The ``value`` number is the destination discriminator (mirrors the exploder's
    layout_reference.rs): 5 = a named ``<LayoutReference>`` (SelectedLayout); 3 =
    LayoutNameByCalc; 4 = LayoutNumberByCalc; 1 = a ``<Label>`` (original/current
    layout). The emit's ``_resolve_layout_token`` reverses these."""
    lr = lrc.find("LayoutReference")
    if lr is not None:
        return '"' + lr.get("name", "") + '"'
    val = lrc.get("value", "")
    if val == "3":
        # Bare calculation, matching what FileMaker itself renders and what the
        # grammar now emits; the emit reads a bare calc back as LayoutNameByCalc.
        return _calc_text(lrc)
    if val == "4":
        # Keeps the keyword: FileMaker renders number-by-calculation identically
        # to name-by-calculation, so a bare calc could not round-trip as this one.
        return "by number: " + _calc_text(lrc)
    label = lrc.find("Label")
    if label is not None:
        return (label.text or "").strip()
    return _calc_text(lrc)


def _bool_on_off(sp: ET.Element) -> str | None:
    """A ``<Parameter><Boolean type=… value=…/>`` → 'On'/'Off' (None if absent)."""
    b = sp.find("Boolean")
    if b is None:
        return None
    return "On" if b.get("value", "False") == "True" else "Off"


def _bool_type(sp: ET.Element) -> str:
    b = sp.find("Boolean")
    return b.get("type", "") if b is not None else ""


# ---------------------------------------------------------------------------
# Where FileMaker puts a step's calculations
# ---------------------------------------------------------------------------
# A step's calcs cannot be read positionally. FileMaker does not export them in
# catalog order — ``Perform SQL Query by Natural Language`` exports Account Name,
# Model and Prompt in the reverse of the order the catalog declares them, so
# "consume the next Calculation-bearing <Parameter>" hands every calc to the wrong
# param. The result is well-formed and confident: a positional read cannot produce a
# malformed document, so nothing downstream can tell.
#
# So each calc param is addressed by NAME instead. An address is a path starting at
# the top-level ``<Parameter type="…">`` and reading downward, where a nested
# ``<Parameter type="X">`` contributes ``X`` and any other element contributes its
# tag. It matches as an ordered subsequence anchored at the top, so it only needs to
# be as long as it takes to separate two params that share a parent — ``Go to
# Object`` keeps its object name under ``Object/Name`` and its repetition under
# ``Object/repetition``, while a param with a parent to itself is just its type.
#
# Two catalog params MAY share one address (``Set Field By Name`` exports both of its
# calcs as ``<Parameter type="Calculation">``); they then claim that address's calcs
# in document order, which is catalog order for every case measured.
#
# The names are DATA, not a rule — 88 of the 114 below differ from the param's
# fmxmlsnippet element name, and no prefix rule relates them (``LLMInstruction`` →
# ``Instructions``, ``LLMSlidingWindowCount`` → ``SlidingWindowMessageCount``,
# ``Name`` → ``AccountName``). Every entry was measured, not inferred: each calc was
# pinned to a distinguishable literal in a script built for the purpose, exported
# from FileMaker Pro 26.0.1 on macOS, and read back to see which <Parameter> carried
# it. Extend it the same way — a guessed address is worse than no address, because it
# looks authoritative. A step absent here still reads positionally.
_SAXML_CALC_PARAMS: dict[str, dict[str, str]] = {
    "AVPlayer Play": {
        "Repetition":       "Source",
        "PlaybackPosition": "position",
        "StartOffset":      "Start",
        "EndOffset":        "End",
    },
    "AVPlayer Set Options": {
        "PlaybackPosition": "position",
        "StartOffset":      "Start",
        "EndOffset":        "End",
        "Volume":           "Volume",
    },
    "Add Account": {
        "AccountName": "Name",
        "Password":    "Password",
    },
    "Change Password": {
        "OldPassword": "Old",
        "NewPassword": "New",
    },
    "Configure Local Notification": {
        "Name":                    "Name",
        "Delay":                   "Delay",
        "Title":                   "Title",
        "Body":                    "Body",
        "Button1Label":            "Button1Label",
        "Button2Label":            "Button2Label",
        "Button3Label":            "Button3Label",
        "Button1ForceFgnd":        "Button1Foreground",
        "Button2ForceFgnd":        "Button2Foreground",
        "Button3ForceFgnd":        "Button3Foreground",
        "ShowWhenAppInForeground": "ShowInForeground",
    },
    "Configure NFC Reading": {
        "Timeout":      "Timeout",
        "ReadMultiple": "ReadMultiple",
        "JSONOutput":   "JSONOutput",
    },
    "Configure Persistent Data": {
        "InstanceId": "PersistentStore",
    },
    "Configure Prompt Template": {
        "TemplateName":          "TemplateName",
        "SQLPrompt":             "SQLPrompt",
        "NaturalLanguagePrompt": "NaturalLanguagePrompt",
    },
    "Configure RAG Account ": {
        "RAGAccountName": "RAGAccountName",
        "Endpoint":       "RAGEndpoint",
        "AccessAPIKey":   "RAGAPIKey",
    },
    "Configure Region Monitor Script": {
        "RangeName":     "Name",
        "ProximityUUID": "UUID",
        "MajorID":       "Major",
        "MinorID":       "Minor",
    },
    "Fine-Tune Model": {
        "AccountName":           "FineTuneAccountName",
        "FineTuneBaseModelName": "FineTuneBaseModel",
        "Parameters":            "FineTuneParameters",
    },
    "Generate Response from Model": {
        "AccountName":               "LLMAccountName",
        "Model":                     "LLMModel",
        "UserPrompt":                "LLMUserPrompt",
        "Instructions":              "LLMInstruction",
        "SlidingWindowMessageCount": "LLMSlidingWindow",
        "Temperature":               "LLMTemperature",
        "ToolDefinitions":           "LLMToolDefinitions",
        "Parameters":                "LLMParameters",
        "ObjectName":                "LLMWebScript/Name",
        "FunctionName":              "LLMWebScript/FunctionRef",
    },
    "Go to Object": {
        "ObjectName": "Object/Name",
        "Repetition": "Object/repetition",
    },
    "Insert Embedding": {
        "AccountName": "LLMEmbeddingAccountName",
        "Model":       "LLMEmbeddingModel",
        "InputText":   "LLMEmbeddingInputText",
    },
    "Insert Embedding in Found Set": {
        "AccountName": "LLMEmbeddingAccountName",
        "Model":       "LLMEmbeddingModel",
        "Parameters":  "LLMParameters",
    },
    "Insert Image Caption": {
        "AccountName": "LLMEmbeddingAccountName",
        "Model":       "LLMEmbeddingModel",
        "InputText":   "LLMEmbeddingInputText",
    },
    "Insert Image Captions in Found Set": {
        "AccountName": "LLMEmbeddingAccountName",
        "Model":       "LLMEmbeddingModel",
        "Parameters":  "LLMParameters",
    },
    "Insert from URL": {
        "Calculation": "URL",
        "CURLOptions": "Calculation",
    },
    "Perform Find by Natural Language": {
        "AccountName":   "LLMAccountName",
        "Model":         "LLMModel",
        "PromptMessage": "LLMMessage",
        "Parameters":    "LLMParameters",
    },
    "Perform Find/Replace": {
        "FindCalc":    "find",
        "ReplaceCalc": "replace",
    },
    "Perform RAG Action": {
        "RAGAccountName": "RAGAccountName",
        "SpaceID":        "RAGSpaceID",
        "InputText":      "RAGInputText",
        "PromptMessage":  "LLMMessage",
        "AIAccountName":  "RAGAIAccountName",
        "Model":          "RAGModel",
        "TemplateName":   "TemplateName",
        "Parameters":     "RAGPromptParameters",
    },
    "Perform SQL Query by Natural Language": {
        "AccountName":   "LLMAccountName",
        "Model":         "LLMModel",
        "PromptMessage": "LLMMessage",
        "OptionsName":   "LLMOptionsName",
        "TemplateName":  "LLMPromptTemplateName",
        "Parameters":    "LLMParameters",
    },
    "Perform Script on Server": {
        "Calculated":  "List",
        "Calculation": "Parameter",
    },
    "Perform Semantic Find": {
        "Count":     "Count",
        "Threshold": "Threshold",
    },
    "Re-Login": {
        "AccountName": "Name",
        "Password":    "Password",
    },
    "Read from Data File": {
        "Calculation": "id",
        "Count":       "size",
    },
    "Refresh Object": {
        "ObjectName": "Object/Name",
        "Repetition": "Object/repetition",
    },
    "Reset Account Password": {
        "AccountName": "Name",
        "Password":    "Password",
    },
    "Revert Transaction": {
        "Condition":    "Condition",
        "ErrorCode":    "ErrorCode",
        "ErrorMessage": "ErrorMessage",
    },
    "Save Records as JSONL": {
        "SystemPrompt":    "SaveAsJSONLSystemPromptField",
        "UserPrompt":      "SaveAsJSONLUserPromptField",
        "AssistantPrompt": "SaveAsJSONLAssistantPromptField",
    },
    "Set Data File Position": {
        "Calculation": "id",
        "position":    "position",
    },
    "Set Field By Name": {
        "TargetName": "Calculation",
        "Result":     "Calculation",
    },
    "Set Selection": {
        "StartPosition": "Select/Start",
        "EndPosition":   "Select/End",
    },
    "Set Web Viewer": {
        "ObjectName": "Calculation",
        "URL":        "action",
    },
    "Set Window Title": {
        "Name":    "WindowReference/WindowReference/Select",
        "NewName": "WindowReference/WindowReference/Rename",
    },
}


# ---------------------------------------------------------------------------
# Where FileMaker puts a step's enums
# ---------------------------------------------------------------------------
# The same defect the calc addresses above exist to fix, in the same place: FileMaker
# does not export a step's ``<List>`` params in catalog order either, so "take the next
# unconsumed <Parameter> holding a <List>" hands each enum to the wrong param. It is
# quieter than the calc case because most steps have one enum and cannot be reordered,
# and louder when it does bite: an enum can GOVERN, so a swapped discriminator leaves
# the emitter unable to recognise the branch and it drops every param that branch would
# have revealed, calculations included.
#
# ``Perform RAG Action`` exports ``RAGDataSource`` ahead of ``RAGAction``, so the two
# swap and six correctly-placed calcs are dropped. ``AVPlayer Set Options`` shows the
# quiet form: FileMaker omits an unset ``Presentation``, and the ``Zoom`` that follows
# slides into its slot exactly as an absent optional calc shifts every later one.
#
# The addresses use the same grammar as ``_SAXML_CALC_PARAMS`` and are read the same way
# (``_addressed_lists``). Every entry below was derived from FileMaker's own SaXML by
# VALUE IDENTITY — a ``<List name>`` was attributed to a param only when that label is
# one the param declares (``enumValues`` or an ``hrEnumValues`` label) and no sibling
# enum declares it. The one step where the labels collide, ``Perform SQL Query by
# Natural Language`` (both its selection params render "From list"), was separated by
# the value only one of them accepts: ``LLMOptionsSelection`` carries "By JSON data",
# which is ``OptionsSelectionType``'s alone, leaving ``LLMTablesSelection`` for
# ``TablesSelectionType``. ``Perform Semantic Find``'s three are additionally an exact
# name match against the catalog's own ``xmlElement``.
#
# As with the calcs, a step present here does not fall back to position: a param with no
# address reads as empty. Two params are deliberately absent from their step's map —
# ``AVPlayer Play``'s ``Source`` and ``Perform SQL Query by Natural Language``'s
# ``UniversalPathList`` — because FileMaker exports no ``<List>`` for either in any
# sample seen, and reading empty is the honest answer. Both were previously claiming the
# NEXT param's list.
_SAXML_ENUM_PARAMS: dict[str, dict[str, str]] = {
    "AVPlayer Play": {
        "Presentation": "Presentation",
    },
    "AVPlayer Set Options": {
        "Presentation": "Presentation",
        "Zoom":         "Zoom",
        "Sequence":     "Sequence",
    },
    "Configure Prompt Template": {
        "ModelProvider": "ModelProvider",
        "RequestType":   "TemplateType",
    },
    "Configure Regression Model": {
        "LLMTrainAction": "LLMTrainActions",
        "LLMAlgorithm":   "LLMTrainAlgorithm",
    },
    "Perform RAG Action": {
        "RAGSpaceAction": "RAGAction",
        "DataSource":     "RAGDataSource",
    },
    "Perform Semantic Find": {
        "Query":     "Query",
        "Condition": "Condition",
        "Records":   "Records",
    },
    "Perform SQL Query by Natural Language": {
        "Action":               "LLMAction",
        "OptionsSelectionType": "LLMOptionsSelection",
        "TablesSelectionType":  "LLMTablesSelection",
    },
}


# ---------------------------------------------------------------------------
# What FileMaker's SaXML enum labels mean
# ---------------------------------------------------------------------------
# A SaXML ``<List name="…">`` carries the label FileMaker's script editor DISPLAYS, not
# the value it writes into fmxmlsnippet. Where the two differ and nothing in the catalog
# reverses the label, the label reaches the emitted attribute unchanged and FileMaker
# will not accept it back: ``<WindowState value="Resize to Fit"/>`` for a step FileMaker
# writes as ``ResizeToFit``, ``<With value="Replace with calculation: "/>`` for
# ``Calculation``.
#
# There is NO rule relating the two. De-spacing explains "Resize to Fit" → ResizeToFit
# and breaks on "Cascade Window" → Cascade; a prefix explains "Replace" →
# FindMatchingReplace and breaks on everything else; "On" → Show and "OpenAI" → ChatGPT
# share nothing with either. Like the calc addresses, this is DATA.
#
# Every entry is sourced from FileMaker's own fmxmlsnippet corpus in
# ``agent/snippet_examples/steps/`` — either a comment that states the mapping outright
# ("MonitorType value: iBeacon | GeoLocation (HR: Geofence) | Clear"; "ShowHide value:
# Show [On] | Hide [Off]"; "LLMType value: ChatGPT (OpenAI) | …") or a legal-value list
# in which the unchanged members anchor the changed one by position and wording
# ("ResizeToFit" is the only member of Adjust Window's list that is not already the
# label). Do not add an entry any other way: a guessed mapping looks authoritative and
# is worse than leaving the label to pass through.
#
# A label with no entry falls through to the catalog's ``hrEnumValues`` reversed, then to
# itself. Most enums need nothing here — "Home", "Toggle", "Fit" and the rest are their
# own values, and ``Set Zoom Level``/``Save a Copy as``/``Undo/Redo`` and friends already
# carry a full ``hrEnumValues`` that reverses cleanly.
#
# Some KEYS are the label FileMaker's corpus documents rather than one seen in a SaXML
# sample — ``Configure AI Account``'s "Custom" and ``Configure Machine Learning Model``'s
# "Unload" are both quoted from a corpus comment, but only "OpenAI" and "uninstall" have
# actually been observed in an export. That is deliberate: the MAPPING is documented
# either way, and a key that never arrives costs nothing, while a missing one would let a
# label reach the output. It is not licence to invent a key whose mapping is unattested.
# Seven steps' entries were removed once the catalog's own ``hrEnumValues`` was corrected
# to hold FileMaker's values and labels (Adjust Window, Arrange All Windows, AVPlayer Set
# Options, Configure AI Account, Configure Region Monitor Script, Enable Touch Keyboard,
# Find Matching Records): the catalog now reverses each of those labels to the same value
# this map did, so the entries were duplicated data and a second place to drift. What
# stays is what the catalog CANNOT reverse — a SaXML label that differs from the HR one
# ("By Calculation…" carries a trailing ellipsis the Script Workspace does not show;
# "uninstall" arrives lowercased) or a param the catalog models differently.
_SAXML_ENUM_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "Configure Machine Learning Model": {
        "ConfigureCoreML": {"uninstall": "Uninstall", "Unload": "Uninstall"},
    },
    "Go to Portal Row": {
        "RowPageLocation": {"By Calculation…": "ByCalculation"},
    },
    "Go to Record/Request/Page": {
        "RowPageLocation": {"By Calculation…": "ByCalculation"},
    },
    "Perform Semantic Find": {
        # "Query type (HR: Query by): 1 → Natural language | 2 → Vector data | 3 →
        # Image", and FileMaker's natural-language mode is the one carrying a <Text>
        # calc — which is what its SaXML list is named, at value="1".
        "Query": {"Text": "1"},
    },
    "Replace Field Contents": {
        "With": {
            "Current contents":              "CurrentContents",
            "Replace with serial numbers: ": "SerialNumbers",
            "Replace with calculation: ":    "Calculation",
        },
    },
    "Set Web Viewer": {
        "Action": {
            "Go to URL...": "GoToURL",
            "Reset":        "Reset",
            "Reload":       "Reload",
            "Go Forward":   "GoForward",
            "Go Back":      "GoBack",
        },
    },
}


# ---------------------------------------------------------------------------
# Flags FileMaker exports as a labeled <Boolean>
# ---------------------------------------------------------------------------
# A flag-style enum whose SaXML is neither a ``<List>`` nor a parameter of its own,
# but a ``<Boolean type="…">`` FileMaker writes for it, possibly nested inside
# another param's value. Its value True is FileMaker drawing the flag on the step's
# line. Measured, never inferred: the boolean's label and polarity come from an
# export of the step in every state.
#
# ``Replace Field Contents``' ``Skip auto-enter options`` (the catalog's
# ``SerialNumbers#PerformAutoEnter`` projection) sits inside the replace ``<List>``
# and is True exactly when the step stores ``<Restore state="True"/>`` and
# ``PerformAutoEnter="False"`` (FileMaker Pro 26.0.3, every With mode; Restore alone
# is bit 0x02000000 of ``<Options>``, and Restore True with PerformAutoEnter True
# exports False, the same line as Restore False).
_SAXML_FLAG_BOOLEANS: dict[str, dict[str, str]] = {
    "Replace Field Contents": {
        "SerialNumbers#PerformAutoEnter": "Skip auto-enter options",
    },
}


def _take_labeled_boolean(
    sparams: list[ET.Element], consumed: list[bool], claimed: set[int], label: str
) -> ET.Element | None:
    """The ``<Boolean type="label">``, claimed once: a top-level param's own boolean
    first (that param is consumed), then one nested inside another param's value,
    never through an object reference."""
    for i, sp in enumerate(sparams):
        if consumed[i]:
            continue
        b = sp.find("Boolean")
        if b is not None and b.get("type") == label and id(b) not in claimed:
            consumed[i] = True
            claimed.add(id(b))
            return b

    def walk(el: ET.Element) -> ET.Element | None:
        for ch in el:
            if ch.tag in _CALC_REF_SKIP:
                continue
            if ch.tag == "Boolean" and ch.get("type") == label and id(ch) not in claimed:
                return ch
            found = walk(ch)
            if found is not None:
                return found
        return None

    for sp in sparams:
        b = walk(sp)
        if b is not None:
            claimed.add(id(b))
            return b
    return None


def _saxml_enum_value(entry: CatalogEntry, param: StepParam, label: str) -> str:
    """A SaXML ``<List name>`` → the XML value FileMaker writes for it.

    Measured mapping first, then the catalog's own ``hrEnumValues`` reversed, then the
    label unchanged. The passthrough is deliberate: an unmeasured label is not
    necessarily wrong (most enums label themselves with their value), and refusing the
    step instead would cost every other param on it to fix nothing.
    """
    measured = _SAXML_ENUM_LABELS.get(entry.name, {}).get(param_key(param))
    if measured and label in measured:
        return measured[label]
    for value, mapped in param.hr_enum_values.items():
        if mapped == label:
            return value
    return label


def _saxml_seg(el: ET.Element) -> str:
    """One address segment: a ``<Parameter type="X">`` reads as X, anything else as its tag."""
    return (el.get("type") or "") if el.tag == "Parameter" else el.tag


def _addressed_nodes(
    sparams: list[ET.Element], address: str, tag: str
) -> list[tuple[int, ET.Element]]:
    """Every ``<tag>`` the address reaches, as ``(top-level param index, node)``.

    Document order, which is how two params sharing one address are told apart. The walk
    stops at the first ``tag`` on a branch, so an address never reaches through one node
    of the kind it is looking for into another nested inside it — ``Replace Field
    Contents`` nests an entry-option ``<List>`` inside the ``<List>`` that carries its
    branch, and only the outer one is the param's value.
    """
    want = address.split("/")
    out: list[tuple[int, ET.Element]] = []

    def walk(el: ET.Element, matched: int, i: int) -> None:
        if el.tag == tag:
            if matched == len(want):
                out.append((i, el))
            return
        for ch in el:
            seg = _saxml_seg(ch)
            nxt = matched + 1 if matched < len(want) and seg == want[matched] else matched
            walk(ch, nxt, i)

    for i, sp in enumerate(sparams):
        if _saxml_seg(sp) != want[0]:
            continue
        walk(sp, 1, i)
    return out


def _addressed_calcs(sparams: list[ET.Element], address: str) -> list[tuple[int, ET.Element]]:
    """Every ``<Calculation>`` the address reaches — see ``_addressed_nodes``."""
    return _addressed_nodes(sparams, address, "Calculation")


def _addressed_lists(sparams: list[ET.Element], address: str) -> list[tuple[int, ET.Element]]:
    """Every ``<List>`` the address reaches — see ``_addressed_nodes``."""
    return _addressed_nodes(sparams, address, "List")


def _value_calcs(el: ET.Element, depth: int = 0) -> list[ET.Element]:
    """Every value ``<Calculation>`` under ``el``, not crossing an object reference.

    ``_find_value_calc``'s plural form. ``<repetition>`` is NOT skipped here: it holds a
    real param value on the object steps (``Go to Object``'s Repetition), and the
    addresses tell those apart from a field reference's own repetition, which the
    object-reference skip below still excludes.
    """
    out: list[ET.Element] = []
    for ch in el:
        if ch.tag == "Calculation":
            out.append(ch)
        elif ch.tag in _CALC_REF_SKIP:
            continue
        elif depth < 4:
            out.extend(_value_calcs(ch, depth + 1))
    return out


# ---------------------------------------------------------------------------
# Per-param value extraction (simple param types)
# ---------------------------------------------------------------------------
def _extract_simple(
    entry: CatalogEntry, param: StepParam, sparams: list[ET.Element], consumed: list[bool],
    claimed: set[int] | None = None
) -> str:
    """Extract one catalog param's values[] token from the SaXML params.

    Matches by type-correspondence, consuming SaXML params as it goes. Returns '' when
    the param has no source (the emitter then applies its default). Raises
    ``UnsupportedSaXML`` for a composite facet with no decoder yet.
    """
    ptype = param.type
    if claimed is None:
        claimed = set()

    if ptype in _COMPOSITE_TYPES:
        raise UnsupportedSaXML(f"{entry.name}: no decoder for {ptype} param {param.xml_element!r}")

    if ptype in ("calculation", "calc", "namedCalc"):
        address = _SAXML_CALC_PARAMS.get(entry.name, {}).get(param_key(param))
        if address is not None:
            # Addressed by name: take the first calc at this address nothing has
            # claimed. Nothing there means FileMaker did not export the param — the
            # emitter applies its default — NOT that the next calc along belongs here.
            for i, node in _addressed_calcs(sparams, address):
                if id(node) in claimed:
                    continue
                claimed.add(id(node))
                if all(id(c) in claimed for c in _value_calcs(sparams[i])):
                    consumed[i] = True
                return _calc_of(node)
            return ""

        if entry.name in _SAXML_CALC_PARAMS:
            # The step's calc layout was measured, and this param was not in it —
            # FileMaker did not export it in any mode the capture reached. Falling back
            # to position here would let it claim a calc that belongs to an addressed
            # param, which is the failure the addresses exist to prevent. Anything
            # FileMaker does export and no address claims is refused below instead.
            return ""

        # No address measured for this step: consume the next Calculation-bearing SaXML
        # param (Calculation / Title / Message / URL / a named calc param), searching
        # through any wrapper. Correct only while FileMaker's export order matches the
        # catalog's declaration order, which is why the table above exists.
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            if _find_value_calc(sp) is not None:
                consumed[i] = True
                return _calc_text(sp)
        return ""

    if ptype in ("text", "name"):
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            t = sp.find("Text")
            if t is not None:
                consumed[i] = True
                raw = t.get("value", "") or (t.text or "")
                # An hrQuoted param's token is one escaped text literal (the form
                # the HR renderer writes and the emitter decodes), so a raw value
                # holding quotes is not unwrapped on the way through.
                return _quoted_text_token(param, raw) if param.hr_quoted else raw
            # File-path steps carry the path as <UniversalPathList><ObjectList>
            # <Location>… rather than a <Text> node.
            loc = sp.find(".//Location")
            if loc is not None:
                consumed[i] = True
                return loc.text or ""
        return ""

    if ptype == "field":
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            fr = sp.find("FieldReference")
            if fr is not None:
                consumed[i] = True
                return _target_token(fr)
            if sp.get("type") == "Target":
                v = sp.find("Variable")
                if v is not None:
                    consumed[i] = True
                    return _target_token(v)
                fr = sp.find("FieldReference")
                if fr is not None:
                    consumed[i] = True
                    return _target_token(fr)
        return ""

    if ptype == "fieldOrVariable":
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            if sp.get("type") == "Target":
                v = sp.find("Variable")
                if v is not None:
                    consumed[i] = True
                    return _target_token(v)
                fr = sp.find("FieldReference")
                if fr is not None:
                    consumed[i] = True
                    return _target_token(fr)
            fr = sp.find("FieldReference")
            if fr is not None:
                consumed[i] = True
                return _target_token(fr)
        return ""

    if ptype == "script":
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            sr = sp.find(".//ScriptReference")
            if sr is not None:
                consumed[i] = True
                return '"' + sr.get("name", "") + '"'
        return ""

    if ptype == "layout":
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            lrc = sp.find("LayoutReferenceContainer")
            if lrc is not None:
                consumed[i] = True
                return _layout_token_from_lrc(lrc)
            lay = sp.find("Layout")
            if lay is not None:
                consumed[i] = True
                return '"' + lay.get("name", "") + '"'
        return ""

    if ptype == "boolean":
        # Match by the SaXML <Boolean type="…"> attribute against the catalog hrLabel.
        if param.hr_label:
            for i, sp in enumerate(sparams):
                if consumed[i]:
                    continue
                if _bool_type(sp) == param.hr_label:
                    consumed[i] = True
                    return _bool_on_off(sp) or ""
        # Fallback: the one unconsumed Boolean left, positionally — and ONLY when
        # exactly one is left.
        #
        # FileMaker's SaXML carries booleans this reader must not claim: editor
        # state that is no step param at all (``<Boolean type="Collapsed">`` on
        # block steps), presence flags for optional companions the catalog models
        # as calcs (``Revert Transaction``'s Condition / Error Code), and the
        # boolean of a param this reader skipped because it is ``hrHidden``.
        # Taking "the next one" when several are unclaimed is a coin flip, and a
        # wrong pick is silent — it emits a plausible ``<Elem state="…"/>`` that
        # simply means something else.
        #
        # With exactly one candidate there is no choice to get wrong, so the
        # positional read stands (FileMaker omits the ``type`` entirely on the
        # single-boolean steps, and names it differently from the catalog's
        # hrLabel on others — ``Set Error Logging`` is ``enabled`` against a
        # ``Logging`` label). With more than one, fail loud, exactly as an
        # undecodable composite facet does: the caller counts it rather than
        # emitting XML that is confidently wrong.
        cands = [
            i for i, sp in enumerate(sparams)
            if not consumed[i] and sp.find("Boolean") is not None
        ]
        if not cands:
            return ""
        if len(cands) > 1:
            offered = ", ".join(repr(_bool_type(sparams[i])) for i in cands)
            raise UnsupportedSaXML(
                f"{entry.name}: cannot place boolean param {param.xml_element!r} "
                f"(hrLabel {param.hr_label!r}) — {len(cands)} unclaimed SaXML "
                f"booleans to choose from ({offered})")
        consumed[cands[0]] = True
        return _bool_on_off(sparams[cands[0]]) or ""

    if ptype == "enum":
        # A flag FileMaker exports as a labeled <Boolean> (see _SAXML_FLAG_BOOLEANS).
        # An export without the measured boolean is a changed shape: refuse the step
        # rather than read the flag as off.
        flag_label = _SAXML_FLAG_BOOLEANS.get(entry.name, {}).get(param_key(param))
        if flag_label is not None and param.flag_style:
            b = _take_labeled_boolean(sparams, consumed, claimed, flag_label)
            if b is None:
                raise UnsupportedSaXML(
                    f"{entry.name}: no SaXML <Boolean type={flag_label!r}> for flag "
                    f"param {param_key(param)!r}")
            # Drawn = the HR token present; not drawn = absent (empty), which is how
            # the emitter derives a gating sibling's state (Restore here).
            return "True" if b.get("value") == "True" else ""

        # <Animation> is its own SaXML element, not a <List>, and only ever belongs to
        # the param named for it — so it is matched by name and never competes for a
        # list with the layout-destination enum it sits beside.
        if param.xml_element == "Animation":
            for i, sp in enumerate(sparams):
                if consumed[i]:
                    continue
                anim = sp.find("Animation")
                if anim is not None:
                    consumed[i] = True
                    raw = anim.get("name", "")
                    # FM stores <Animation name="None"> in SaXML but omits the value in
                    # its fmxmlsnippet (empty <Animation value=""/>); mirror that.
                    if raw == "None":
                        return ""
                    return param.hr_enum_values.get(raw, raw)
            return ""

        def token(lst: ET.Element) -> str:
            # The SaXML label is the script editor's, and the value FileMaker writes is
            # frequently a different string; translate before anything else looks at it.
            value = _saxml_enum_value(entry, param, lst.get("name", "") or lst.get("value", ""))
            # A governing discriminator is matched against the catalog's own value, so
            # hand that over as-is — a branch value the emitter does not recognise
            # silently hides every param the branch reveals, calculations included.
            # A simple enum forward-maps to its HR label, which the emitter reverses.
            if is_governing_discriminator(param):
                return value
            return param.hr_enum_values.get(value, value)

        def take(i: int, node: ET.Element) -> str:
            # An enum claims the LIST, not the whole <Parameter> that holds it — a
            # branch's list carries the branch's calculation inside it (Replace Field
            # Contents keeps its replacement calc under <List name="Replace with
            # calculation: ">), and marking the param consumed hides that calc from
            # every later param. Retire the param only once nothing is left in it, the
            # same test the addressed calcs use.
            claimed.add(id(node))
            if all(id(c) in claimed for c in _value_calcs(sparams[i])):
                consumed[i] = True
            return token(node)

        address = _SAXML_ENUM_PARAMS.get(entry.name, {}).get(param_key(param))
        if address is not None:
            for i, node in _addressed_lists(sparams, address):
                if id(node) in claimed:
                    continue
                return take(i, node)
            return ""

        if entry.name in _SAXML_ENUM_PARAMS:
            # The step's enum layout was measured whole and this param was not in it, so
            # FileMaker exports no list for it. Falling back to position here would let
            # it claim a list belonging to an addressed param — the failure the
            # addresses exist to prevent.
            return ""

        # No address measured for this step: consume the next list-bearing SaXML param.
        # Correct only while FileMaker's export order matches the catalog's declaration
        # order, which is why the table above exists — but a step with a single enum
        # has no order to get wrong, and that is most of the catalog.
        for i, sp in enumerate(sparams):
            if consumed[i]:
                continue
            lst = sp.find("List")
            if lst is not None and id(lst) not in claimed:
                return take(i, lst)
        return ""

    # Unrecognized-but-simple type: no token (emitter applies default).
    return ""


# ===========================================================================
# Dedicated decoders — composite/semi-composite SaXML shapes the generic
# per-param extractor can't map (WindowReference, bitmask WindowReference,
# FindRequestSet, dialog buttons, sort specs, attrGroup bundles, …). Each
# decoder reads the step's SaXML into the per-catalog-param ``values[]`` token
# array the emit engine consumes — so the OUTPUT stays catalog-canonical (the
# emit grammar, verified 203/203) while only the READING is step-specific. This
# is the plan's sanctioned exception for bundled steps (control-flow stays in
# the converter's hand-coders, not here).
# ===========================================================================
_DECODERS: dict[str, object] = {}


def _decoder(*names: str):
    def reg(fn):
        for n in names:
            _DECODERS[n] = fn
        return fn
    return reg


class _Vals:
    """A values[] builder that sets tokens by catalog-param identity."""

    def __init__(self, entry: CatalogEntry) -> None:
        self.entry = entry
        self.v = [""] * len(entry.params)

    def set(self, token: str, xml: str | None = None, wrap: str | None = None,
            hr: str | None = None, index: int | None = None) -> None:
        if index is not None:
            self.v[index] = token
            return
        for i, p in enumerate(self.entry.params):
            if wrap is not None and (p.wrapper_element or "") != wrap:
                continue
            if xml is not None and p.xml_element != xml:
                continue
            if hr is not None and p.hr_label != hr:
                continue
            self.v[i] = token
            return

    def param(self, xml: str | None = None, wrap: str | None = None,
              hr: str | None = None) -> StepParam | None:
        for p in self.entry.params:
            if wrap is not None and (p.wrapper_element or "") != wrap:
                continue
            if xml is not None and p.xml_element != xml:
                continue
            if hr is not None and p.hr_label != hr:
                continue
            return p
        return None

    def list(self) -> list[str]:
        return self.v


def _ptype(step_el: ET.Element, t: str) -> ET.Element | None:
    for sp in step_el.findall("ParameterValues/Parameter"):
        if sp.get("type") == t:
            return sp
    return None


def _ptypes(step_el: ET.Element, t: str) -> list[ET.Element]:
    return [sp for sp in step_el.findall("ParameterValues/Parameter") if sp.get("type") == t]


def _quoted_text_token(param: StepParam | None, raw: str) -> str:
    """An ``hrQuoted`` text param's HR token: one escaped text literal in the
    param's delimiter style (``hrQuoteStyle``), '' for an empty value."""
    if not raw:
        return ""
    if param is not None and param.hr_quote_style == "straight":
        return quote_calc_literal(raw, '"', '"')
    return quote_calc_literal(raw)


def _bool_tok(sp: ET.Element | None) -> str:
    """A ``<Parameter><Boolean value=…/>`` → 'On'/'Off' ('' when absent)."""
    if sp is None:
        return ""
    b = sp.find("Boolean")
    if b is None:
        return ""
    return "On" if b.get("value", "False") == "True" else "Off"


def _enum_tok(param: StepParam | None, raw_xml: str) -> str:
    """XML enum value → the emit token (raw for governing discriminators, else the
    forward-mapped HR label), mirroring the generic extractor."""
    if param is None or raw_xml == "":
        return raw_xml
    if is_governing_discriminator(param):
        return raw_xml
    return param.hr_enum_values.get(raw_xml, raw_xml)


# ── WindowReference family (Close / Select / Move-Resize Window) ──────────────
def _decode_window_reference(v: _Vals, wr: ET.Element | None) -> None:
    """Fill LimitToWindowsOfCurrentFile / Window / Name from a <WindowReference>."""
    if wr is None:
        return
    sel = wr.find("Select")
    if sel is None:
        return
    if (sel.get("type", "Calculated") or "").lower() == "current":
        v.set(_enum_tok(v.param(xml="Window"), "Current"), xml="Window")
        return
    v.set(_enum_tok(v.param(xml="Window"), "ByName"), xml="Window")
    name_el = sel.find("Name")
    if name_el is not None:
        limit = name_el.get("current", "True")
        v.set("On" if limit == "True" else "Off", xml="LimitToWindowsOfCurrentFile")
        v.set(_calc_text(name_el), wrap="Name")


@_decoder("Close Window", "Select Window")
def _dec_window(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    v.set("On", xml="LimitToWindowsOfCurrentFile")  # FM default when no <Name>
    p = _ptype(step_el, "WindowReference")
    _decode_window_reference(v, p.find("WindowReference") if p is not None else None)
    return v.list()


@_decoder("Move/Resize Window")
def _dec_move_resize(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    v.set("On", xml="LimitToWindowsOfCurrentFile")
    p = _ptype(step_el, "WindowReference")
    wr = p.find("WindowReference") if p is not None else None
    _decode_window_reference(v, wr)
    if wr is not None:
        bounds = wr.find("Bounds")
        if bounds is not None:
            for tag, wrap in (("height", "Height"), ("width", "Width"),
                              ("top", "DistanceFromTop"), ("left", "DistanceFromLeft")):
                el = bounds.find(tag)
                if el is not None:
                    v.set(_calc_text(el), wrap=wrap)
    return v.list()


# ── Perform AppleScript (Options-discriminated: Text literal vs Calculation) ──
@_decoder("Perform AppleScript")
def _dec_applescript(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    opt = _ptype(step_el, "Options")
    o = opt.find("Options") if opt is not None else None
    if o is not None:
        ctype = o.get("type", "")  # "Text" (literal) or "Calculation"
        v.set(_enum_tok(v.param(xml="ContentType"), ctype), xml="ContentType")
        calc = _calc_of(o.find("Calculation"))
        # Either mode's script may sit in <Calculation> or as the element's text
        # (Text mode = the literal). ContentType governs which companion emits, so
        # the script goes to the one the mode reveals: Text mode's literal is an
        # hrQuoted param, handed to the emitter in its quoted form.
        script = calc if calc else (o.text or "")
        if ctype == "Text":
            v.set(_quoted_text_token(v.param(xml="Text"), script), xml="Text")
        else:
            v.set(script, xml="Calculation")
    return v.list()


# ── Perform Script (script spec via <List>; parameter via <Parameter>) ────────
@_decoder("Perform Script")
def _dec_perform_script(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    lst = _ptype(step_el, "List")
    if lst is not None:
        inner = lst.find("List")
        sr = inner.find("ScriptReference") if inner is not None else None
        if sr is not None:
            v.set('"' + sr.get("name", "") + '"', xml="Script")
    par = _ptype(step_el, "Parameter")
    if par is not None:
        v.set(_calc_text(par), xml="Calculation")
    return v.list()


# ── Get Folder/File Path (result Variable + Repetition/Dialog/Location calcs) ──
@_decoder("Get Folder Path", "Get File Path")
def _dec_get_path(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    v.set(_bool_tok(_ptype(step_el, "Boolean")), xml="AllowFolderCreation")
    var = _ptype(step_el, "Variable")
    if var is not None:
        rep = var.find("repetition")
        if rep is not None:
            v.set(_calc_text(rep), wrap="Repetition")
        nm = var.find("Name")
        if nm is not None:
            v.set(nm.get("value", ""), xml="Name")
    title = _ptype(step_el, "Title")
    if title is not None:
        v.set(_calc_text(title), wrap="DialogTitle")
    loc = _ptype(step_el, "Location")
    if loc is not None:
        v.set(_calc_text(loc), wrap="DefaultLocation")
    return v.list()


# ── Perform JavaScript in Web Viewer (parametersList of argument calcs) ───────
@_decoder("Perform JavaScript in Web Viewer")
def _dec_perform_js(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    nm = _ptype(step_el, "Name")
    if nm is not None:
        v.set(_calc_text(nm), wrap="ObjectName")
    fn = _ptype(step_el, "FunctionRef")
    if fn is not None:
        v.set(_calc_text(fn), wrap="FunctionName")
    # Every <Parameter type="Parameter"> after the function is a JS argument calc;
    # the emit's parametersList splits the token on top-level commas back into <P>s.
    args = [_calc_text(p) for p in _ptypes(step_el, "Parameter")]
    v.set(", ".join(a for a in args if a), xml="Parameters")
    return v.list()


# ── Sort Records (fieldList of Table::Field=Direction entries) ────────────────
@_decoder("Sort Records")
def _dec_sort(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    b = _ptype(step_el, "Boolean")
    if b is not None:
        wd = b.find("Boolean")
        with_dialog = wd is not None and wd.get("value") == "True"
        # NoInteract has invertedHr; the token is the "With dialog" HR flag itself
        # and the emit inverts it to the NoInteract state.
        v.set("On" if with_dialog else "Off", xml="NoInteract")
    r = _ptype(step_el, "Restore")
    rr = r.find("Restore") if r is not None else None
    v.set("On" if (rr is not None and rr.get("value") == "True") else "Off", xml="Restore")
    ss = _ptype(step_el, "SortSpecification")
    spec = ss.find("SortSpecification") if ss is not None else None
    entries = []
    if spec is not None:
        for s in spec.findall("SortList/Sort"):
            fr = s.find(".//FieldReference")
            if fr is not None:
                entries.append(_field_ref_token(fr) + "=" + s.get("type", "Ascending"))
    v.set(", ".join(entries), xml="SortList")
    return v.list()


# ── New Window (bitmaskGroup style + chrome, Name/Layout/bounds calcs) ────────
# SaXML <Options> element tag → the bitmaskFlags hrLabel the emit packs.
_NW_OPT_HR = {
    "Close": "Close", "Minimize": "Minimize", "Maximize": "Maximize",
    "Resize": "Resize", "MenuBar": "Menu Bar", "Toolbar": "Toolbars",
    "DimParentWindow": "Dim parent window",
}


@_decoder("New Window")
def _dec_new_window(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    p = _ptype(step_el, "WindowReference")
    wr = p.find("WindowReference") if p is not None else None
    if wr is None:
        return v.list()
    style = wr.find("Style")
    style_tok = style.get("name", "") if style is not None else ""
    opts = wr.find("Options")
    if opts is not None:
        controls = [_NW_OPT_HR[ch.tag] for ch in opts
                    if ch.tag in _NW_OPT_HR and (ch.text or "").strip() == "True"]
        v.set(style_tok + "\x1e" + ", ".join(controls), xml="NewWndStyles")
    else:
        v.set(style_tok, xml="NewWndStyles")
    nm = wr.find("Name")
    if nm is not None and _find_value_calc(nm) is not None:
        v.set(_calc_text(nm), wrap="Name")
    lrc = wr.find("LayoutReferenceContainer")
    lr = lrc.find("LayoutReference") if lrc is not None else None
    if lr is not None:
        v.set('"' + lr.get("name", "") + '"', xml="Layout")
    # A by-calc New Window layout is dropped in FM's own fmxmlsnippet (the catalog's
    # non-discriminated Layout can't carry it) — leave empty to match.
    bounds = wr.find("Bounds")
    if bounds is not None:
        for tag, wrap in (("height", "Height"), ("width", "Width"),
                          ("top", "DistanceFromTop"), ("left", "DistanceFromLeft")):
            el = bounds.find(tag)
            if el is not None and _find_value_calc(el) is not None:
                v.set(_calc_text(el), wrap=wrap)
    return v.list()


# ── Show Custom Dialog (Title/Message namedCalcs + Buttons/InputFields repeat) ─
def _button_label_token(bp: ET.Element) -> str | None:
    """A ``<Parameter type="ButtonN" value="OK">`` → the repeatGroup ``label`` value.

    A literal caption lives in the ``value`` attribute (its calc form is a quoted
    string, e.g. ``"OK"``); a computed caption is a ``<Calculation>`` child (used
    raw). Absent → no ``label`` key (the button renders with no label token)."""
    calc = _calc_text(bp)
    if calc:
        return calc
    cap = bp.get("value")
    if cap is not None and cap != "":
        return '"' + cap.replace('"', '""') + '"'
    return None


def _input_field_token(fp: ET.Element) -> str:
    """A ``<Parameter type="FieldN">`` → the InputFields repeatGroup entry body
    ``password=…, field=…, label=…`` (only the present keys)."""
    parts = ["password=" + (_bool_type_value(fp, "Password") or "False")]
    tgt = None
    for sp in fp.findall("Parameter"):
        if sp.get("type") == "Target":
            tgt = sp
            break
    if tgt is not None:
        var = tgt.find("Variable")
        if var is not None:
            fld = _target_token(var, allow_calc=False)
        else:
            fr = tgt.find("FieldReference")
            fld = _target_token(fr, allow_calc=False) if fr is not None else ""
        if fld:
            parts.append("field=" + fld)
    lbl = None
    for sp in fp.findall("Parameter"):
        if sp.get("type") == "Label":
            lbl = _calc_text(sp)
            break
    if lbl:
        parts.append("label=" + lbl)
    return ", ".join(parts)


def _bool_type_value(parent: ET.Element, btype: str) -> str | None:
    """The ``value`` of a ``<Boolean type="btype">`` directly under ``parent``."""
    for b in parent.findall("Boolean"):
        if b.get("type") == btype:
            return b.get("value", "False")
    return None


@_decoder("Show Custom Dialog")
def _dec_show_dialog(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    # namedCalcs by SaXML <Parameter type=…> → catalog wrapperElement.
    has_named = False
    for stype, wrap in (("Title", "Title"), ("Message", "Message"),
                        ("height", "Height"), ("width", "Width"),
                        ("top", "DistanceFromTop"), ("left", "DistanceFromLeft")):
        p = _ptype(step_el, stype)
        if p is not None:
            v.set(_calc_text(p), wrap=wrap)
            has_named = True
    # Buttons: FM serializes Button1..Button3 whenever the dialog is configured, but
    # omits the whole <Buttons> block for an entirely blank dialog (no title/message/
    # inputs, all buttons bare) — that step serializes to nothing.
    buttons = []
    for bp in step_el.findall("ParameterValues/Parameter"):
        if not (bp.get("type") or "").startswith("Button"):
            continue
        parts = ["commit=" + (_bool_type_value(bp, "Commit") or "False")]
        lbl = _button_label_token(bp)
        if lbl is not None:
            parts.append("label=" + lbl)
        buttons.append(", ".join(parts))
    has_input = any((p.get("type") or "").startswith("Field")
                    for p in step_el.findall("ParameterValues/Parameter"))
    if buttons and (has_named or has_input):
        v.set(" | ".join(buttons), xml="Buttons")
    # Input fields: FM pads the fmxmlsnippet to 3 <InputField> whenever any exists.
    inputs = [_input_field_token(fp) for fp in step_el.findall("ParameterValues/Parameter")
              if (fp.get("type") or "").startswith("Field")]
    if inputs:
        while len(inputs) < 3:
            inputs.append("password=False")
        v.set(" | ".join(inputs), xml="InputFields")
    return v.list()


# ── Go to Related Record (attrGroup default-fill + TO/Layout refs) ────────────
@_decoder("Go to Related Record")
def _dec_gtrr(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    rel = _ptype(step_el, "Related")
    if rel is None:
        return v.list()
    tor = rel.find("TableOccurrenceReference")
    if tor is not None:
        v.set(tor.get("name", ""), xml="Table")
    lrc = rel.find("LayoutReferenceContainer")
    if lrc is not None:
        v.set(_layout_token_from_lrc(lrc), xml="Layout")  # LayoutDestination derived on emit
    # <WindowReference> present ⇒ result opens a new window.
    if rel.find("WindowReference") is not None:
        v.set("On", xml="ShowInNewWindow")
    opts = rel.find("Options")
    if opts is not None and opts.get("matchFoundSet") == "True":
        v.set("On", xml="MatchAllRecords")
    # NB: <Options ShowRelated> does NOT map to the catalog's Option ("Show only
    # related records") — confirmed False in every sample while ShowRelated was True.
    # Its true source is unresolved (no live sample with Option on); left at default.
    #
    # NewWndStyles is always serialized by FM with field defaults even without a new
    # window; synthesize a non-empty defaults token so the emitter fills them (an
    # empty attrGroup token would be omitted). New-window styles are unverified.
    nw = v.param(xml="NewWndStyles")
    if nw is not None:
        defaults = ", ".join(
            _raw_field(f, "xmlAttr") + "=" + _raw_field(f, "defaultValue")
            for f in (nw.raw.get("fields") or []) if _raw_field(f, "defaultValue")
        )
        v.set(defaults, xml="NewWndStyles")
    return v.list()


def _raw_field(f: object, k: str) -> str:
    return f.get(k, "") if isinstance(f, dict) and isinstance(f.get(k), str) else ""


# ===========================================================================
# Composite-param builders — for steps whose SIMPLE params extract generically
# but which carry ONE (or few) composite facet params (attrGroup / fieldList /
# findRequests) with a bespoke SaXML shape. Keyed by (step name, xmlElement); each
# returns the values[] token for that ONE param, reading the step directly. The
# generic extractor handles the rest of the step, so these stay small.
# ===========================================================================
_COMPOSITE_BUILDERS: dict[tuple[str, str], object] = {}


def _cbuild(step: str, *xml_elements: str):
    def reg(fn):
        for xe in xml_elements:
            _COMPOSITE_BUILDERS[(step, xe)] = fn
        return fn
    return reg


# ── group-token construction helpers (inverse of catalog_emit's group parse) ──
def _grp(*pairs: tuple[str, str | None]) -> str:
    """Join present ``key=value`` pairs with ', '. A None value drops the key."""
    return ", ".join(f"{k}={v}" for k, v in pairs if v is not None)


def _field_defaults_token(param: StepParam) -> dict[str, str]:
    """{fieldKey: defaultValue} for an attrGroup param's flat attr fields."""
    out: dict[str, str] = {}
    for f in param.raw.get("fields") or []:
        if isinstance(f, dict) and isinstance(f.get("xmlAttr"), str):
            out[f["xmlAttr"]] = f.get("defaultValue", "") or ""
    return out


# ── Print (PrintSettings) — <Print> attrs → the flat attr group ───────────────
_PRINT_TYPE = {
    "Records being browsed": "BrowsedRecords",
    "Current record": "CurrentRecord",
    "Blank record": "BlankRecord",
}


@_cbuild("Print", "PrintSettings")
def _cb_print_settings(entry: CatalogEntry, param: StepParam, step_el: ET.Element) -> str:
    p = _ptype(step_el, "Print")
    pr = p.find("Print") if p is not None else None
    if pr is None:
        return ""
    name = pr.get("name", "Records being browsed")
    pages = pr.find("Pages")
    copies = pr.find("Copies")
    return _grp(
        ("PrintType", _PRINT_TYPE.get(name, name.replace(" ", ""))),
        ("NumCopies", copies.get("value", "1") if copies is not None else None),
        ("AllPages", (pages.get("All") if pages is not None else None)),
        ("PrintToFile", pr.get("toFile")),
    )


# ── Print Setup (PageFormat) — an OS print-record blob; FM re-serializes the ──
# fmxmlsnippet PageFormat with printer defaults (the SaXML PageSetup uses a
# different measurement system and is not mechanically convertible). Emit the
# catalog defaults, overriding orientation from the SaXML when non-Portrait.
@_cbuild("Print Setup", "PageFormat")
def _cb_page_format(entry: CatalogEntry, param: StepParam, step_el: ET.Element) -> str:
    defaults = _field_defaults_token(param)
    p = _ptype(step_el, "PageSetup")
    ps = p.find("PageSetup") if p is not None else None
    if ps is not None:
        orient = ps.find("Orientation")
        if orient is not None and orient.get("name"):
            defaults["PageOrientation"] = orient.get("name", "Portrait")
    return _grp(*[(k, v) for k, v in defaults.items()])


# ── Insert from Device — device type + nested <Options type><Parameter> ───────
@_decoder("Insert from Device")
def _dec_insert_from_device(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    """The device type is the outer ``<List name>``; each device option is a nested
    ``<Options><Parameter type="X"><List name="Y"/>`` naming the catalog param whose
    xmlElement is X (Camera / Resolution / LightMode …), set to Y. An option no
    catalog param names is refused rather than dropped."""
    v = _Vals(entry)
    sparams = _params(step_el)
    consumed = [False] * len(sparams)
    claimed: set[int] = set()
    lst = _ptype(step_el, "List")
    inner = lst.find("List") if lst is not None else None
    if inner is not None:
        v.set(inner.get("name", ""), xml="InsertFrom")
    for i, p in enumerate(entry.params):
        if p.type == "fieldOrVariable" and not p.parent_element:
            v.set(_extract_simple(entry, p, sparams, consumed, claimed), index=i)
    opts = inner.find("Options") if inner is not None else None
    for sp in opts.findall("Parameter") if opts is not None else []:
        gname = sp.get("type", "")
        gl = sp.find("List")
        p = v.param(xml=gname)
        if p is None or p.type != "enum" or not p.parent_element or gl is None:
            raise UnsupportedSaXML(f"Insert from Device: no catalog option for {gname!r}")
        v.set(gl.get("name", ""), xml=gname)
    return v.list()


# ── Insert File (DialogOptions) — <Options type=…> siblings → the group ───────
_STORAGE_TYPE = {"Insert": "InsertOnly", "Reference": "Reference", "Automatic": "UserChoice"}
_COMPRESS_TYPE = {"Compress when possible": "WhenPossible", "Never compress": "Never",
                  "Automatic": "UserChoice"}


@_cbuild("Insert File", "DialogOptions")
def _cb_dialog_options(entry: CatalogEntry, param: StepParam, step_el: ET.Element) -> str:
    p = _ptype(step_el, "Options")
    if p is None:
        return _grp(("asFile", "True"), ("enable", "True"))
    title = ""
    storage = compress = None
    has_filters = False
    for o in p.findall("Options"):
        ot = o.get("type", "")
        if ot == "Title":
            title = _calc_text(o.find("Title")) or _calc_text(o)
        elif ot == "Storage":
            st = o.find("Storage")
            if st is not None:
                storage = _STORAGE_TYPE.get(st.get("name", ""), st.get("name", "").replace(" ", ""))
        elif ot == "Compress":
            cp = o.find("Compress")
            if cp is not None:
                compress = _COMPRESS_TYPE.get(cp.get("name", ""), cp.get("name", "").replace(" ", ""))
        elif ot == "Filters":
            has_filters = True
    parts = [("asFile", "True"), ("enable", "True")]
    if title:
        parts.append(("Title", title))
    if storage is not None:
        parts.append(("Storage", f"(type={storage})"))
    if compress is not None:
        parts.append(("Compress", f"(type={compress})"))
    tok = _grp(*parts)
    if has_filters:
        tok += ", FilterList=()"
    return tok


# ── Perform Script on Server with Callback (dedicated: mixed blob) ────────────
@_decoder("Perform Script on Server with Callback")
def _dec_psos(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    # Callback script state enum.
    st = _ptype(step_el, "CallbackScriptState")
    sl = st.find("List") if st is not None else None
    if sl is not None:
        p = v.param(xml="CallbackScriptState")
        v.set(_enum_tok(p, sl.get("name", "")), xml="CallbackScriptState")
    # PSoS parameter calc (<Parameter type="Parameter"><Parameter><Calculation>).
    par = _ptype(step_el, "Parameter")
    if par is not None:
        v.set(_calc_text(par), xml="Calculation")
    # The server script reference (Specified: From list) — empty ref emits <Script/>.
    sref = None
    for lst in _ptypes(step_el, "List"):
        r = lst.find(".//ScriptReference")
        if r is not None:
            sref = r
            break
    if sref is not None and sref.get("name"):
        v.set('"' + sref.get("name", "") + '"', xml="Script")
    # Callback script attrGroup: Parameter (calc) + optional Script name.
    cp = _ptype(step_el, "CallbackScriptParameter")
    cb_parts = []
    if cp is not None:
        calc = _calc_text(cp)
        if calc:
            cb_parts.append(("Parameter", calc))
    v.set(_grp(*cb_parts), xml="CallbackScript")
    return v.list()


# ── Export Records (Profile by format + ExportOptions + two fieldLists) ───────
# Export/Save profile blobs are format constants (the SaXML fileType selects them).
_FORMAT_PROFILE = {
    "TABS": 'FieldDelimiter="\t", IsPredefined=-1, FieldNameRow=-1, DataType=TABS',
    "MERG": 'FieldDelimiter=",", IsPredefined=-1, FieldNameRow=-1, DataType=MERG',
    "CACO": 'DataType=CACO, IsPredefined=-1, FieldNameRow=-1',
    "DBF ": "DataType=DBF, IsPredefined=-1",
    "SLK ": "DataType=SLK, IsPredefined=-1",
    "HTML": "DataType=HTML, IsPredefined=-1",
    "XML ": "DataType=XML, IsPredefined=-1",
    "XLXE": 'DataType=XLXE, FieldDelimiter="\t", FieldNameRow=-1, IsPredefined=-1',
}
_CHARSET = {"Unicode (UTF-8)": "UTF-8", "UTF-8": "UTF-8", "Unicode (UTF-16)": "UTF-16",
            "Windows (ANSI)": "Windows", "Macintosh": "Macintosh", "DOS": "DOS"}


def _profile_for(ftype: str) -> str:
    return _FORMAT_PROFILE.get(ftype, f"DataType={ftype.strip()}")


@_decoder("Export Records")
def _dec_export(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    for p in step_el.findall("ParameterValues/Parameter"):
        b = p.find("Boolean")
        if b is not None:
            on = "On" if b.get("value") == "True" else "Off"
            if b.get("type") == "With dialog":
                v.set(on, xml="NoInteract")
            elif b.get("type") == "Create folders":
                v.set(on, xml="CreateDirectories")
    upl = _ptype(step_el, "UniversalPathList")
    ue = upl.find("UniversalPathList") if upl is not None else None
    if ue is not None:
        v.set("On" if ue.get("AutoOpen") == "True" else "Off", xml="AutoOpen")
        v.set("On" if ue.get("CreateMail") == "True" else "Off", xml="CreateEmail")
        loc = ue.find(".//Location")
        if loc is not None:
            v.set(loc.text or "", xml="UniversalPathList")
        v.set(_profile_for(ue.get("fileType", "")), xml="Profile")
    exp = _ptype(step_el, "Export")
    ex = exp.find("Export") if exp is not None else None
    if ex is not None:
        o = ex.find("Options")
        if o is not None:
            cs = _CHARSET.get(o.get("name", ""), o.get("name", ""))
            fmt = "True" if o.get("Formatting") == "True" else "False"
            v.set(f"FormatUsingCurrentLayout={fmt}, CharacterSet={cs}", xml="ExportOptions")
        entries, groups = [], []
        for order in ex.findall("Order"):
            if order.get("type") == "Field":
                for fl in order.findall("Field"):
                    fr = fl.find("FieldReference")
                    entries.append(_field_ref_token(fr) if fr is not None else "")
            elif order.get("type") == "Group":
                for g in order.findall("Group"):
                    fr = g.find("FieldReference")
                    groups.append((_field_ref_token(fr) if fr is not None else "") + "=True")
        v.set(", ".join(entries), xml="ExportEntries")
        v.set(", ".join(groups), xml="SummaryFields")
    v.set("On", xml="Restore")  # Export always restores its stored order
    return v.list()


# ── Save Records as Excel (Profile const + WorkSheet/Title/Subject/Author) ────
@_decoder("Save Records as Excel")
def _dec_excel(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    for p in step_el.findall("ParameterValues/Parameter"):
        b = p.find("Boolean")
        if b is not None:
            on = "On" if b.get("value") == "True" else "Off"
            if b.get("type") == "With dialog":
                v.set(on, xml="NoInteract")
            elif b.get("type") == "Create folders":
                v.set(on, xml="CreateDirectories")
    rp = _ptype(step_el, "Restore")
    rr = rp.find("Restore") if rp is not None else None
    v.set("On" if (rr is not None and rr.get("value") == "True") else "Off", xml="Restore")
    prof = v.param(xml="Profile")
    if prof is not None:
        v.set(", ".join(f"{k}={val}" for k, val in _field_defaults_token(prof).items()), xml="Profile")
    upl = _ptype(step_el, "UniversalPathList")
    ue = upl.find("UniversalPathList") if upl is not None else None
    if ue is not None:
        v.set("On" if ue.get("AutoOpen") == "True" else "Off", xml="AutoOpen")
        v.set("On" if ue.get("CreateMail") == "True" else "Off", xml="CreateEmail")
        loc = ue.find(".//Location")
        if loc is not None:
            v.set(loc.text or "", xml="UniversalPathList")
    opt = _ptype(step_el, "Options")
    o = opt.find("Options") if opt is not None else None
    if o is not None:
        save = o.find("Save")
        if save is not None:
            v.set(save.get("type", ""), xml="SaveType")
            # UseFieldNames is INVERTED between SaXML and fmxmlsnippet (SaXML
            # useFieldNames="False" ⇔ fmxmlsnippet UseFieldNames state="True").
            v.set("Off" if save.get("useFieldNames") == "True" else "On", xml="UseFieldNames")
        for sp in o.findall("Parameter"):
            wrap = {"Worksheet": "WorkSheet", "Title": "Title",
                    "Subject": "Subject", "Author": "Author"}.get(sp.get("type", ""))
            if wrap:
                v.set(_calc_text(sp), wrap=wrap)
    return v.list()


# ── Import Records (ImportOptions + TargetFields map fieldList) ────────────────
@_decoder("Import Records")
def _dec_import(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    for p in step_el.findall("ParameterValues/Parameter"):
        b = p.find("Boolean")
        if b is not None:
            on = "On" if b.get("value") == "True" else "Off"
            if b.get("type") == "With dialog":
                v.set(on, xml="NoInteract")
            elif b.get("type") == "Verify SSL Certificates":
                v.set(on, xml="VerifySSLCertificates")
    # The import source path lives in <DataSourceReference> (real imports) or a bare
    # <UniversalPathList> (degenerate). DataSourceReference also names the source type.
    dsr = _ptype(step_el, "DataSourceReference")
    src = dsr if dsr is not None else _ptype(step_el, "UniversalPathList")
    if src is not None:
        loc = src.find(".//Location")
        if loc is not None:
            v.set(loc.text or "", xml="UniversalPathList")
        v.set("File", xml="DataSourceType")
    imp = _ptype(step_el, "ImportField")
    im = imp.find("ImportField") if imp is not None else None
    if im is not None:
        o = im.find("Options")
        cs = im.find("CharacterSet")
        act = im.find("action")
        opts = []
        if cs is not None:
            opts.append(f"CharacterSet={cs.get('name', '')}")
        if o is not None:
            opts.append(f"PreserveContainer={o.get('copyContainersAsIs', 'False')}")
            opts.append(f"MatchFieldNames={o.get('matchFieldNames', 'False')}")
            opts.append(f"AutoEnter={o.get('doAutoEntry', 'True')}")
            opts.append(f"SplitRepetitions={o.get('splitRepetitions', 'False')}")
        if act is not None:
            opts.append(f"method={act.get('name', 'Add')}")
        v.set(", ".join(opts), xml="ImportOptions")
        fld = im.find("Field")
        ents = []
        if fld is not None:
            for m in fld.findall("Map"):
                fr = m.find("FieldReference")
                ref = _field_ref_token(fr) if fr is not None else ""
                ents.append(f"{ref}={'Import' if m.get('kind') == '0' else 'DoNotImport'}")
        v.set(", ".join(ents), xml="TargetFields")
    return v.list()


# ── Send Mail (one <Parameter type="Email"> blob → many params) ───────────────
# SaXML <SMTP>/<OAuthAuthentication> child element → catalog wrapperElement.
_SMTP_MAP = {"Name": "SMTPNameDescription", "Email": "SMTPEmailAddress",
             "ReplyTo": "SMTPReplyAddress", "Server": "SMTPServer", "Port": "SMTPPort",
             "UserName": "SMTPUserName", "Password": "SMTPPassword"}
_OAUTH_MAP = {"Name": "OAuthNameDescription", "Email": "OAuthEmailAddress",
              "ReplyTo": "OAuthReplyAddress", "UserID": "OAuthUserID",
              "ServiceAccount": "OAuthAccountEmail", "PrivateKey": "OAuthPrivateKey"}
_SMTP_ENC = {"TLS": "SMTPEncryptionTLS", "SSL": "SMTPEncryptionSSL", "None": "SMTPEncryptionNone"}
_SMTP_AUTH = {"Plain Password": "SMTPAuthenticationPlain", "None": "SMTPAuthenticationNone"}


@_decoder("Send Mail")
def _dec_send_mail(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    em = _ptype(step_el, "Email")
    if em is None:
        return v.list()
    # FM ALWAYS serializes the encryption/auth/provider enums (defaults even for the
    # inactive auth mode); the active <SMTP> block overrides encryption/auth below.
    v.set("SMTPEncryptionTLS", xml="SMTPEncryptionType")
    v.set("SMTPAuthenticationPlain", xml="SMTPAuthenticationType")
    v.set("OAuthProviderGoogle", xml="OAuthProvider")
    nd = em.find("Boolean")  # <Boolean type="No dialog">
    if nd is not None:
        v.set("Off" if nd.get("value") == "True" else "On", xml="NoInteract")
    upl = em.find("UniversalPathList")
    if upl is not None:
        loc = upl.find(".//Location")
        if loc is not None:
            v.set(loc.text or "", xml="UniversalPathList")
    send = em.find("Send")
    if send is not None:
        for tag, xe in (("To", "To"), ("CC", "Cc"), ("BCC", "Bcc")):
            t = send.find(tag)
            if t is None:
                continue
            ca = t.find("CollectAddresses")
            usefs = ca.get("value", "False") if ca is not None else "False"
            calc = _calc_text(t)
            # FM omits an empty recipient element (no value + not found-set mode).
            if calc or usefs == "True":
                v.set(f"useFoundSet={usefs}, value={calc}", xml=xe)
        for tag, wrap in (("Subject", "Subject"), ("Message", "Message")):
            t = send.find(tag)
            if t is not None:
                v.set(_calc_text(t), wrap=wrap)
        v.set("On" if send.get("SMTP") == "True" else "Off", xml="SendViaSMTP")
        v.set("On" if send.get("OAuthAuthentication") == "True" else "Off",
              xml="SendViaOAuthAuthentication")
        mult = send.find("Multiple")
        v.set("On" if (mult is not None and mult.get("value") == "True") else "Off",
              xml="MultipleEmails")
    sm = em.find("SMTP")
    if sm is not None:
        for tag, wrap in _SMTP_MAP.items():
            el = sm.find(tag)
            if el is not None and _find_value_calc(el) is not None:
                v.set(_calc_text(el), wrap=wrap)
        enc = sm.find("Encryption")
        if enc is not None and enc.get("name"):
            v.set(_SMTP_ENC.get(enc.get("name", ""), "SMTPEncryption" + enc.get("name", "")),
                  xml="SMTPEncryptionType")
        au = sm.find("Authentication")
        if au is not None and au.get("name"):
            v.set(_SMTP_AUTH.get(au.get("name", ""),
                                 "SMTPAuthentication" + au.get("name", "").replace(" ", "")),
                  xml="SMTPAuthenticationType")
    oa = em.find("OAuthAuthentication")
    if oa is not None:
        for tag, wrap in _OAUTH_MAP.items():
            el = oa.find(tag)
            if el is not None and _find_value_calc(el) is not None:
                v.set(_calc_text(el), wrap=wrap)
        prov = oa.find("OAuthProvider")
        if prov is not None and prov.get("name"):
            v.set("OAuthProvider" + prov.get("name", ""), xml="OAuthProvider")
    return v.list()


# ── Save Records as PDF (deep PDFOptions: Document/Security/View subgroups) ────
_PDF_EDIT = {"Any except extracting pages": "AnyExceptExtractingPages", "None": "None",
             "Any except extracting or printing pages": "AnyExceptExtractingOrPrinting",
             "Only document assembly": "OnlyDocumentAssembly"}
_PDF_PRINT = {"High Resolution": "HighResolution", "Low Resolution": "LowResolution", "None": "None"}
_PDF_LAYOUT = {"Single Page": "SinglePage", "Continuous": "Continuous",
               "Continuous - Facing": "ContinuousFacing", "Default": "Default"}
_PDF_SHOW = {"Pages Panel and Page": "PagesPanelAndPage", "Page Only": "PageOnly",
             "Bookmarks Panel and Page": "BookmarksPanelAndPage", "Default": "Default"}
_PDF_MAG = {"100%": "100", "Default": "Default", "Fit Page": "FitPage", "Fit Width": "FitWidth"}


@_decoder("Save Records as PDF")
def _dec_pdf(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    for p in step_el.findall("ParameterValues/Parameter"):
        b = p.find("Boolean")
        if b is not None:
            on = "On" if b.get("value") == "True" else "Off"
            t = b.get("type")
            if t == "With dialog":
                v.set(on, xml="NoInteract")
            elif t == "Append to existing PDF":
                v.set(on, xml="Option")
            elif t == "Create folders":
                v.set(on, xml="CreateDirectories")
    rp = _ptype(step_el, "Restore")
    rr = rp.find("Restore") if rp is not None else None
    v.set("On" if (rr is not None and rr.get("value") == "True") else "Off", xml="Restore")
    upl = _ptype(step_el, "UniversalPathList")
    if upl is not None:
        loc = upl.find(".//Location")
        if loc is not None:
            v.set(loc.text or "", xml="UniversalPathList")
    opt = _ptype(step_el, "Options")
    o = opt.find("Options") if opt is not None else None
    if o is None:
        return v.list()
    source = _PDF_SOURCE.get(o.get("type", ""), o.get("type", "").replace(" ", ""))
    save_type = "File"
    sr = _ptype(step_el, "SaveResult")
    srl = sr.find("List") if sr is not None else None
    if srl is not None:
        save_type = srl.get("name", "File")
    # The saved-label calc mirrors the PDF Title (FM emits it as a bare Calculation).
    doc = o.find("Document")
    doc_parts = []
    if doc is not None:
        for tag in ("Title", "Subject", "Author", "Keywords"):
            sp = None
            for x in doc.findall("Parameter"):
                if x.get("type") == tag:
                    sp = x
                    break
            calc = _calc_text(sp) if sp is not None else ""
            if tag == "Title" and calc:
                v.set(calc, xml="Calculation")  # the bare saved-label calc
            if calc:
                doc_parts.append(f"{tag}={calc}")
        pages = o.find("Pages")  # <Pages> is a sibling of <Document> under <Options>
        if pages is not None:
            inc = pages.find("Include")
            allp = inc.get("All", "True") if inc is not None else "True"
            frm = None
            for x in pages.findall("Parameter"):
                if x.get("type") == "from":
                    frm = x
                    break
            pg = [f"AllPages={allp}"]
            nf = _calc_text(frm) if frm is not None else ""
            if nf:
                pg.append(f"NumberFrom={nf}")
            doc_parts.append("Pages=(" + ", ".join(pg) + ")")
    sec = o.find("Security")
    sec_tok = ""
    if sec is not None:
        def _sv(tag, attr):
            e = sec.find(tag)
            return e.get(attr, "") if e is not None else ""
        sec_tok = "Security=(" + _grp(
            ("ScreenReader", _sv("AllowScreenReader", "value") or None),
            ("Copying", _sv("EnableCopying", "value") or None),
            ("Editing", _PDF_EDIT.get(_sv("Edit", "name"), _sv("Edit", "name")) or None),
            ("Printing", _PDF_PRINT.get(_sv("Print", "name"), _sv("Print", "name")) or None),
        ) + ")"
    view = o.find("View")
    view_tok = ""
    if view is not None:
        def _vn(tag):
            e = view.find(tag)
            return e.get("name", "") if e is not None else ""
        view_tok = "View=(" + _grp(
            ("Magnification", _PDF_MAG.get(_vn("Magnification"), _vn("Magnification")) or None),
            ("Layout", _PDF_LAYOUT.get(_vn("Layout"), _vn("Layout")) or None),
            ("Show", _PDF_SHOW.get(_vn("show"), _vn("show")) or None),
        ) + ")"
    parts = [f"source={source}", f"PDFSaveType={save_type}"]
    if doc_parts:
        parts.append("Document=(" + ", ".join(doc_parts) + ")")
    if sec_tok:
        parts.append(sec_tok)
    if view_tok:
        parts.append(view_tok)
    v.set(", ".join(parts), xml="PDFOptions")
    return v.list()


_PDF_SOURCE = {"Records being browsed": "RecordsBeingBrowsed", "Current record": "CurrentRecord"}


# ── Find family (findRequests) — <FindRequestSet> → the packed HR token ────────
def _decode_find_requests(step_el: ET.Element) -> str:
    """SaXML ``<FindRequestSet>`` → the findRequests HR token the emit reverses:
    rows joined ' | ', omit rows prefixed 'Omit ', criteria joined ' & ', each
    ``Table::Field: <criteria>`` (the criteria text is the ``<find criteria="">``
    attribute). Returns '' when there is no request set."""
    fp = _ptype(step_el, "FindRequest")
    fset = fp.find("FindRequestSet") if fp is not None else None
    if fset is None:
        return ""
    rows = []
    for req in fset.findall("FindRequest"):
        crits = []
        for fnd in req.findall("find"):
            fr = fnd.find("FieldReference")
            field = _field_ref_token(fr) if fr is not None else ""
            text = fnd.get("criteria", "")
            crits.append(f"{field}: {text}" if field else text)
        row = " & ".join(crits)
        if req.get("action") == "omit":
            row = "Omit " + row if row else "Omit"
        rows.append(row)
    return " | ".join(rows)


# FileMaker keeps the find steps' "Restore" (Specify find requests) only in the
# step's <Options> bitmask: no <Parameter> carries it, and a step whose stored
# request set is empty has no <FindRequestSet> either way. Measured over every
# find-family step in several FileMaker 22.0.4 and 26.0.3 Save a Copy as XML
# exports: each one FileMaker draws with Restore has this bit set (33554432 alone
# for a Perform Find [ Restore ] with no request set, its clipboard XML
# <Restore state="True"/>), and each one without it has Options 0. The bit is
# the same number FileMaker writes as the id of the matching <Boolean>, as Enter
# Find Mode's Pause (id 16777216) shows.
_FIND_RESTORE_OPTION_BIT = 0x02000000


def _options_bit(step_el: ET.Element, bit: int) -> bool:
    try:
        return bool(int((step_el.findtext("Options") or "0").strip()) & bit)
    except ValueError:
        raise UnsupportedSaXML(f"unreadable <Options> {step_el.findtext('Options')!r}")


def _find_family(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    v = _Vals(entry)
    for p in step_el.findall("ParameterValues/Parameter"):
        b = p.find("Boolean")
        if b is None:
            continue
        for param in entry.params:
            if param.type == "boolean" and param.hr_label == b.get("type"):
                v.set("On" if b.get("value") == "True" else "Off", xml=param.xml_element)
                break
    query = _decode_find_requests(step_el)
    v.set(query, xml="Query")
    restore = _options_bit(step_el, _FIND_RESTORE_OPTION_BIT)
    if query and not restore:
        # Never measured: a stored request set FileMaker does not restore.
        raise UnsupportedSaXML(f"{entry.name}: <FindRequestSet> without the Restore option bit")
    v.set("On" if restore else "Off", xml="Restore")
    return v.list()


@_decoder("Perform Find", "Constrain Found Set", "Extend Found Set", "Enter Find Mode")
def _dec_find_family(entry: CatalogEntry, step_el: ET.Element) -> list[str]:
    return _find_family(entry, step_el)


# ---------------------------------------------------------------------------
# Public entry point
def _reject_unaddressed_calcs(
    entry: CatalogEntry, sparams: list[ET.Element], claimed: set[int]
) -> None:
    """Refuse a calc the addresses do not account for, on a step that HAS addresses.

    A step in ``_SAXML_CALC_PARAMS`` is one whose calc layout was measured whole. A calc
    left over afterwards therefore means the measurement no longer matches what
    FileMaker exports — a param added by a later version, or a mode this capture never
    exercised. Reading the rest and dropping that one would be the silent loss the
    addresses exist to end, so the step is counted unsupported instead.
    """
    if entry.name not in _SAXML_CALC_PARAMS:
        return
    for i, sp in enumerate(sparams):
        for node in _value_calcs(sp):
            if id(node) not in claimed:
                raise UnsupportedSaXML(
                    f"{entry.name}: SaXML carries a calculation at "
                    f"{_saxml_seg(sp)!r} that no catalog param addresses "
                    f"({_calc_of(node)!r}) — the measured calc layout is out of date")


# ---------------------------------------------------------------------------
def read_saxml_step(
    entry: CatalogEntry, step_el: ET.Element
) -> tuple[bool, list[str], SeededResolver]:
    """Read a SaXML ``<Step>`` into ``(disabled, values[], resolver)`` for emit.

    A step with a dedicated decoder (fully composite/blob shape) is routed to it;
    otherwise the generic per-param extractor runs, with composite facet params
    handled by a registered ``_COMPOSITE_BUILDERS`` builder. Raises
    ``UnsupportedSaXML`` only when a composite facet still has no builder — callers
    count these rather than emitting silently-wrong XML.
    """
    disabled = step_el.get("enable", "True") == "False"
    resolver = _seed_resolver(step_el)
    dec = _DECODERS.get(entry.name)
    if dec is not None:
        return disabled, _keep_unread_flag_defaults(entry, dec(entry, step_el)), resolver
    sparams = _params(step_el)
    consumed = [False] * len(sparams)
    claimed: set[int] = set()
    values = [""] * len(entry.params)
    for pi, param in enumerate(entry.params):
        if param.hr_hidden or param.type == "complex":
            continue
        if param.type in _COMPOSITE_TYPES:
            builder = _COMPOSITE_BUILDERS.get((entry.name, param.xml_element))
            if builder is None:
                raise UnsupportedSaXML(
                    f"{entry.name}: no decoder for {param.type} param {param.xml_element!r}")
            values[pi] = builder(entry, param, step_el)
        else:
            values[pi] = _extract_simple(entry, param, sparams, consumed, claimed)
    _reject_unaddressed_calcs(entry, sparams, claimed)
    return disabled, _keep_unread_flag_defaults(entry, values), resolver


def _keep_unread_flag_defaults(entry: CatalogEntry, values: list[str]) -> list[str]:
    """Give a flag-style boolean this reader found no value for its catalog default.

    In HR an absent flag-style label means off, and the emit reads an empty value
    that way. Here an empty value means the SaXML carried nothing this reader
    decodes for the param (``Go to Related Record``'s Restore, ``Print``'s
    ``<Parameter type="Restore">``), which is not the same as off: keep the
    fresh-step default the emit used to apply, as an explicit token."""
    out = list(values)
    # A companion a sibling discriminator reveals (Set Window Title's Current file
    # under Current Window) takes its state from that discriminator on emit.
    governed = {
        name
        for q in entry.params
        for dv in (q.raw.get("discriminatorValues") or {}).values()
        if isinstance(dv, dict)
        for name in (dv.get("reveal") or [])
    }
    for i, param in enumerate(entry.params):
        if i >= len(out) or out[i] != "" or not param.flag_style or param.type != "boolean":
            continue
        if param.xml_element in governed:
            continue
        if not param.default_value:
            continue
        on = (param.default_value == "True") != bool(param.inverted_hr)
        out[i] = "On" if on else "Off"
    return out
