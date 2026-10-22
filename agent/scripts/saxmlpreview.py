#!/usr/bin/env python3
"""
saxmlpreview.py — Convert a FileMaker SaXML script file to Script Workspace format.

Usage:
    python3 agent/scripts/saxmlpreview.py <path-to-script.xml>

Each <Step> element in the SaXML produces exactly one output line, so line numbers
are deterministic and 1:1 with what a developer sees in FileMaker Script Workspace.

Architecture (P6.4 — catalog unification):
    Non-control steps are decoded by ``saxml_read.read_saxml_step`` → the shared
    ``values[]`` token array → ``catalog_emit.convert_step_with_catalog`` →
    ``catalog_grammar.render_step_hr``, i.e. the SAME catalog grammar the fmxmlsnippet
    converters use — no per-step preview logic. Only the control-flow primitives stay
    hand-coded here (they drive the block-indentation the grammar engine does not
    model): ``# (comment)``, If / Else If / Else / End If, Loop / Exit Loop If /
    End Loop, Exit Script, Set Variable.

Block indentation follows Script Workspace rules:
    - If, Loop         → render at current level, then indent +1
    - Else, Else If    → indent -1, render, then indent +1
    - End If, End Loop → indent -1, render
Disabled steps are prefixed with '// '. Blank comment steps render as blank lines.
"""

import os
import sys
import xml.etree.ElementTree as ET

INDENT = "    "  # 4 spaces per indentation level


# ---------------------------------------------------------------------------
# Locate the repo + wire the shared catalog grammar engine (agent/scripts)
# ---------------------------------------------------------------------------
def _find_repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isfile(os.path.join(here, "agent", "catalogs", "step-catalog-en.json")):
            return here
        here = os.path.dirname(here)
    return None


_REPO = _find_repo_root()
if _REPO:
    sys.path.insert(0, os.path.join(_REPO, "agent", "scripts"))

try:
    from catalog_emit import convert_step_with_catalog
    from catalog_grammar import load_catalog, render_step_hr
    from saxml_read import UnsupportedSaXML, read_saxml_step
    _CATALOG = {e.name: e for e in load_catalog(
        os.path.join(_REPO, "agent", "catalogs", "step-catalog-en.json"))} if _REPO else {}
except Exception:  # noqa: BLE001 — degrade to name-only preview if the engine is absent
    convert_step_with_catalog = render_step_hr = read_saxml_step = None
    UnsupportedSaXML = Exception
    _CATALOG = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_cdata(element):
    """First <Text> descendant, collapsed to one line (SW's single-row display)."""
    if element is None:
        return ''
    node = element.find('.//Text')
    if node is not None and node.text:
        return ' '.join(node.text.split())
    return ''


# ---------------------------------------------------------------------------
# Control-flow renderers (the sanctioned hand-coded exception — they own the
# block-indentation the catalog grammar engine does not model)
# ---------------------------------------------------------------------------
def _render_control(step, step_id, pfx):
    """Return (line, (close_before, open_after)) for a control-flow step, or None."""
    if step_id == 89:  # # (comment) / blank line
        comment_el = step.find('.//Comment')
        text = comment_el.get('value', '') if comment_el is not None else ''
        if pfx:  # disabled
            return pfx + ('# ' + text if text else '# '), (False, False)
        return (('# ' + text) if text else ''), (False, False)
    if step_id == 68:  # If
        return f'{pfx}If [ {get_cdata(step.find(".//Parameter[@type=\"Calculation\"]"))} ]', (False, True)
    if step_id == 69:  # Else
        return f'{pfx}Else', (True, True)
    if step_id == 125:  # Else If
        return f'{pfx}Else If [ {get_cdata(step.find(".//Parameter[@type=\"Calculation\"]"))} ]', (True, True)
    if step_id == 70:  # End If
        return f'{pfx}End If', (True, False)
    if step_id == 71:  # Loop
        list_param = step.find('ParameterValues/Parameter[@type="List"]')
        flush = ''
        if list_param is not None:
            le = list_param.find('List')
            if le is not None and le.get('name') == 'Always':
                flush = ' [ Flush: Always ]'
        return f'{pfx}Loop{flush}', (False, True)
    if step_id == 72:  # Exit Loop If
        return f'{pfx}Exit Loop If [ {get_cdata(step.find(".//Parameter[@type=\"Calculation\"]"))} ]', (False, False)
    if step_id == 73:  # End Loop
        return f'{pfx}End Loop', (True, False)
    if step_id == 103:  # Exit Script
        result = get_cdata(step.find('.//Parameter[@type="Calculation"]'))
        if result:
            return f'{pfx}Exit Script [ Text Result: {result} ]', (False, False)
        return f'{pfx}Exit Script [ Text Result:    ]', (False, False)
    if step_id == 141:  # Set Variable
        var_param = step.find('ParameterValues/Parameter[@type="Variable"]')
        if var_param is not None:
            name_el = var_param.find('Name')
            var_name = name_el.get('value', '') if name_el is not None else ''
            expr = get_cdata(var_param.find('value'))
            return f'{pfx}Set Variable [ {var_name} ; Value: {expr} ]', (False, False)
        return f'{pfx}Set Variable [ ]', (False, False)
    return None


# ---------------------------------------------------------------------------
# Step renderer
# ---------------------------------------------------------------------------
def render_step(step):
    """Render a single <Step> to its SW line (no indentation).

    Returns (line_text, (close_before, open_after))."""
    step_id = int(step.get('id', 0))
    enabled = step.get('enable', 'True') == 'True'
    name = step.get('name', '')
    pfx = '' if enabled else '// '

    control = _render_control(step, step_id, pfx)
    if control is not None:
        return control

    # Every other step: SaXML → values[] → fmxmlsnippet → catalog HR.
    entry = _CATALOG.get(name)
    if entry is not None and read_saxml_step is not None:
        try:
            disabled, values, resolver = read_saxml_step(entry, step)
            xml = convert_step_with_catalog(entry, disabled, values, resolver)
            node = ET.fromstring('<fmxmlsnippet>' + xml + '</fmxmlsnippet>').find('Step')
            hr = render_step_hr(entry, node)
            # SW shows each step on ONE row — collapse multi-line calcs to keep the
            # preview's 1:1 line↔step mapping.
            hr = ' '.join(hr.split())
            return f'{pfx}{hr}', (False, False)
        except UnsupportedSaXML:
            return f'{pfx}{name} [ … unsupported SaXML shape … ]', (False, False)
        except Exception:  # noqa: BLE001 — never break the preview; fall back to name-only
            return f'{pfx}{name}', (False, False)
    return f'{pfx}{name}', (False, False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def convert(xml_path):
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f'ERROR: Could not parse XML: {e}', file=sys.stderr)
        sys.exit(1)

    root = tree.getroot()
    script_ref = root.find('.//ScriptReference')
    script_name = script_ref.get('name', 'Unknown') if script_ref is not None else 'Unknown'

    object_list = root.find('.//ObjectList')
    if object_list is None:
        print('ERROR: No <ObjectList> found in XML.', file=sys.stderr)
        sys.exit(1)

    lines = []
    indent = 0
    for step in object_list.findall('Step'):
        text, (close_before, open_after) = render_step(step)
        if close_before:
            indent = max(0, indent - 1)
        lines.append(INDENT * indent + text)
        if open_after:
            indent += 1

    print(f'Script: {script_name}')
    print()
    for i, line in enumerate(lines, 1):
        print(f'{i}\t{line}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f'Usage: python3 {sys.argv[0]} <path-to-script.xml>', file=sys.stderr)
        sys.exit(1)
    convert(sys.argv[1])
