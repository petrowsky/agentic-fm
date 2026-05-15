#!/usr/bin/env python3
"""
fm_xml_to_snippet.py

Translate a FileMaker "Save As XML" script export (xml_parsed/scripts/ format)
into the fmxmlsnippet clipboard format used by FileMaker for script pasting.

Usage:
    python3 agent/scripts/fm_xml_to_snippet.py <input.xml> [output.xml]

Arguments:
    input.xml   Path to the Save-As-XML script file (from xml_parsed/scripts/)
    output.xml  Optional output path. Defaults to stdout.

Notes:
    - The Options bitmask on each step is intentionally ignored. Meaningful
      state (Restore, FlushType, NoInteract, etc.) is derived from the
      structured ParameterValues instead.
    - The hash attribute on each step is not included in output.
    - Perform Script steps referencing an external file emit a FileReference
      element without a UniversalPathList; a TODO comment marks the location.
      Fill in the correct path or global variable before pasting into FileMaker.
    - Step types not covered by this translator emit a self-closing Step and
      a TODO comment. A warning is also printed to stderr.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Indentation
# ---------------------------------------------------------------------------

S  = '  '       # step level (direct children of <fmxmlsnippet>)
L1 = '    '     # children of <Step>
L2 = '      '   # grandchildren of <Step>
L3 = '        ' # great-grandchildren (e.g. <Calculation> inside <Button>)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def escape_xml(text: str) -> str:
    """Escape &, <, > for XML text content."""
    if not text:
        return ''
    if '&' not in text and '<' not in text and '>' not in text:
        return text
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def escape_attr(text: str) -> str:
    """Escape for double-quoted XML attribute values."""
    return escape_xml(text).replace('"', '&quot;')


def cdata(text: str) -> str:
    """Wrap text in a CDATA section."""
    return f'<![CDATA[{text or ""}]]>'


def get_calc_text(element) -> str:
    """
    Extract calculation text from the xml_parsed nested Calculation structure:

        <outer-element>
            <Calculation datatype="N" position="N">
                <Calculation>
                    <Text><![CDATA[...]]></Text>
                </Calculation>
            </Calculation>
        </outer-element>

    Accepts any element that contains this pattern as a direct child.
    Returns the raw string (CDATA markers are already stripped by ET).
    """
    if element is None:
        return ''
    outer = element.find('Calculation')
    if outer is None:
        return ''
    inner = outer.find('Calculation')
    if inner is not None:
        t = inner.find('Text')
        if t is not None:
            return t.text or ''
    # Fallback: Text directly inside outer Calculation (unusual but safe)
    t = outer.find('Text')
    return (t.text or '') if t is not None else ''


def param_by_type(step, param_type: str):
    """Return the first <Parameter type="X"> element, or None."""
    for p in step.findall('ParameterValues/Parameter'):
        if p.get('type') == param_type:
            return p
    return None


def all_params(step):
    """Return all <Parameter> elements in document order."""
    return step.findall('ParameterValues/Parameter')


def step_attrs(step):
    """Return (enable, id) strings from a <Step> element."""
    return step.get('enable', 'True'), step.get('id', '0')


# ---------------------------------------------------------------------------
# Step translators
# Each returns a fully-formed string (may be multi-line) for one <Step>.
# ---------------------------------------------------------------------------

def tx_comment(step) -> str:
    enable, sid = step_attrs(step)
    p = param_by_type(step, 'Comment')
    text = ''
    if p is not None:
        c = p.find('Comment')
        if c is not None:
            text = c.get('value', '')
    if not text:
        return f'{S}<Step enable="{enable}" id="{sid}" name="# (comment)"/>'
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="# (comment)">\n'
        f'{L1}<Text>{escape_xml(text)}</Text>\n'
        f'{S}</Step>'
    )


def tx_allow_user_abort(step) -> str:
    enable, sid = step_attrs(step)
    p = param_by_type(step, 'Boolean')
    state = 'False'
    if p is not None:
        b = p.find('Boolean')
        if b is not None:
            state = b.get('value', 'False')
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Allow User Abort">\n'
        f'{L1}<Set state="{state}"/>\n'
        f'{S}</Step>'
    )


def tx_set_error_capture(step) -> str:
    enable, sid = step_attrs(step)
    p = param_by_type(step, 'Boolean')
    state = 'True'
    if p is not None:
        b = p.find('Boolean')
        if b is not None:
            state = b.get('value', 'True')
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Set Error Capture">\n'
        f'{L1}<Set state="{state}"/>\n'
        f'{S}</Step>'
    )


def tx_if_elseif(step) -> str:
    """Handles both 'If' and 'Else If' (identical structure)."""
    enable, sid = step_attrs(step)
    name = step.get('name', 'If')
    restore = 'False'
    calc_text = ''
    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'Collapsed':
                restore = b.get('value', 'False')
        elif ptype == 'Calculation':
            calc_text = get_calc_text(p)
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="{name}">\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{L1}<Calculation>{cdata(calc_text)}</Calculation>\n'
        f'{S}</Step>'
    )


def tx_else(step) -> str:
    enable, sid = step_attrs(step)
    restore = 'False'
    for p in all_params(step):
        b = p.find('Boolean')
        if b is not None and b.get('type') == 'Collapsed':
            restore = b.get('value', 'False')
            break
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Else">\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{S}</Step>'
    )


def tx_self_closing(step) -> str:
    """Self-closing steps with no children: End If, End Loop."""
    enable, sid = step_attrs(step)
    name = step.get('name', '')
    return f'{S}<Step enable="{enable}" id="{sid}" name="{name}"/>'


def tx_exit_script(step) -> str:
    enable, sid = step_attrs(step)
    # Exit Script may optionally carry a script result calculation
    p = param_by_type(step, 'Calculation')
    if p is not None:
        calc = get_calc_text(p)
        if calc:
            return (
                f'{S}<Step enable="{enable}" id="{sid}" name="Exit Script">\n'
                f'{L1}<Calculation>{cdata(calc)}</Calculation>\n'
                f'{S}</Step>'
            )
    return f'{S}<Step enable="{enable}" id="{sid}" name="Exit Script"/>'


def tx_loop(step) -> str:
    enable, sid = step_attrs(step)
    restore = 'False'
    flush = 'Always'
    for p in all_params(step):
        b = p.find('Boolean')
        if b is not None and b.get('type') == 'Collapsed':
            restore = b.get('value', 'False')
        lst = p.find('List')
        if lst is not None:
            flush = lst.get('name', 'Always')
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Loop">\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{L1}<FlushType value="{flush}"/>\n'
        f'{S}</Step>'
    )


def tx_exit_loop_if(step) -> str:
    enable, sid = step_attrs(step)
    p = param_by_type(step, 'Calculation')
    calc = get_calc_text(p) if p is not None else ''
    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Exit Loop If">\n'
        f'{L1}<Calculation>{cdata(calc)}</Calculation>\n'
        f'{S}</Step>'
    )


def tx_set_variable(step) -> str:
    enable, sid = step_attrs(step)
    var_name = ''
    value_calc = None
    rep_calc = '1'

    p = param_by_type(step, 'Variable')
    if p is not None:
        n = p.find('Name')
        if n is not None:
            var_name = n.get('value', '')
        v = p.find('value')
        if v is not None:
            value_calc = get_calc_text(v)   # '' when empty <value/>
        r = p.find('repetition')
        if r is not None:
            rep_calc = get_calc_text(r) or '1'

    parts = [f'{S}<Step enable="{enable}" id="{sid}" name="Set Variable">']
    if value_calc:
        parts += [
            f'{L1}<Value>',
            f'{L2}<Calculation>{cdata(value_calc)}</Calculation>',
            f'{L1}</Value>',
        ]
    parts += [
        f'{L1}<Repetition>',
        f'{L2}<Calculation>{cdata(rep_calc)}</Calculation>',
        f'{L1}</Repetition>',
        f'{L1}<Name>{escape_xml(var_name)}</Name>',
        f'{S}</Step>',
    ]
    return '\n'.join(parts)


def tx_perform_script(step) -> str:
    enable, sid = step_attrs(step)
    script_id = '0'
    script_name = ''
    file_ref_id = None
    file_ref_name = None
    param_calc = ''

    list_p = param_by_type(step, 'List')
    if list_p is not None:
        lst = list_p.find('List')
        if lst is not None:
            ds = lst.find('DataSourceReference')
            if ds is not None:
                file_ref_id = ds.get('id', '0')
                file_ref_name = ds.get('name', '')
            sr = lst.find('ScriptReference')
            if sr is not None:
                script_id = sr.get('id', '0')
                script_name = sr.get('name', '')

    param_p = param_by_type(step, 'Parameter')
    if param_p is not None:
        inner = param_p.find('Parameter')
        if inner is not None:
            param_calc = get_calc_text(inner)

    parts = [f'{S}<Step enable="{enable}" id="{sid}" name="Perform Script">']
    if param_calc:
        parts.append(f'{L1}<Calculation>{cdata(param_calc)}</Calculation>')
    if file_ref_id is not None:
        parts.append(
            f'{L1}<FileReference id="{file_ref_id}" name="{escape_attr(file_ref_name)}">'
        )
        parts.append(
            f'{L2}<!-- TODO: insert UniversalPathList path for file reference "{escape_xml(file_ref_name)}" -->'
        )
        parts.append(f'{L1}</FileReference>')
    if script_name:
        parts.append(
            f'{L1}<Script id="{script_id}" name="{escape_attr(script_name)}"/>'
        )
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_perform_script_on_server(step) -> str:
    """
    Translate Perform Script on Server.

    Two script-reference modes — both must be preserved:
    - "By name": <List name="By name"> wraps a <Calculation> giving the target script name
      (typically Get(ScriptName) for self-recursion). Emits <Calculated><Calculation>...</Calculated>.
    - "From list": <List name="From list"> contains <DataSourceReference> + <ScriptReference>.
      Emits <Script id="N" name="..."/> (and optional FileReference).

    Plus the parameter Calculation (wrapped in <Parameter><Parameter>... in SaXML, emitted
    as a top-level <Calculation> in fmxmlsnippet) and the Wait for completion boolean.

    The catalog-driven generic translator strips the script reference and parameter calc
    silently — this is a project-critical step for PSOS self-recursion (see
    docs/patterns/psos-self-recursion.md) so it needs a dedicated handler.
    """
    enable, sid = step_attrs(step)

    list_mode = None  # 'By name' or 'From list'
    script_name_calc = ''
    script_id = '0'
    script_name = ''
    file_ref_id = None
    file_ref_name = None
    param_calc = ''
    has_param_calc = False
    wait = 'True'

    list_p = param_by_type(step, 'List')
    if list_p is not None:
        lst = list_p.find('List')
        if lst is not None:
            list_mode = lst.get('name', '')
            # By name: <Calculation> direct child
            calc = lst.find('Calculation')
            if calc is not None:
                # Reuse the nested-Calculation extractor
                inner = calc.find('Calculation')
                if inner is not None:
                    t = inner.find('Text')
                    if t is not None:
                        script_name_calc = t.text or ''
            # From list: <DataSourceReference> + <ScriptReference>
            ds = lst.find('DataSourceReference')
            if ds is not None:
                file_ref_id = ds.get('id', '0')
                file_ref_name = ds.get('name', '')
            sr = lst.find('ScriptReference')
            if sr is not None:
                script_id = sr.get('id', '0')
                script_name = sr.get('name', '')

    param_p = param_by_type(step, 'Parameter')
    if param_p is not None:
        inner = param_p.find('Parameter')
        if inner is not None:
            # The inner <Parameter> wraps a <Calculation> directly (one fewer
            # level of nesting than get_calc_text expects).
            outer_calc = inner.find('Calculation')
            if outer_calc is not None:
                has_param_calc = True
                deeper = outer_calc.find('Calculation')
                if deeper is not None:
                    t = deeper.find('Text')
                    if t is not None:
                        param_calc = t.text or ''

    bool_p = param_by_type(step, 'Boolean')
    if bool_p is not None:
        b = bool_p.find('Boolean')
        if b is not None:
            wait = b.get('value', 'True')

    parts = [f'{S}<Step enable="{enable}" id="{sid}" name="Perform Script on Server">']
    # By name: emit <Calculated> wrapping a Calculation
    if list_mode == 'By name':
        parts.append(
            f'{L1}<Calculated><Calculation>{cdata(script_name_calc)}</Calculation></Calculated>'
        )
    parts.append(f'{L1}<WaitForCompletion state="{wait}"/>')
    if has_param_calc:
        parts.append(f'{L1}<Calculation>{cdata(param_calc)}</Calculation>')
    # From list: emit FileReference (if any) + Script
    if list_mode == 'From list':
        if file_ref_id is not None:
            parts.append(
                f'{L1}<FileReference id="{file_ref_id}" name="{escape_attr(file_ref_name)}">'
            )
            parts.append(
                f'{L2}<!-- TODO: insert UniversalPathList path for file reference "{escape_xml(file_ref_name)}" -->'
            )
            parts.append(f'{L1}</FileReference>')
        parts.append(
            f'{L1}<Script id="{script_id}" name="{escape_attr(script_name)}"/>'
        )
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_new_window(step) -> str:
    """
    Translate New Window.

    SaXML structure is a single <WindowReference> param containing Style, Name,
    LayoutReferenceContainer (with Label "original layout" for CurrentLayout, or
    a nested Layout for SelectedLayout), Bounds (height/width/top/left), and
    Options (Close/Minimize/Maximize/Resize/MenuBar/Toolbar/DimParentWindow +
    numeric Styles bitmask).

    The catalog-driven generic translator strips all of this silently — every
    transactional workflow in this codebase relies on a properly-configured
    New Window for the $windowNameForTransaction isolation-window pattern, so
    this needs a dedicated handler. See docs/patterns/fm-fmxmlsnippet-gotchas.md
    section "Go to Layout requires LayoutDestination" for the related enum
    discussion — New Window uses the same LayoutDestination enum.
    """
    enable, sid = step_attrs(step)

    style = 'Document'
    name_calc = ''
    layout_dest = 'CurrentLayout'
    layout_id = None
    layout_name = ''
    height_calc = ''
    width_calc = ''
    top_calc = ''
    left_calc = ''
    # Default options (FM's New Window defaults)
    opt = {
        'Close': 'Yes',
        'Minimize': 'Yes',
        'Maximize': 'Yes',
        'Resize': 'Yes',
        'MenuBar': 'Yes',
        'Toolbars': 'No',
        'DimParentWindow': 'No',
    }
    styles_bitmask = '1074202114'  # default Document-style bitmask

    wr_p = param_by_type(step, 'WindowReference')
    if wr_p is not None:
        wr = wr_p.find('WindowReference')
        if wr is not None:
            # Style
            st_el = wr.find('Style')
            if st_el is not None:
                style = st_el.get('name', 'Document')
            # Name calculation
            name_el = wr.find('Name')
            if name_el is not None:
                name_calc = get_calc_text(name_el)
            # LayoutReferenceContainer
            lrc = wr.find('LayoutReferenceContainer')
            if lrc is not None:
                label = lrc.find('Label')
                label_txt = label.text if label is not None else ''
                # SaXML uses "original layout" Label for the CurrentLayout (HR
                # default) enum; a nested <Layout> indicates SelectedLayout.
                nested_layout = lrc.find('Layout')
                if nested_layout is not None:
                    layout_dest = 'SelectedLayout'
                    layout_id = nested_layout.get('id', '0')
                    layout_name = nested_layout.get('name', '')
                elif label_txt and 'original' in label_txt.lower():
                    layout_dest = 'CurrentLayout'
                # else: keep default CurrentLayout
            # Bounds
            bounds = wr.find('Bounds')
            if bounds is not None:
                for tag, target in (('height', 'height_calc'),
                                    ('width', 'width_calc'),
                                    ('top', 'top_calc'),
                                    ('left', 'left_calc')):
                    el = bounds.find(tag)
                    if el is not None:
                        val = get_calc_text(el)
                        if target == 'height_calc':
                            height_calc = val
                        elif target == 'width_calc':
                            width_calc = val
                        elif target == 'top_calc':
                            top_calc = val
                        elif target == 'left_calc':
                            left_calc = val
            # Options
            options = wr.find('Options')
            if options is not None:
                styles_bitmask = options.get('value', styles_bitmask)
                # SaXML uses tag text "True"/"False"; fmxmlsnippet wants Yes/No.
                tag_to_attr = [
                    ('Close', 'Close'),
                    ('Minimize', 'Minimize'),
                    ('Maximize', 'Maximize'),
                    ('Resize', 'Resize'),
                    ('MenuBar', 'MenuBar'),
                    ('Toolbar', 'Toolbars'),         # note SaXML "Toolbar" → fmxmlsnippet "Toolbars"
                    ('DimParentWindow', 'DimParentWindow'),
                ]
                for sa_tag, attr in tag_to_attr:
                    el = options.find(sa_tag)
                    if el is not None and el.text:
                        opt[attr] = 'Yes' if el.text.strip().lower() == 'true' else 'No'

    parts = [f'{S}<Step enable="{enable}" id="{sid}" name="New Window">']
    parts.append(f'{L1}<LayoutDestination value="{layout_dest}"/>')
    # Name (always emit — empty calc is fine if no name supplied)
    parts.append(f'{L1}<Name>')
    parts.append(f'{L2}<Calculation>{cdata(name_calc)}</Calculation>')
    parts.append(f'{L1}</Name>')
    # Height / Width — emit when present
    if height_calc or width_calc:
        parts.append(f'{L1}<Height>')
        parts.append(f'{L2}<Calculation>{cdata(height_calc)}</Calculation>')
        parts.append(f'{L1}</Height>')
        parts.append(f'{L1}<Width>')
        parts.append(f'{L2}<Calculation>{cdata(width_calc)}</Calculation>')
        parts.append(f'{L1}</Width>')
    # Top / Left — emit only if non-empty (rare)
    if top_calc:
        parts.append(f'{L1}<DistanceFromTop>')
        parts.append(f'{L2}<Calculation>{cdata(top_calc)}</Calculation>')
        parts.append(f'{L1}</DistanceFromTop>')
    if left_calc:
        parts.append(f'{L1}<DistanceFromLeft>')
        parts.append(f'{L2}<Calculation>{cdata(left_calc)}</Calculation>')
        parts.append(f'{L1}</DistanceFromLeft>')
    # NewWndStyles
    parts.append(
        f'{L1}<NewWndStyles DimParentWindow="{opt["DimParentWindow"]}" '
        f'Toolbars="{opt["Toolbars"]}" MenuBar="{opt["MenuBar"]}" '
        f'Style="{style}" Close="{opt["Close"]}" Minimize="{opt["Minimize"]}" '
        f'Maximize="{opt["Maximize"]}" Resize="{opt["Resize"]}" '
        f'Styles="{styles_bitmask}"/>'
    )
    # Layout element only for SelectedLayout per the 2026-05-14 gotcha note
    if layout_dest == 'SelectedLayout' and layout_id is not None:
        parts.append(
            f'{L1}<Layout id="{layout_id}" name="{escape_attr(layout_name)}"/>'
        )
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_show_custom_dialog(step) -> str:
    enable, sid = step_attrs(step)
    title_calc = ''
    message_calc = ''
    buttons = []  # list of (label_str, commit_state_str)

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Title':
            title_calc = get_calc_text(p)
        elif ptype == 'Message':
            message_calc = get_calc_text(p)
        elif ptype.startswith('Button'):
            label = p.get('value', '')
            commit = 'False'
            b = p.find('Boolean')
            if b is not None:
                commit = b.get('value', 'False')
            buttons.append((label, commit))

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Show Custom Dialog">',
        f'{L1}<Title>',
        f'{L2}<Calculation>{cdata(title_calc)}</Calculation>',
        f'{L1}</Title>',
        f'{L1}<Message>',
        f'{L2}<Calculation>{cdata(message_calc)}</Calculation>',
        f'{L1}</Message>',
        f'{L1}<Buttons>',
    ]
    for label, commit in buttons:
        if label:
            # Button labels are stored as raw text in xml_parsed but are
            # FileMaker calculation expressions in fmxmlsnippet, so wrap in quotes.
            parts += [
                f'{L2}<Button CommitState="{commit}">',
                f'{L3}<Calculation>{cdata(chr(34) + label + chr(34))}</Calculation>',
                f'{L2}</Button>',
            ]
        else:
            parts.append(f'{L2}<Button CommitState="{commit}"/>')
    parts += [f'{L1}</Buttons>', f'{S}</Step>']
    return '\n'.join(parts)


def tx_set_field(step) -> str:
    enable, sid = step_attrs(step)
    calc = ''
    field_table = ''
    field_id = '0'
    field_name = ''

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Calculation':
            calc = get_calc_text(p)
        elif ptype == 'FieldReference':
            fr = p.find('FieldReference')
            if fr is not None:
                field_id = fr.get('id', '0')
                field_name = fr.get('name', '')
                tor = fr.find('TableOccurrenceReference')
                if tor is not None:
                    field_table = tor.get('name', '')

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Set Field">\n'
        f'{L1}<Calculation>{cdata(calc)}</Calculation>\n'
        f'{L1}<Field table="{escape_attr(field_table)}" id="{field_id}"'
        f' name="{escape_attr(field_name)}"/>\n'
        f'{S}</Step>'
    )


def tx_commit(step) -> str:
    enable, sid = step_attrs(step)
    no_interact = 'True'   # default: suppress dialog
    skip_val    = 'False'
    force       = 'False'

    for p in all_params(step):
        b = p.find('Boolean')
        if b is None:
            continue
        btype = b.get('type', '')
        val   = b.get('value', 'False')
        if btype == 'With dialog':
            # "With dialog = False" → NoInteract = True (inverted)
            no_interact = 'False' if val == 'True' else 'True'
        elif btype == 'Skip data entry validation':
            skip_val = val
        elif btype == 'Force Commit':
            force = val

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Commit Records/Requests">\n'
        f'{L1}<NoInteract state="{no_interact}"/>\n'
        f'{L1}<Option state="{skip_val}"/>\n'
        f'{L1}<ESSForceCommit state="{force}"/>\n'
        f'{S}</Step>'
    )


def tx_refresh_object(step) -> str:
    enable, sid = step_attrs(step)
    obj_name = ''
    rep = '1'

    p = param_by_type(step, 'Object')
    if p is not None:
        n = p.find('Name')
        if n is not None:
            obj_name = get_calc_text(n)
        r = p.find('repetition')
        if r is not None:
            rep = get_calc_text(r) or '1'

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Refresh Object">\n'
        f'{L1}<ObjectName>\n'
        f'{L2}<Calculation>{cdata(obj_name)}</Calculation>\n'
        f'{L1}</ObjectName>\n'
        f'{L1}<Repetition>\n'
        f'{L2}<Calculation>{cdata(rep)}</Calculation>\n'
        f'{L1}</Repetition>\n'
        f'{S}</Step>'
    )


def _escape_text_cr(s: str) -> str:
    """Escape XML special chars and convert bare CR (\\r) to &#xD; entity."""
    return escape_xml(s).replace('\r', '&#xD;')


