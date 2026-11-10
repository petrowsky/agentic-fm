#!/usr/bin/env python3
"""
fm_xml_to_snippet.py

Translate a FileMaker "Save As XML" (SaXML) script export (xml_parsed/scripts/
format) into the fmxmlsnippet clipboard format used by FileMaker for pasting.

Usage:
    python3 agent/scripts/fm_xml_to_snippet.py <input.xml> [output.xml]

Arguments:
    input.xml   Path to the Save-As-XML script file (from xml_parsed/scripts/)
    output.xml  Optional output path. Defaults to stdout.

Architecture (P6.4 — catalog unification):
    Every non-control step is decoded by ``saxml_read.read_saxml_step`` into the
    shared per-catalog-param ``values[]`` token array and emitted by the catalog
    grammar engine ``catalog_emit.convert_step_with_catalog`` — the SAME engine the
    HR→XML converter uses. No per-step XML knowledge lives here anymore; the catalog
    ``params[]`` grammar is the single source of truth. A ``SeededResolver`` carries
    the SaXML step's own field/script/layout IDs through emit so real IDs survive.

    Only the control-flow primitives stay hand-coded (the sanctioned exception):
    ``# (comment)``, If / Else If / Else / End If, Loop / Exit Loop If / End Loop,
    Exit Script, Set Variable. The engine handles everything else — including Perform
    Script, Allow User Abort, and Set Error Capture (folded into the grammar in P6.3).

Notes:
    - Meaningful state (NoInteract, bitmask window styles, etc.) is derived from
      the structured ParameterValues via the catalog grammar. The Options bitmask
      is read only where FileMaker keeps a setting nowhere else: the find steps'
      Restore (saxml_read._FIND_RESTORE_OPTION_BIT).
    - The hash attribute on each step is not included in output.
    - A step whose SaXML shape a decoder cannot yet render correctly raises
      ``UnsupportedSaXML`` and is emitted as a clearly-marked TODO placeholder with a
      stderr warning — never silently-wrong output (fail loud, constraint #3).
    - Step types absent from the catalog emit a self-closing Step and a TODO comment.
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catalog_emit import EmptyResolver, convert_step_with_catalog
from catalog_grammar import load_catalog
from saxml_read import UnsupportedSaXML, read_saxml_step

# ---------------------------------------------------------------------------
# Indentation (control-flow hand-coders only; the engine emits its own)
# ---------------------------------------------------------------------------

S  = '  '       # step level (direct children of <fmxmlsnippet>)
L1 = '    '     # children of <Step>
L2 = '      '   # grandchildren of <Step>


# ---------------------------------------------------------------------------
# Helpers (shared by the control-flow translators)
# ---------------------------------------------------------------------------

def escape_xml(text: str) -> str:
    """Escape &, <, > for XML text content, and write a carriage return as ``&#13;``.

    XML parsing turns a raw CR into LF, and FileMaker stores CR and LF as different
    characters, so a multi-line comment written with a raw CR pastes back as LF.
    """
    if not text:
        return ''
    if '&' not in text and '<' not in text and '>' not in text and '\r' not in text:
        return text
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('\r', '&#13;'))


def cdata(text: str) -> str:
    """Wrap text in a CDATA section, splitting each "]]>" across two sections.

    "]]>" ends a CDATA section, so an unsplit one leaves malformed XML that
    FileMaker refuses to paste; FileMaker joins the sections back into one value.
    """
    body = (text or "").replace("]]>", "]]]]><![CDATA[>")
    return f'<![CDATA[{body}]]>'


def get_calc_text(element) -> str:
    """Extract calculation text from the SaXML nested Calculation structure:

        <outer-element>
            <Calculation datatype="N" position="N">
                <Calculation><Text><![CDATA[...]]></Text></Calculation>
            </Calculation>
        </outer-element>
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
# Control-flow translators (the sanctioned hand-coded exception).
# The catalog grammar engine does not model block indentation / control-flow,
# so these stay here — each returns a fully-formed <Step> string.
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
    """Self-closing control-flow steps with no children: End If, End Loop."""
    enable, sid = step_attrs(step)
    name = step.get('name', '')
    return f'{S}<Step enable="{enable}" id="{sid}" name="{name}"/>'


