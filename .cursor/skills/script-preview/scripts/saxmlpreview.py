#!/usr/bin/env python3
"""
saxmlpreview.py — Convert a FileMaker SaXML script file to Script Workspace format.

Usage:
    python3 .claude/skills/script-preview/scripts/saxmlpreview.py <path-to-script.xml>

Each <Step> element in the SaXML produces exactly one output line, making the line
numbers deterministic and 1:1 with what a developer sees in FileMaker Script Workspace.

Blank lines (empty # comment steps) are rendered as blank lines — matching what
a developer sees in Script Workspace. (The MBS plug-in copies them as '# ' but
that is a copy artefact, not the actual presentation.)

Disabled steps are prefixed with '//'.

Block indentation follows Script Workspace rules:
    - If, Loop         → render at current level, then indent +1
    - Else, Else If    → indent -1, render, then indent +1
    - End If, End Loop → indent -1, render

Parameter rendering is driven by agent/catalogs/step-catalog-en.json.  Specific
handlers are kept only for structurally unique steps (block control, Set Variable,
Perform Script, Show Custom Dialog, Exit Script).  All other steps use a generic
renderer that uses the catalog hrSignature + SaXML parameter types to produce the
correct output without per-step hard-coding.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

INDENT = "    "  # 4 spaces per indentation level

# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def _find_catalog():
    """Locate step-catalog-en.json relative to the repo root."""
    # Walk up from this script's location to find agent/catalogs/
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidate = os.path.join(here, 'agent', 'catalogs', 'step-catalog-en.json')
        if os.path.isfile(candidate):
            return candidate
        here = os.path.dirname(here)
    return None


def _load_catalog():
    """Return a dict keyed by step id (int) with the full catalog entry."""
    path = _find_catalog()
    if path is None:
        return {}
    with open(path, encoding='utf-8') as f:
        entries = json.load(f)
    return {entry['id']: entry for entry in entries if 'id' in entry}


CATALOG = _load_catalog()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_cdata(element):
    """
    Find the first <Text> descendant and return its text content.
    Multi-line calculations are collapsed to a single line — matching
    Script Workspace's single-row display.
    """
    if element is None:
        return ''
    node = element.find('.//Text')
    if node is not None and node.text:
        return ' '.join(node.text.split())
    return ''


# ---------------------------------------------------------------------------
# Generic parameter renderer (catalog-driven)
# ---------------------------------------------------------------------------

def _render_params(step):
    """
    Generically extract and render the bracket content of a step using
    the SaXML <ParameterValues> structure, guided by the catalog hrSignature.

    Returns the content that goes between [ and ] (may be empty).
    """
    step_id  = int(step.get('id', 0))
    entry    = CATALOG.get(step_id, {})
    hr_sig   = entry.get('hrSignature') or ''

    parts = []

    for param_el in step.findall('ParameterValues/Parameter'):
        ptype = param_el.get('type', '')

        # ── Boolean ──────────────────────────────────────────────────────────
        if ptype == 'Boolean':
            bool_el = param_el.find('Boolean')
            if bool_el is None:
                continue
            b_type = bool_el.get('type', '')
            b_val  = bool_el.get('value', 'False') == 'True'

            if b_type == 'Collapsed':
                continue  # internal display state

            if b_type:
                # Labelled boolean — check hrSignature to decide display style.
                # If hrSignature references the label with On/Off (any form) → always show.
                # Otherwise treat as an optional flag (show label only when True).
                sig_has_label = (
                    f'{b_type}: On|Off' in hr_sig or
                    f'{b_type}: Off|On' in hr_sig or
                    f'{b_type}: On' in hr_sig or
                    f'{b_type}: Off' in hr_sig
                )
                if sig_has_label:
                    parts.append(f'{b_type}: {"On" if b_val else "Off"}')
                elif b_val:
                    parts.append(b_type)
            else:
                # Unlabelled boolean (e.g. Allow User Abort, Set Error Capture).
                # hrSignature "[ On|Off ]" tells us to render as On or Off.
                if 'On|Off' in hr_sig or 'Off|On' in hr_sig:
                    parts.append('On' if b_val else 'Off')
                elif b_val:
                    parts.append('True')

        # ── Calculation ───────────────────────────────────────────────────────
        elif ptype == 'Calculation':
            calc = get_cdata(param_el)
            if calc:
                parts.append(calc)

        # ── Options (e.g. Pause/Resume Script duration) ───────────────────────
        elif ptype == 'Options':
            opts_el = param_el.find('Options')
            if opts_el is not None:
                label = opts_el.get('type', '')
                calc  = get_cdata(opts_el)
                text  = f'{label}{calc}'.strip()
                if text:
                    parts.append(text)

        # ── List / enum selection ─────────────────────────────────────────────
        elif ptype == 'List':
            list_el = param_el.find('List')
            if list_el is not None:
                # Script reference inside a list (used by Perform Script etc.)
                ref = list_el.find('.//ScriptReference')
                if ref is not None:
                    parts.append(f'"{ref.get("name", "")}"')
                else:
                    name = list_el.get('name', '')
                    if name:
                        parts.append(name)

        # ── Direct script reference ───────────────────────────────────────────
        elif ptype == 'ScriptReference':
            ref = param_el.find('ScriptReference')
            if ref is not None:
                parts.append(f'"{ref.get("name", "")}"')

        # ── Field reference ───────────────────────────────────────────────────
        elif ptype == 'Field':
            field_el = param_el.find('Field')
            if field_el is not None:
                tbl = field_el.get('table', '')
                fld = field_el.get('name', '')
                ref = f'{tbl}::{fld}' if tbl else fld
                if ref:
                    parts.append(ref)

        # ── Layout reference ──────────────────────────────────────────────────
        elif ptype == 'Layout':
            layout_el = param_el.find('.//Layout')
            if layout_el is not None:
                lname = layout_el.get('name', '')
                parts.append(f'"{lname}"')

        # ── Target (Insert Text target variable/field) ───────────────────────────
        elif ptype == 'Target':
            var_el = param_el.find('Variable')
            if var_el is not None:
                var_name = var_el.get('value', '')
                if var_name:
                    parts.append(f'Target: {var_name}')
            else:
                field_el = param_el.find('Field')
                if field_el is not None:
                    tbl = field_el.get('table', '')
                    fld = field_el.get('name', '')
                    ref = f'{tbl}::{fld}' if tbl else fld
                    if ref:
                        parts.append(f'Target: {ref}')

        # ── Portal row direction (Go to Portal Row) ───────────────────────────────
        elif ptype == 'Portal':
            list_el = param_el.find('List')
            if list_el is not None:
                direction = list_el.get('name', '')
                if direction:
                    parts.append(direction)
                exit_el = list_el.find('Boolean[@type="Exit after last"]')
                if exit_el is not None and exit_el.get('value', 'False') == 'True':
                    parts.append('Exit after last: On')

        # ── Window reference (Close Window, etc.) ────────────────────────────────
        elif ptype == 'WindowReference':
            win_ref = param_el.find('WindowReference')
            if win_ref is not None:
                select_el = win_ref.find('Select')
                if select_el is not None:
                    name_el = select_el.find('Name')
                    if name_el is not None:
                        win_name = get_cdata(name_el)
                        if win_name:
                            parts.append(f'Name: {win_name}')
                        if name_el.get('current', 'False') == 'True':
                            parts.append('Current file')

        # ── Layout reference container (Go to Layout etc.) ───────────────────────
        elif ptype == 'LayoutReferenceContainer':
            container_el = param_el.find('LayoutReferenceContainer')
            if container_el is not None:
                layout_ref = container_el.find('LayoutReference')
                if layout_ref is not None:
                    lname = layout_ref.get('name', '')
                    parts.append(f'"{lname}"')
                else:
                    label_el = container_el.find('Label')
                    if label_el is not None and label_el.text:
                        parts.append(label_el.text)

        # ── Animation ─────────────────────────────────────────────────────────
        elif ptype == 'Animation':
            anim_el = param_el.find('Animation')
            if anim_el is not None:
                aname = anim_el.get('name', '')
                parts.append(f'Animation: {aname}')

        # ── Field reference (Go to Field etc.) ────────────────────────────────
        elif ptype == 'FieldReference':
            field_ref = param_el.find('FieldReference')
            if field_ref is not None:
                fname = field_ref.get('name', '')
                to_ref = field_ref.find('TableOccurrenceReference')
                tname = to_ref.get('name', '') if to_ref is not None else ''
                ref = f'{tname}::{fname}' if tname else fname
                if ref:
                    parts.append(ref)

        # ── Object name ───────────────────────────────────────────────────────
        elif ptype == 'Object':
            name_el = param_el.find('Name')
            calc    = get_cdata(name_el) if name_el is not None else get_cdata(param_el)
            if calc:
                parts.append(f'Object Name: {calc}')
            rep_el = param_el.find('repetition')
            if rep_el is not None:
                rep = get_cdata(rep_el)
                if rep:
                    parts.append(f'Repetition: {rep}')

        # ── Text / calculation wrapper ─────────────────────────────────────────
        elif ptype == 'Text':
            calc = get_cdata(param_el)
            if calc:
                parts.append(calc)
            else:
                # Insert Text stores content in <Text value="..."> attribute
                text_el = param_el.find('Text')
                if text_el is not None:
                    raw = text_el.get('value', '')
                    if raw:
                        norm = ' '.join(raw.split())
                        if len(norm) > 100:
                            norm = norm[:100].rstrip() + '\u2026'
                        parts.append(f'"{norm}"')

        # Skip: Variable, Comment, Parameter (script param), Title, Message,
        # Button1/2/3 — these are handled by specific step renderers below.

    return ' ; '.join(parts)


# ---------------------------------------------------------------------------
# Step renderer
# ---------------------------------------------------------------------------

def render_step(step):
    """
    Render a single <Step> element to its Script Workspace line (no indentation).

    Returns:
        (line_text, (close_before, open_after))
        close_before: decrease indent BEFORE rendering this line
        open_after:   increase indent AFTER rendering this line
    """
    step_id  = int(step.get('id', 0))
    enabled  = step.get('enable', 'True') == 'True'
    name     = step.get('name', '')
    pfx      = '' if enabled else '// '

    entry    = CATALOG.get(step_id, {})
    hr_sig   = entry.get('hrSignature') or ''

    # ── COMMENT / BLANK LINE  (id=89) ─────────────────────────────────────────
    if step_id == 89:
        comment_el = step.find('.//Comment')
        text = ''
        if comment_el is not None:
            text = comment_el.get('value', '')
        if not enabled:
            return pfx + ('# ' + text if text else '# '), (False, False)
        return ('# ' + text) if text else '', (False, False)

    # ── IF  (id=68) ────────────────────────────────────────────────────────────
    if step_id == 68:
        calc_param = step.find('.//Parameter[@type="Calculation"]')
        condition  = get_cdata(calc_param)
        return f'{pfx}If [ {condition} ]', (False, True)

    # ── ELSE  (id=69) ──────────────────────────────────────────────────────────
    if step_id == 69:
        return f'{pfx}Else', (True, True)

    # ── ELSE IF  (id=125) ──────────────────────────────────────────────────────
    if step_id == 125:
        calc_param = step.find('.//Parameter[@type="Calculation"]')
        condition  = get_cdata(calc_param)
        return f'{pfx}Else If [ {condition} ]', (True, True)

    # ── END IF  (id=70) ────────────────────────────────────────────────────────
    if step_id == 70:
        return f'{pfx}End If', (True, False)

    # ── LOOP  (id=71) ──────────────────────────────────────────────────────────
    if step_id == 71:
        list_param = step.find('ParameterValues/Parameter[@type="List"]')
        flush = ''
        if list_param is not None:
            list_el = list_param.find('List')
            if list_el is not None and list_el.get('name') == 'Always':
                flush = ' [ Flush: Always ]'
        return f'{pfx}Loop{flush}', (False, True)

    # ── EXIT LOOP IF  (id=72) ──────────────────────────────────────────────────
    if step_id == 72:
        calc_param = step.find('.//Parameter[@type="Calculation"]')
        condition  = get_cdata(calc_param)
        return f'{pfx}Exit Loop If [ {condition} ]', (False, False)

    # ── END LOOP  (id=73) ──────────────────────────────────────────────────────
    if step_id == 73:
        return f'{pfx}End Loop', (True, False)

    # ── EXIT SCRIPT  (id=103) ──────────────────────────────────────────────────
    if step_id == 103:
        calc_param = step.find('.//Parameter[@type="Calculation"]')
        result     = get_cdata(calc_param)
        if result:
            return f'{pfx}Exit Script [ Text Result: {result} ]', (False, False)
        return f'{pfx}Exit Script [ Text Result:    ]', (False, False)

    # ── SET VARIABLE  (id=141) ─────────────────────────────────────────────────
    if step_id == 141:
        var_param = step.find('ParameterValues/Parameter[@type="Variable"]')
        if var_param is not None:
            name_el  = var_param.find('Name')
            var_name = name_el.get('value', '') if name_el is not None else ''
            value_el = var_param.find('value')
            expr     = get_cdata(value_el)
            return f'{pfx}Set Variable [ {var_name} ; Value: {expr} ]', (False, False)
        return f'{pfx}Set Variable [ ]', (False, False)

    # ── SHOW CUSTOM DIALOG  (id=87) ────────────────────────────────────────────
    if step_id == 87:
        title_param = step.find('ParameterValues/Parameter[@type="Title"]')
        msg_param   = step.find('ParameterValues/Parameter[@type="Message"]')
        title = get_cdata(title_param) if title_param is not None else '""'
        msg   = get_cdata(msg_param)   if msg_param   is not None else '""'
        MAX = 80
        if len(msg) > MAX:
            msg = msg[:MAX].rstrip() + '\u2026'
        parts = [title, msg]
        for fnum in ('Field1', 'Field2', 'Field3'):
            f_param = step.find(f'ParameterValues/Parameter[@type="{fnum}"]')
            if f_param is not None:
                var_el = f_param.find('.//Variable')
                if var_el is not None:
                    var_name = var_el.get('value', '')
                    if var_name:
                        parts.append(var_name)
        return f'{pfx}Show Custom Dialog [ {" ; ".join(parts)} ]', (False, False)

    # ── PERFORM SCRIPT  (id=1) ─────────────────────────────────────────────────
    if step_id == 1:
        list_param  = step.find('ParameterValues/Parameter[@type="List"]')
        param_outer = step.find('ParameterValues/Parameter[@type="Parameter"]')
        param_val   = ''
        if param_outer is not None:
            inner = param_outer.find('Parameter')
            if inner is not None:
                param_val = get_cdata(inner)
        param_str = f' Parameter: {param_val}' if param_val else ' Parameter:   '
        if list_param is not None:
            list_el  = list_param.find('List')
            if list_el is not None and list_el.get('name') == 'By name':
                expr = get_cdata(list_el)
                return (f'{pfx}Perform Script [ Specified: By name ; {expr} ;{param_str} ]'), (False, False)
            # From list (default)
            ref = list_param.find('.//ScriptReference')
            script_name = ref.get('name', '') if ref is not None else ''
            return (f'{pfx}Perform Script [ "{script_name}" ; Specified: From list ;{param_str} ]'), (False, False)
        return f'{pfx}Perform Script [ ]', (False, False)

    # ── GO TO RELATED RECORD  (id=74) ─────────────────────────────────────────
    if step_id == 74:
        related = step.find('ParameterValues/Parameter[@type="Related"]')
        if related is not None:
            to_ref     = related.find('TableOccurrenceReference')
            to_name    = to_ref.get('name', '') if to_ref is not None else ''
            layout_con = related.find('LayoutReferenceContainer')
            layout_ref = layout_con.find('LayoutReference') if layout_con is not None else None
            layout_name = layout_ref.get('name', '') if layout_ref is not None else ''
            win_ref    = related.find('WindowReference')
            win_style  = ''
            if win_ref is not None:
                style_el = win_ref.find('Style')
                if style_el is not None:
                    sname = style_el.get('name', '')
                    if sname in ('Card', 'Document', 'Floating Document'):
                        win_style = 'New window'
            parts = ['Show only related records']
            if to_name:
                parts.append(f'From table: "{to_name}"')
            if layout_name:
                parts.append(f'Using layout: "{layout_name}"')
            if win_style:
                parts.append(win_style)
            return f'{pfx}Go to Related Record [ {" ; ".join(parts)} ]', (False, False)
        return f'{pfx}Go to Related Record [ ]', (False, False)

    # ── ALL OTHER STEPS — catalog-driven generic renderer ─────────────────────
    step_name = name or entry.get('name', f'Step_{step_id}')
    inner     = _render_params(step)

    # Steps that have no params at all in the catalog render without brackets.
    no_params = not entry.get('params') and not step.find('ParameterValues')
    if no_params:
        return f'{pfx}{step_name}', (False, False)

    bracket = f'[ {inner} ]' if inner else '[]'
    return f'{pfx}{step_name} {bracket}', (False, False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def convert(xml_path):
    """Parse a SaXML file and print Script Workspace-format output."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f'ERROR: Could not parse XML: {e}', file=sys.stderr)
        sys.exit(1)

    root = tree.getroot()

    script_ref  = root.find('.//ScriptReference')
    script_name = script_ref.get('name', 'Unknown') if script_ref is not None else 'Unknown'

    object_list = root.find('.//ObjectList')
    if object_list is None:
        print('ERROR: No <ObjectList> found in XML.', file=sys.stderr)
        sys.exit(1)

    steps  = object_list.findall('Step')
    lines  = []
    indent = 0

    for step in steps:
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