def _target_field_xml(p) -> str:
    """
    Given a <Parameter type="Target"> element, return the fmxmlsnippet
    <Field> element string (with L1 indent), or '' if no target found.
    """
    if p is None:
        return ''
    v = p.find('Variable')
    if v is not None:
        return f'{L1}<Field>{escape_xml(v.get("value", ""))}</Field>'
    fr = p.find('FieldReference')
    if fr is not None:
        fid   = fr.get('id', '0')
        fname = fr.get('name', '')
        tor   = fr.find('TableOccurrenceReference')
        ftbl  = tor.get('name', '') if tor is not None else ''
        return f'{L1}<Field table="{escape_attr(ftbl)}" id="{fid}" name="{escape_attr(fname)}"/>'
    return ''


def tx_pause_resume(step) -> str:
    enable, sid = step_attrs(step)
    pause_time = 'Indefinitely'
    calc_text  = ''

    p = param_by_type(step, 'Options')
    if p is not None:
        opts = p.find('Options')
        if opts is not None:
            otype = opts.get('type', 'Indefinitely')
            if 'Duration' in otype or 'second' in otype.lower():
                pause_time = 'ForDuration'
                calc_text  = get_calc_text(opts)
            # else: Indefinitely

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Pause/Resume Script">',
        f'{L1}<PauseTime value="{pause_time}"/>',
    ]
    if pause_time == 'ForDuration':
        parts.append(f'{L1}<Calculation>{cdata(calc_text)}</Calculation>')
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_go_to_layout(step) -> str:
    enable, sid  = step_attrs(step)
    layout_dest  = 'OriginalLayout'
    layout_id    = '0'
    layout_name  = ''
    has_layout   = False
    animation    = ''

    p = param_by_type(step, 'LayoutReferenceContainer')
    if p is not None:
        lrc = p.find('LayoutReferenceContainer')
        if lrc is not None:
            lr = lrc.find('LayoutReference')
            if lr is not None:
                layout_dest = 'SelectedLayout'
                layout_id   = lr.get('id', '0')
                layout_name = lr.get('name', '')
                has_layout  = True
            else:
                label_el   = lrc.find('Label')
                label_text = (label_el.text or '').strip() if label_el is not None else ''
                layout_dest = 'OriginalLayout' if label_text == 'original layout' else 'SelectedLayout'

    anim_p = param_by_type(step, 'Animation')
    if anim_p is not None:
        anim = anim_p.find('Animation')
        if anim is not None and anim.get('name', 'None') != 'None':
            animation = anim.get('name', '').replace(' ', '')

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Go to Layout">',
        f'{L1}<LayoutDestination value="{layout_dest}"/>',
    ]
    if has_layout:
        parts.append(f'{L1}<Layout id="{layout_id}" name="{escape_attr(layout_name)}"/>')
    if animation:
        parts.append(f'{L1}<Animation value="{animation}"/>')
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_set_web_viewer(step) -> str:
    enable, sid = step_attrs(step)
    obj_name    = ''
    action      = 'GoToURL'
    url_calc    = ''

    # Map xml_parsed List value → fmxmlsnippet Action string
    _SWV_ACTION = {'1': 'Reset', '2': 'Reload', '3': 'GoBack', '4': 'GoForward', '5': 'GoToURL'}

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Calculation':
            obj_name = get_calc_text(p)
        elif ptype == 'action':
            lst = p.find('List')
            if lst is not None:
                action   = _SWV_ACTION.get(lst.get('value', '5'), 'GoToURL')
                url_calc = get_calc_text(lst)   # empty for non-GoToURL actions

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Set Web Viewer">',
        f'{L1}<Action value="{action}"/>',
        f'{L1}<ObjectName>',
        f'{L2}<Calculation>{cdata(obj_name)}</Calculation>',
        f'{L1}</ObjectName>',
    ]
    if url_calc:
        parts += [
            f'{L1}<URL custom="False">',
            f'{L2}<Calculation>{cdata(url_calc)}</Calculation>',
            f'{L1}</URL>',
        ]
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_get_file_size(step) -> str:
    """Same structure as Get File Exists."""
    enable, sid = step_attrs(step)
    path_text = ''
    target_p  = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'UniversalPathList':
            upl = p.find('UniversalPathList')
            if upl is not None:
                loc = upl.find('ObjectList/Location')
                if loc is not None:
                    path_text = loc.text or ''
        elif ptype == 'Target':
            target_p = p

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Get File Size">',
        f'{L1}<UniversalPathList>{escape_xml(path_text)}</UniversalPathList>',
        f'{L1}<Text/>',
    ]
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_insert_file(step) -> str:
    enable, sid = step_attrs(step)
    path_text = ''
    target_p  = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'UniversalPathList':
            upl = p.find('UniversalPathList')
            if upl is not None:
                loc = upl.find('ObjectList/Location')
                if loc is not None:
                    path_text = loc.text or ''
        elif ptype == 'Target':
            target_p = p

    parts = [f'{S}<Step enable="{enable}" id="{sid}" name="Insert File">']
    if path_text:
        parts.append(f'{L1}<UniversalPathList type="Embedded">{escape_xml(path_text)}</UniversalPathList>')
    parts.append(f'{L1}<Text/>')
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    # DialogOptions: suppress dialog when path is provided; use neutral UserChoice defaults
    dialog_enable = 'False' if path_text else 'True'
    parts += [
        f'{L1}<DialogOptions asFile="True" enable="{dialog_enable}">',
        f'{L2}<Storage type="UserChoice"/>',
        f'{L2}<Compress type="UserChoice"/>',
        f'{L2}<FilterList/>',
        f'{L1}</DialogOptions>',
        f'{S}</Step>',
    ]
    return '\n'.join(parts)