def tx_exit_script(step) -> str:
    enable, sid = step_attrs(step)
    # Exit Script may optionally carry a script result calculation.
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
    # FileMaker writes the Minimum flush as the token "Min" in fmxmlsnippet while
    # displaying "Minimum". If the SaXML source carries the display label,
    # normalise it so the emitted snippet matches the catalog enum value.
    if flush.lower() == 'minimum':
        flush = 'Min'
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


# ---------------------------------------------------------------------------
# Control-flow dispatch (the ONLY hand-coded steps)
# ---------------------------------------------------------------------------

CONTROL_TRANSLATORS = {
    '# (comment)':   tx_comment,
    'If':            tx_if_elseif,
    'Else If':       tx_if_elseif,
    'Else':          tx_else,
    'End If':        tx_self_closing,
    'Loop':          tx_loop,
    'Exit Loop If':  tx_exit_loop_if,
    'End Loop':      tx_self_closing,
    'Exit Script':   tx_exit_script,
    'Set Variable':  tx_set_variable,
}


# ---------------------------------------------------------------------------
# Catalog grammar engine — every non-control step
# ---------------------------------------------------------------------------

def _find_catalog() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, '..', 'catalogs', 'step-catalog-en.json'))
    return candidate if os.path.isfile(candidate) else None


_CATALOG_PATH = _find_catalog()
_BY_NAME = {e.name: e for e in load_catalog(_CATALOG_PATH)} if _CATALOG_PATH else {}


def _self_close(entry, disabled: bool) -> str:
    sid = entry.id if entry.id is not None else 0
    return f'{S}<Step enable="{"False" if disabled else "True"}" id="{sid}" name="{escape_xml(entry.name)}"/>'


def tx_engine(step, stats: dict) -> str:
    """Decode a non-control SaXML step via the catalog grammar (reader → emit).

    Real object IDs are preserved through the SeededResolver returned by the reader.
    A catalog-absent step or an undecodable SaXML shape emits a marked placeholder
    and is counted (fail loud, constraint #3) — never silently-wrong XML.
    """
    name = step.get('name', '')
    enable, sid = step_attrs(step)
    entry = _BY_NAME.get(name)
    if entry is None:
        stats['unknown'] += 1
        print(f'WARNING: step "{name}" (id={sid}) not in catalog — self-closing placeholder',
              file=sys.stderr)
        return (f'{S}<!-- TODO: manual conversion required for step "{escape_xml(name)}" -->\n'
                f'{S}<Step enable="{enable}" id="{sid}" name="{escape_xml(name)}"/>')
    try:
        disabled, values, resolver = read_saxml_step(entry, step)
    except UnsupportedSaXML as ex:
        stats['unsupported'] += 1
        print(f'WARNING: step "{name}" (id={sid}) — {ex}; TODO placeholder emitted',
              file=sys.stderr)
        return (f'{S}<!-- TODO: unsupported SaXML shape for "{escape_xml(name)}": {escape_xml(str(ex))} -->\n'
                f'{S}<Step enable="{enable}" id="{sid}" name="{escape_xml(name)}"/>')
    if entry.self_closing and not entry.params:
        return _self_close(entry, disabled)
    return convert_step_with_catalog(entry, disabled, values, resolver)


# ---------------------------------------------------------------------------
# Core translation
# ---------------------------------------------------------------------------

def translate_script(input_path: Path, stats: dict | None = None) -> str:
    if stats is None:
        stats = {'unknown': 0, 'unsupported': 0}
    tree = ET.parse(str(input_path))
    root = tree.getroot()

    object_list = root.find('.//ObjectList')
    if object_list is None:
        raise ValueError(f'No <ObjectList> found in {input_path}')

    parts = ['<?xml version="1.0"?>', '<fmxmlsnippet type="FMObjectList">']
    for step_el in object_list.findall('Step'):
        name = step_el.get('name', '')
        control = CONTROL_TRANSLATORS.get(name)
        if control is not None:
            parts.append(control(step_el))
        else:
            parts.append(tx_engine(step_el, stats))
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

    stats = {'unknown': 0, 'unsupported': 0}
    result = translate_script(input_path, stats)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding='utf-8')
        print(f'Written to {output_path}', file=sys.stderr)
    else:
        print(result)

    if stats['unknown'] or stats['unsupported']:
        print(f'NOTE: {stats["unknown"]} unknown, {stats["unsupported"]} unsupported step(s)',
              file=sys.stderr)


# Kept for the emptyresolver id-normalized path (tests / no-context conversion).
_EMPTY = EmptyResolver()


if __name__ == '__main__':
    main()