def tx_perform_js_in_web_viewer(step) -> str:
    enable, sid  = step_attrs(step)
    obj_name     = ''
    func_name    = ''
    params_calcs = []   # list of calc strings, in position order

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Name':
            obj_name  = get_calc_text(p)
        elif ptype == 'FunctionRef':
            func_name = get_calc_text(p)
        elif ptype == 'Parameter':
            # Multiple <Calculation> children, each is one JS parameter
            for calc_outer in p.findall('Calculation'):
                inner = calc_outer.find('Calculation')
                if inner is not None:
                    t = inner.find('Text')
                    if t is not None:
                        params_calcs.append(t.text or '')

    count = len(params_calcs)
    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Perform JavaScript in Web Viewer">',
        f'{L1}<ObjectName>',
        f'{L2}<Calculation>{cdata(obj_name)}</Calculation>',
        f'{L1}</ObjectName>',
        f'{L1}<FunctionName>',
        f'{L2}<Calculation>{cdata(func_name)}</Calculation>',
        f'{L1}</FunctionName>',
        f'{L1}<Parameters Count="{count}">',
    ]
    for pc in params_calcs:
        parts += [f'{L2}<P>', f'{L3}<Calculation>{cdata(pc)}</Calculation>', f'{L2}</P>']
    parts += [f'{L1}</Parameters>', f'{S}</Step>']
    return '\n'.join(parts)


def tx_create_data_file(step) -> str:
    enable, sid    = step_attrs(step)
    create_dirs    = 'True'
    path_text      = ''

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'UniversalPathList':
            upl = p.find('UniversalPathList')
            if upl is not None:
                loc = upl.find('ObjectList/Location')
                if loc is not None:
                    path_text = loc.text or ''
        elif ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'Create folders':
                create_dirs = b.get('value', 'True')

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Create Data File">\n'
        f'{L1}<CreateDirectories state="{create_dirs}"/>\n'
        f'{L1}<UniversalPathList>{escape_xml(path_text)}</UniversalPathList>\n'
        f'{S}</Step>'
    )


def tx_open_data_file(step) -> str:
    """Same UPL+Target pattern as Get File Exists."""
    enable, sid = step_attrs(step)
    path_text = ''
    target_p  = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'UniversalPathList':
            upl = p.find('UniversalPathList')
            if upl is not None:
                loc = upl.find('ObjectList/Location')
                if loc is not None:
                    path_text = loc.text or ''
        elif ptype == 'Target':
            target_p = p

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Open Data File">',
        f'{L1}<UniversalPathList>{escape_xml(path_text)}</UniversalPathList>',
        f'{L1}<Text/>',
    ]
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_write_to_data_file(step) -> str:
    enable, sid    = step_attrs(step)
    append_lf      = 'True'
    data_src_type  = '2'   # UTF-8 default
    file_id_calc   = ''
    target_p       = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'id':
            file_id_calc = get_calc_text(p)
        elif ptype == 'Target':
            target_p = p
        elif ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'Append line feed':
                append_lf = b.get('value', 'True')

    # Encoding element is a sibling of <Parameter>, not wrapped in Parameter
    enc = step.find('ParameterValues/Encoding')
    if enc is None:
        enc = step.find('.//Encoding')
    if enc is not None:
        data_src_type = enc.get('type', '2')

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Write to Data File">',
        f'{L1}<AppendLineFeed state="{append_lf}"/>',
        f'{L1}<DataSourceType value="{data_src_type}"/>',
        f'{L1}<Calculation>{cdata(file_id_calc)}</Calculation>',
        f'{L1}<Text/>',
    ]
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_close_data_file(step) -> str:
    enable, sid  = step_attrs(step)
    file_id_calc = ''

    p = param_by_type(step, 'id')
    if p is not None:
        file_id_calc = get_calc_text(p)

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Close Data File">\n'
        f'{L1}<Calculation>{cdata(file_id_calc)}</Calculation>\n'
        f'{S}</Step>'
    )


def tx_enter_find_mode(step) -> str:
    enable, sid = step_attrs(step)
    pause   = 'False'
    restore = 'False'

    for p in all_params(step):
        b = p.find('Boolean')
        if b is None:
            continue
        btype = b.get('type', '')
        if btype == 'Pause':
            pause = b.get('value', 'False')
        elif btype == 'Collapsed':
            restore = b.get('value', 'False')

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Enter Find Mode">\n'
        f'{L1}<Pause state="{pause}"/>\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{S}</Step>'
    )


def tx_perform_find(step) -> str:
    enable, sid = step_attrs(step)
    # Restore is "True" only when stored find requests exist in xml_parsed.
    # An empty step (no ParameterValues) means no stored requests → "False".
    restore = 'False'
    pv = step.find('ParameterValues')
    if pv is not None:
        for p in pv.findall('Parameter'):
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'Collapsed':
                restore = b.get('value', 'False')

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Perform Find">\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{S}</Step>'
    )


def tx_constrain_found_set(step) -> str:
    enable, sid = step_attrs(step)
    option  = 'False'   # Find without indexes
    restore = 'False'

    for p in all_params(step):
        b = p.find('Boolean')
        if b is None:
            continue
        btype = b.get('type', '')
        if btype == 'Find without indexes':
            option = b.get('value', 'False')

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Constrain Found Set">\n'
        f'{L1}<Option state="{option}"/>\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{S}</Step>'
    )


def tx_extend_found_set(step) -> str:
    enable, sid = step_attrs(step)
    restore = 'False'

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Extend Found Set">\n'
        f'{L1}<Restore state="{restore}"/>\n'
        f'{S}</Step>'
    )


def tx_set_field_by_name(step) -> str:
    enable, sid      = step_attrs(step)
    result_calc      = ''
    target_name_calc = ''

    for p in all_params(step):
        if p.get('type') != 'Calculation':
            continue
        outer = p.find('Calculation')
        if outer is None:
            continue
        pos  = outer.get('position', '0')
        text = get_calc_text(p)
        if pos == '0':
            result_calc = text
        elif pos == '1':
            target_name_calc = text

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Set Field By Name">\n'
        f'{L1}<Result>\n'
        f'{L2}<Calculation>{cdata(result_calc)}</Calculation>\n'
        f'{L1}</Result>\n'
        f'{L1}<TargetName>\n'
        f'{L2}<Calculation>{cdata(target_name_calc)}</Calculation>\n'
        f'{L1}</TargetName>\n'
        f'{S}</Step>'
    )


def tx_delete_file(step) -> str:
    enable, sid = step_attrs(step)
    path_text   = ''

    p = param_by_type(step, 'UniversalPathList')
    if p is not None:
        upl = p.find('UniversalPathList')
        if upl is not None:
            loc = upl.find('ObjectList/Location')
            if loc is not None:
                path_text = loc.text or ''

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Delete File">\n'
        f'{L1}<UniversalPathList>{escape_xml(path_text)}</UniversalPathList>\n'
        f'{S}</Step>'
    )


def tx_get_file_exists(step) -> str:
    enable, sid = step_attrs(step)
    path_text = ''
    target_p  = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'UniversalPathList':
            upl = p.find('UniversalPathList')
            if upl is not None:
                loc = upl.find('ObjectList/Location')
                if loc is not None:
                    path_text = loc.text or ''
        elif ptype == 'Target':
            target_p = p

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Get File Exists">',
        f'{L1}<UniversalPathList>{escape_xml(path_text)}</UniversalPathList>',
        f'{L1}<Text/>',
    ]
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_set_layout_object_animation(step) -> str:
    enable, sid = step_attrs(step)
    state = 'True'

    p = param_by_type(step, 'Boolean')
    if p is not None:
        b = p.find('Boolean')
        if b is not None:
            state = b.get('value', 'True')

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Set Layout Object Animation">\n'
        f'{L1}<Set state="{state}"/>\n'
        f'{S}</Step>'
    )


def tx_refresh_portal(step) -> str:
    enable, sid = step_attrs(step)
    obj_name = ''

    p = param_by_type(step, 'Object')
    if p is not None:
        n = p.find('Name')
        if n is not None:
            obj_name = get_calc_text(n)

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Refresh Portal">\n'
        f'{L1}<ObjectName>\n'
        f'{L2}<Calculation>{cdata(obj_name)}</Calculation>\n'
        f'{L1}</ObjectName>\n'
        f'{S}</Step>'
    )


def tx_insert_calculated_result(step) -> str:
    enable, sid = step_attrs(step)
    select_all = 'True'
    calc_text  = ''
    target_p   = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'Select':
                select_all = b.get('value', 'True')
        elif ptype == 'Target':
            target_p = p
        elif ptype == 'Calculation':
            calc_text = get_calc_text(p)

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Insert Calculated Result">',
        f'{L1}<SelectAll state="{select_all}"/>',
        f'{L1}<Calculation>{cdata(calc_text)}</Calculation>',
        f'{L1}<Text/>',
    ]
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_insert_text(step) -> str:
    enable, sid = step_attrs(step)
    select_all = 'True'
    text_value = ''
    target_p   = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'Select':
                select_all = b.get('value', 'True')
        elif ptype == 'Target':
            target_p = p
        elif ptype == 'Text':
            t = p.find('Text')
            if t is not None:
                text_value = t.get('value', '')

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Insert Text">',
        f'{L1}<SelectAll state="{select_all}"/>',
        f'{L1}<Text>{_escape_text_cr(text_value)}</Text>',
    ]
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_close_window(step) -> str:
    enable, sid = step_attrs(step)
    limit_to_file = 'True'
    window_by     = 'ByName'
    name_calc     = ''

    p = param_by_type(step, 'WindowReference')
    if p is not None:
        wr  = p.find('WindowReference')
        sel = wr.find('Select') if wr is not None else None
        if sel is not None:
            sel_type = sel.get('type', 'Calculated')
            if sel_type.lower() == 'current':
                window_by = 'Current'
            else:
                window_by = 'ByName'
            name_el = sel.find('Name')
            if name_el is not None:
                limit_to_file = name_el.get('current', 'True')
                name_calc     = get_calc_text(name_el)

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Close Window">',
        f'{L1}<LimitToWindowsOfCurrentFile state="{limit_to_file}"/>',
        f'{L1}<Window value="{window_by}"/>',
    ]
    if window_by == 'ByName':
        parts += [
            f'{L1}<Name>',
            f'{L2}<Calculation>{cdata(name_calc)}</Calculation>',
            f'{L1}</Name>',
        ]
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_sort_records(step) -> str:
    enable, sid   = step_attrs(step)
    no_interact   = 'True'
    restore_state = 'True'
    maintain      = 'True'
    sl_value      = 'True'
    sorts         = []   # list of (sort_type, field_table, field_id, field_name)

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'With dialog':
                val = b.get('value', 'False')
                no_interact = 'False' if val == 'True' else 'True'
        elif ptype == 'Restore':
            r = p.find('Restore')
            if r is not None:
                restore_state = r.get('value', 'True')
        elif ptype == 'SortSpecification':
            ss = p.find('SortSpecification')
            if ss is not None:
                sl_value = ss.get('value', 'True')
                maintain = ss.get('maintain', 'True')
                sl = ss.find('SortList')
                if sl is not None:
                    for sort_el in sl.findall('Sort'):
                        stype = sort_el.get('type', 'Ascending')
                        fr = sort_el.find('PrimaryField/FieldReference')
                        fid   = '0'
                        fname = ''
                        ftbl  = ''
                        if fr is not None:
                            fid   = fr.get('id', '0')
                            fname = fr.get('name', '')
                            tor   = fr.find('TableOccurrenceReference')
                            if tor is not None:
                                ftbl = tor.get('name', '')
                        sorts.append((stype, ftbl, fid, fname))

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Sort Records">',
        f'{L1}<NoInteract state="{no_interact}"/>',
        f'{L1}<Restore state="{restore_state}"/>',
        f'{L1}<SortList Maintain="{maintain}" value="{sl_value}">',
    ]
    for stype, ftbl, fid, fname in sorts:
        parts += [
            f'{L2}<Sort type="{stype}">',
            f'{L3}<PrimaryField>',
            f'          <Field table="{escape_attr(ftbl)}" id="{fid}" name="{escape_attr(fname)}"/>',
            f'{L3}</PrimaryField>',
            f'{L2}</Sort>',
        ]
    parts += [f'{L1}</SortList>', f'{S}</Step>']
    return '\n'.join(parts)


def tx_replace_field_contents(step) -> str:
    enable, sid   = step_attrs(step)
    no_interact   = 'True'
    restore_state = 'False'
    with_value    = 'Calculation'
    calc_text     = ''
    # SerialNumbers attrs — always emitted (FileMaker includes even in Calculation mode)
    perform_auto  = 'False'
    update_entry  = 'False'
    use_entry     = 'True'
    field_table   = ''
    field_id      = '0'
    field_name    = ''

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None and b.get('type') == 'With dialog':
                val = b.get('value', 'False')
                no_interact = 'False' if val == 'True' else 'True'
        elif ptype == 'Restore':
            r = p.find('Restore')
            if r is not None:
                restore_state = r.get('value', 'False')
        elif ptype == 'FieldReference':
            fr = p.find('FieldReference')
            if fr is not None:
                field_id   = fr.get('id', '0')
                field_name = fr.get('name', '')
                tor = fr.find('TableOccurrenceReference')
                if tor is not None:
                    field_table = tor.get('name', '')
        elif ptype == 'replace':
            lst = p.find('List')
            if lst is not None:
                list_name = lst.get('name', '').lower()
                if 'calculation' in list_name:
                    with_value = 'Calculation'
                elif 'serial' in list_name:
                    with_value = 'SerialNumbers'
                else:
                    with_value = 'CurrentContents'
                calc_text = get_calc_text(lst)
                b = lst.find('Boolean')
                if b is not None and b.get('type') == 'Skip auto-enter options':
                    perform_auto = b.get('value', 'False')

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Replace Field Contents">',
        f'{L1}<NoInteract state="{no_interact}"/>',
        f'{L1}<Restore state="{restore_state}"/>',
        f'{L1}<With value="{with_value}"/>',
    ]
    if with_value == 'Calculation':
        parts.append(f'{L1}<Calculation>{cdata(calc_text)}</Calculation>')
    parts += [
        f'{L1}<SerialNumbers PerformAutoEnter="{perform_auto}" UpdateEntryOptions="{update_entry}" UseEntryOptions="{use_entry}"/>',
        f'{L1}<Field table="{escape_attr(field_table)}" id="{field_id}" name="{escape_attr(field_name)}"/>',
        f'{S}</Step>',
    ]
    return '\n'.join(parts)


def tx_open_url(step) -> str:
    enable, sid = step_attrs(step)
    no_interact = 'True'
    option      = 'False'  # external browser (FileMaker Go only)
    url_calc    = ''

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None:
                btype = b.get('type', '')
                val   = b.get('value', 'False')
                if btype == 'In external browser':
                    option = val
                elif btype == 'With dialog':
                    no_interact = 'False' if val == 'True' else 'True'
        elif ptype == 'URL':
            url_el = p.find('URL')
            if url_el is not None:
                url_calc = get_calc_text(url_el)

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Open URL">\n'
        f'{L1}<NoInteract state="{no_interact}"/>\n'
        f'{L1}<Option state="{option}"/>\n'
        f'{L1}<Calculation>{cdata(url_calc)}</Calculation>\n'
        f'{S}</Step>'
    )


def tx_go_to_object(step) -> str:
    """Go to Object — identical structure to Refresh Object."""
    enable, sid = step_attrs(step)
    obj_name = ''
    rep = '1'

    p = param_by_type(step, 'Object')
    if p is not None:
        n = p.find('Name')
        if n is not None:
            obj_name = get_calc_text(n)
        r = p.find('repetition')
        if r is not None:
            rep = get_calc_text(r) or '1'

    return (
        f'{S}<Step enable="{enable}" id="{sid}" name="Go to Object">\n'
        f'{L1}<ObjectName>\n'
        f'{L2}<Calculation>{cdata(obj_name)}</Calculation>\n'
        f'{L1}</ObjectName>\n'
        f'{L1}<Repetition>\n'
        f'{L2}<Calculation>{cdata(rep)}</Calculation>\n'
        f'{L1}</Repetition>\n'
        f'{S}</Step>'
    )


def tx_go_to_related_record(step) -> str:
    enable, sid = step_attrs(step)
    option          = 'False'
    match_all       = 'False'
    show_new_window = 'False'
    # Restore defaults to False per FM round-trip 2026-05-15. The
    # snippet_examples archival file lists True; that's wrong — pasting
    # a step with Restore=True produces "Show only related records" in HR
    # even when <Option state="False"/>. Apparently Restore controls a
    # different facet of the step than the snippet_example comment claims.
    restore         = 'False'
    layout_dest     = 'CurrentLayout'
    table_id        = '0'
    table_name      = ''
    layout_id       = '0'
    layout_name     = ''
    has_layout      = False
    animation       = 'None'
    has_animation   = False  # only emit <Animation> when source explicitly sets it

    p = param_by_type(step, 'Related')
    if p is not None:
        tor = p.find('TableOccurrenceReference')
        if tor is not None:
            table_id   = tor.get('id', '0')
            table_name = tor.get('name', '')

        lrc = p.find('LayoutReferenceContainer')
        if lrc is not None:
            label_el   = lrc.find('Label')
            label_text = (label_el.text or '').strip() if label_el is not None else ''
            if label_text == 'original layout':
                layout_dest = 'CurrentLayout'
            else:
                layout_dest = 'SelectedLayout'
                has_layout  = True
                lr = lrc.find('LayoutReference')
                if lr is not None:
                    layout_id   = lr.get('id', '0')
                    layout_name = lr.get('name', '')

        anim = p.find('Animation')
        if anim is not None:
            # "Cross Dissolve" → "CrossDissolve"
            anim_name = anim.get('name', 'None').replace(' ', '')
            if anim_name != 'None':
                animation = anim_name
                has_animation = True
            # Source <Animation name="None" value="0"/> is FM's "no animation"
            # marker — omit from output so HR doesn't render an empty animation
            # parameter. FM round-trip 2026-05-15 confirms.

        opts = p.find('Options')
        if opts is not None:
            # SaXML <Options> child contains attributes for the flag-style
            # checkboxes in the step's parameter dialog. Empty
            # <Options></Options> means no flags are set, in which case ALL
            # boolean checkboxes default to False per the canonical snippet
            # shape. TODO 2026-05-15: when a script with one of these flags
            # checked is round-tripped, capture the actual SaXML attribute
            # name(s) here. Prior `ShowRelated` + inversion logic was
            # incorrect — it caused empty <Options> to emit
            # `<Option state="True"/>` (rendering as "Show only related
            # records" in HR) and bit the 2026-05-15 audit pass.
            pass

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Go to Related Record">',
        f'{L1}<Option state="{option}"/>',
        f'{L1}<MatchAllRecords state="{match_all}"/>',
        f'{L1}<ShowInNewWindow state="{show_new_window}"/>',
        f'{L1}<Restore state="{restore}"/>',
        f'{L1}<LayoutDestination value="{layout_dest}"/>',
        f'{L1}<NewWndStyles Style="Document" Close="Yes" Minimize="Yes" Maximize="Yes" Resize="Yes" Styles="3606018"/>',
        f'{L1}<Table id="{table_id}" name="{escape_attr(table_name)}"/>',
    ]
    if has_layout:
        parts.append(f'{L1}<Layout id="{layout_id}" name="{escape_attr(layout_name)}"/>')
    if has_animation:
        parts.append(f'{L1}<Animation value="{animation}"/>')
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


def tx_insert_from_url(step) -> str:
    enable, sid = step_attrs(step)
    no_interact = 'True'
    dont_encode = 'False'
    select_all  = 'True'
    verify_ssl  = 'True'
    curl_calc   = ''
    url_calc    = ''
    target_p    = None

    for p in all_params(step):
        ptype = p.get('type', '')
        if ptype == 'Boolean':
            b = p.find('Boolean')
            if b is not None:
                btype = b.get('type', '')
                val   = b.get('value', 'False')
                if btype == 'Verify SSL Certificates':
                    verify_ssl = val
                elif btype == 'Select':
                    select_all = val
                elif btype == 'With dialog':
                    # "With dialog = False" → NoInteract = True (inverted)
                    no_interact = 'False' if val == 'True' else 'True'
        elif ptype == 'Target':
            target_p = p
        elif ptype == 'URL':
            url_el = p.find('URL')
            if url_el is not None:
                # autoEncode="True" → DontEncodeURL="False" (inverted)
                dont_encode = 'False' if url_el.get('autoEncode', 'True') == 'True' else 'True'
                url_calc = get_calc_text(url_el)
        elif ptype == 'Calculation':
            curl_calc = get_calc_text(p)

    parts = [
        f'{S}<Step enable="{enable}" id="{sid}" name="Insert from URL">',
        f'{L1}<NoInteract state="{no_interact}"/>',
        f'{L1}<DontEncodeURL state="{dont_encode}"/>',
        f'{L1}<SelectAll state="{select_all}"/>',
        f'{L1}<VerifySSLCertificates state="{verify_ssl}"/>',
    ]
    if curl_calc:
        parts += [
            f'{L1}<CURLOptions>',
            f'{L2}<Calculation>{cdata(curl_calc)}</Calculation>',
            f'{L1}</CURLOptions>',
        ]
    parts.append(f'{L1}<Calculation>{cdata(url_calc)}</Calculation>')
    parts.append(f'{L1}<Text/>')
    field_xml = _target_field_xml(target_p)
    if field_xml:
        parts.append(field_xml)
    parts.append(f'{S}</Step>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Step catalog (for generic fallback)
# ---------------------------------------------------------------------------

def _find_catalog():
    """Locate step-catalog-en.json relative to this script."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, '..', 'catalogs', 'step-catalog-en.json'))
    if os.path.isfile(candidate):
        return candidate
    return None


def _load_catalog():
    """Return dict keyed by step name with the full catalog entry."""
    path = _find_catalog()
    if path is None:
        return {}
    with open(path, encoding='utf-8') as f:
        entries = json.load(f)
    return {e['name']: e for e in entries if 'name' in e}


_CATALOG = _load_catalog()

# SaXML Boolean types that are inverted in fmxmlsnippet.
# SaXML "With dialog = False" → fmxmlsnippet "NoInteract state=True"
_INVERTED_BOOLEANS = {
    'With dialog': 'NoInteract',
    'autoEncode': 'AutoEncodeURL',
}


# ---------------------------------------------------------------------------
# Generic catalog-driven translator (fallback for uncovered steps)
# ---------------------------------------------------------------------------

def _extract_saxml_calc(param_el) -> str:
    """Extract calculation text from a SaXML <Parameter type="Calculation">."""
    return get_calc_text(param_el)


def _extract_saxml_boolean(param_el):
    """
    Extract boolean info from a SaXML <Parameter> containing <Boolean>.
    Returns (bool_type, value) or None.
    """
    b = param_el.find('Boolean')
    if b is not None:
        return b.get('type', ''), b.get('value', 'False')
    return None


def _extract_saxml_field_ref(param_el):
    """Extract field reference info from a SaXML <Parameter type="FieldReference">."""
    fr = param_el.find('FieldReference')
    if fr is None:
        return None
    fid = fr.get('id', '0')
    fname = fr.get('name', '')
    tor = fr.find('TableOccurrenceReference')
    ftbl = tor.get('name', '') if tor is not None else ''
    return ftbl, fid, fname


def _extract_saxml_target(param_el):
    """Extract target (variable or field) from a SaXML <Parameter type="Target">."""
    v = param_el.find('Variable')
    if v is not None:
        return 'variable', v.get('value', '')
    fr = param_el.find('FieldReference')
    if fr is not None:
        fid = fr.get('id', '0')
        fname = fr.get('name', '')
        tor = fr.find('TableOccurrenceReference')
        ftbl = tor.get('name', '') if tor is not None else ''
        return 'field', (ftbl, fid, fname)
    return None, None


def _extract_saxml_list(param_el):
    """Extract List info from a SaXML <Parameter> containing <List>."""
    lst = param_el.find('List')
    if lst is None:
        return None
    return {
        'name': lst.get('name', ''),
        'value': lst.get('value', ''),
        'calc': get_calc_text(lst),
    }


def _extract_saxml_text(param_el) -> str:
    """Extract text value from parameter's Text child or value attribute."""
    t = param_el.find('Text')
    if t is not None and t.text:
        return t.text
    return param_el.get('value', '')


def tx_generic(step) -> str:
    """
    Catalog-driven generic translator for steps without hand-coded handlers.

    Strategy:
    1. Look up the step in the catalog by name
    2. Walk the SaXML <Parameter> elements and map to catalog params by type
    3. Emit fmxmlsnippet child elements based on catalog param definitions

    This handles the ~70% of steps with standard parameter structures.
    Steps with non-standard structures (boolean inversions, nested lists,
    complex enum mappings) should have hand-coded translators instead.
    """
    name = step.get('name', '')
    enable, sid = step_attrs(step)
    entry = _CATALOG.get(name)

    if entry is None:
        # Not in catalog — fall through to tx_unknown
        return _tx_unknown_inner(name, enable, sid)

    cat_params = entry.get('params', [])

    # Self-closing steps with no params
    if entry.get('selfClosing', False) and not cat_params:
        return f'{S}<Step enable="{enable}" id="{sid}" name="{escape_attr(name)}"/>'

    # Collect SaXML parameters
    saxml_params = all_params(step)

    # If no SaXML params and no catalog params, self-close
    if not saxml_params and not cat_params:
        return f'{S}<Step enable="{enable}" id="{sid}" name="{escape_attr(name)}"/>'

    # Build child elements by walking catalog params and matching SaXML params
    children = []
    used_saxml = set()  # track consumed SaXML param indices

    for cat_p in cat_params:
        xml_el = cat_p.get('xmlElement', '')
        ptype = cat_p.get('type', '')
        wrapper = cat_p.get('wrapperElement', '')
        xml_attr = cat_p.get('xmlAttr', 'state')

        if ptype == 'boolean':
            # Find matching Boolean in SaXML params
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                binfo = _extract_saxml_boolean(sp)
                if binfo is None:
                    continue
                bool_type, val = binfo
                if bool_type == 'Collapsed':
                    # Internal display state — emit as <Restore>
                    children.append(f'{L1}<Restore state="{val}"/>')
                    used_saxml.add(i)
                    break
                # Check for inverted booleans
                if bool_type in _INVERTED_BOOLEANS:
                    inv_el = _INVERTED_BOOLEANS[bool_type]
                    inv_val = 'False' if val == 'True' else 'True'
                    children.append(f'{L1}<{inv_el} {xml_attr}="{inv_val}"/>')
                    used_saxml.add(i)
                    break
                # Standard boolean
                children.append(f'{L1}<{xml_el} {xml_attr}="{val}"/>')
                used_saxml.add(i)
                break

        elif ptype == 'calculation':
            # Find first unused Calculation param
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                if sp.get('type') == 'Calculation':
                    calc = _extract_saxml_calc(sp)
                    if wrapper:
                        children.append(f'{L1}<{wrapper}>')
                        children.append(f'{L2}<Calculation>{cdata(calc)}</Calculation>')
                        children.append(f'{L1}</{wrapper}>')
                    else:
                        children.append(f'{L1}<Calculation>{cdata(calc)}</Calculation>')
                    used_saxml.add(i)
                    break

        elif ptype == 'namedCalc':
            # Named calculation — wrapped in a parent element
            wrap = wrapper or xml_el
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                # Match by SaXML type containing "Calculation" or the wrapper name
                sp_type = sp.get('type', '')
                if sp_type in ('Calculation', 'Title', 'Message', 'Parameter'):
                    calc = _extract_saxml_calc(sp)
                    if calc or sp_type in ('Title', 'Message'):
                        children.append(f'{L1}<{wrap}>')
                        children.append(f'{L2}<Calculation>{cdata(calc)}</Calculation>')
                        children.append(f'{L1}</{wrap}>')
                        used_saxml.add(i)
                        break

        elif ptype == 'text':
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                sp_type = sp.get('type', '')
                if sp_type in ('Comment', 'Text', xml_el):
                    text = _extract_saxml_text(sp)
                    if text:
                        children.append(f'{L1}<{xml_el}>{escape_xml(text)}</{xml_el}>')
                        used_saxml.add(i)
                        break

        elif ptype == 'field':
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                sp_type = sp.get('type', '')
                if sp_type == 'FieldReference':
                    info = _extract_saxml_field_ref(sp)
                    if info:
                        ftbl, fid, fname = info
                        children.append(
                            f'{L1}<{xml_el} table="{escape_attr(ftbl)}" '
                            f'id="{fid}" name="{escape_attr(fname)}"/>'
                        )
                        used_saxml.add(i)
                        break
                elif sp_type == 'Target':
                    kind, val = _extract_saxml_target(sp)
                    if kind == 'variable':
                        children.append(f'{L1}<{xml_el}>{escape_xml(val)}</{xml_el}>')
                    elif kind == 'field':
                        ftbl, fid, fname = val
                        children.append(
                            f'{L1}<{xml_el} table="{escape_attr(ftbl)}" '
                            f'id="{fid}" name="{escape_attr(fname)}"/>'
                        )
                    used_saxml.add(i)
                    break

        elif ptype == 'enum':
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                lst_info = _extract_saxml_list(sp)
                if lst_info:
                    val = lst_info['name'] or lst_info['value']
                    if val:
                        children.append(f'{L1}<{xml_el} {xml_attr}="{escape_attr(val)}"/>')
                        used_saxml.add(i)
                        break
                # Also check Options param
                if sp.get('type') == 'Options':
                    opts = sp.find('Options')
                    if opts is not None:
                        val = opts.get('type', '') or opts.get('value', '')
                        if val:
                            children.append(f'{L1}<{xml_el} {xml_attr}="{escape_attr(val)}"/>')
                            used_saxml.add(i)
                            break

        elif ptype == 'flagElement':
            # Flag elements: presence = on, absence = off
            # Check if any SaXML boolean with matching semantics exists
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                binfo = _extract_saxml_boolean(sp)
                if binfo:
                    _, val = binfo
                    if val == 'True':
                        children.append(f'{L1}<{xml_el}/>')
                    used_saxml.add(i)
                    break

        elif ptype == 'script':
            # Script reference
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                if sp.get('type') == 'List':
                    lst = sp.find('List')
                    if lst is not None:
                        sr = lst.find('ScriptReference')
                        if sr is not None:
                            s_id = sr.get('id', '0')
                            s_name = sr.get('name', '')
                            children.append(
                                f'{L1}<Script id="{s_id}" '
                                f'name="{escape_attr(s_name)}"/>'
                            )
                            used_saxml.add(i)
                            break

        elif ptype == 'layout':
            # Layout reference
            for i, sp in enumerate(saxml_params):
                if i in used_saxml:
                    continue
                if sp.get('type') == 'Layout':
                    lrc = sp.find('LayoutReferenceContainer')
                    if lrc is not None:
                        lr = lrc.find('LayoutReference')
                        if lr is not None:
                            l_id = lr.get('id', '0')
                            l_name = lr.get('name', '')
                            children.append(
                                f'{L1}<Layout id="{l_id}" '
                                f'name="{escape_attr(l_name)}"/>'
                            )
                            used_saxml.add(i)
                            break

    # Emit the step
    if children:
        parts = [f'{S}<Step enable="{enable}" id="{sid}" name="{escape_attr(name)}">']
        parts.extend(children)
        parts.append(f'{S}</Step>')
        return '\n'.join(parts)
    else:
        # No children extracted — emit self-closing
        return f'{S}<Step enable="{enable}" id="{sid}" name="{escape_attr(name)}"/>'


# ---------------------------------------------------------------------------
# Unknown step (no catalog entry and no hand-coded translator)
# ---------------------------------------------------------------------------

def _tx_unknown_inner(name, enable, sid) -> str:
    """Emit a placeholder for a completely unknown step."""
    print(
        f'WARNING: unhandled step type "{name}" (id={sid}) — '
        'emitted as self-closing with TODO comment',
        file=sys.stderr,
    )
    return (
        f'{S}<!-- TODO: manual conversion required for step "{escape_xml(name)}" -->\n'
        f'{S}<Step enable="{enable}" id="{sid}" name="{escape_attr(name)}"/>'
    )


def tx_unknown(step) -> str:
    name = step.get('name', 'Unknown')
    enable, sid = step_attrs(step)
    # Try the generic catalog-driven translator first
    if name in _CATALOG:
        return tx_generic(step)
    return _tx_unknown_inner(name, enable, sid)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

TRANSLATORS = {
    '# (comment)':             tx_comment,
    'Allow User Abort':        tx_allow_user_abort,
    'Set Error Capture':       tx_set_error_capture,
    'If':                      tx_if_elseif,
    'Else If':                 tx_if_elseif,
    'Else':                    tx_else,
    'End If':                  tx_self_closing,
    'Loop':                    tx_loop,
    'Exit Loop If':            tx_exit_loop_if,
    'End Loop':                tx_self_closing,
    'Exit Script':             tx_exit_script,
    'Set Variable':            tx_set_variable,
    'Perform Script':          tx_perform_script,
    'Perform Script on Server': tx_perform_script_on_server,
    'New Window':              tx_new_window,
    'Show Custom Dialog':      tx_show_custom_dialog,
    'Set Field':               tx_set_field,
    'Commit Records/Requests': tx_commit,
    'Refresh Object':          tx_refresh_object,
    'Insert Calculated Result': tx_insert_calculated_result,
    'Insert Text':             tx_insert_text,
    'Insert from URL':         tx_insert_from_url,
    'Open URL':                tx_open_url,
    'Go to Object':            tx_go_to_object,
    'Go to Related Record':    tx_go_to_related_record,
    'Close Window':            tx_close_window,
    'Sort Records':            tx_sort_records,
    'Replace Field Contents':         tx_replace_field_contents,
    'Get File Exists':                    tx_get_file_exists,
    'Set Layout Object Animation':        tx_set_layout_object_animation,
    'Refresh Portal':                     tx_refresh_portal,
    'Pause/Resume Script':                tx_pause_resume,
    'Go to Layout':                       tx_go_to_layout,
    'Set Web Viewer':                     tx_set_web_viewer,
    'Get File Size':                      tx_get_file_size,
    'Insert File':                        tx_insert_file,
    'Perform JavaScript in Web Viewer':   tx_perform_js_in_web_viewer,
    'Create Data File':                   tx_create_data_file,
    'Open Data File':                     tx_open_data_file,
    'Write to Data File':                 tx_write_to_data_file,
    'Close Data File':                    tx_close_data_file,
    'Delete File':                        tx_delete_file,
    # found sets
    'Enter Find Mode':                    tx_enter_find_mode,
    'Perform Find':                       tx_perform_find,
    'Constrain Found Set':                tx_constrain_found_set,
    'Extend Found Set':                   tx_extend_found_set,
    # fields
    'Set Field By Name':                  tx_set_field_by_name,
    # records
    'New Record/Request':                 tx_self_closing,
    'Omit Record':                        tx_self_closing,
    # windows
    'Freeze Window':                      tx_self_closing,
}


# ---------------------------------------------------------------------------
# Core translation
# ---------------------------------------------------------------------------

def translate_script(input_path: Path) -> str:
    tree = ET.parse(str(input_path))
    root = tree.getroot()

    object_list = root.find('.//ObjectList')
    if object_list is None:
        raise ValueError(f'No <ObjectList> found in {input_path}')

    parts = ['<?xml version="1.0"?>', '<fmxmlsnippet type="FMObjectList">']
    for step_el in object_list.findall('Step'):
        name = step_el.get('name', '')
        translator = TRANSLATORS.get(name, tx_unknown)
        parts.append(translator(step_el))
    parts.append('</fmxmlsnippet>')

    return '\n'.join(parts) + '\n'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f'Error: {input_path} not found', file=sys.stderr)
        sys.exit(1)

    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    result = translate_script(input_path)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding='utf-8')
        print(f'Written to {output_path}', file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()
