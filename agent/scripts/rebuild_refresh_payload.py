#!/usr/bin/env python3
"""Rebuild a RefreshPayload-style script onto TMPL_NewScript shape.

A RefreshPayload is the canonical "build a JSON payload from FM data
and push it to a web viewer" pattern used by the SL Inventory module's
dialog scripts (INV_NewItem_RefreshPayload, INV_StandardCost_RefreshPayload,
INV_PackagingCost_RefreshPayload, INV_SupplierPriceDates_RefreshPayload).
These scripts typically:
  - Read a launch parameter from $$INV_*_LaunchParam (or run unparameterized)
  - Issue ExecuteSQLe queries against type/lookup tables
  - Assemble a JSON payload via JSONSetElement
  - Call Perform JavaScript in Web Viewer to push the payload to JS

This tool restructures a freshly-converted RefreshPayload (from
fm_xml_to_snippet.py) onto the canonical TMPL_NewScript skeleton:

  1. Strip the disabled <Insert Text $README> doc-block (banned by team
     convention 2026-05-13; SL003 enforces).
  2. Remove top-level Allow User Abort + Set Error Capture steps
     (template uses no top-level globals; SL006 enforces).
  3. Inject a Modified history entry into the existing # HISTORY block.
  4. Insert BEGIN PSEUDO LOOP marker comments before the outer Loop
     (SL005 enforces).
  5. Insert InitializeScriptParameterObject + JSON-validity guard at
     the top of the pseudo-loop body.
  6. Wrap each Perform JavaScript in Web Viewer call with the canonical
     Set Error Capture / CreateEventObjectFmErrorsOnly wrapper (SL010
     enforces).
  7. Insert END PSEUDO LOOP marker comments after End Loop (SL005).
  8. Append CLEANUP, DISPLAY NOTIFICATION / ERROR, and HANDLE RESULT &
     EXIT sections (SL014, SL015, SL012 enforce).
  9. Collapse adjacent blank # (comment) steps (SL011).
  10. Collapse vertical-alignment padding inside Calculation CDATA
      (SL013).

After this tool runs, the rebuilt script should be lint-clean against
all SL fork rules. Verify with `python3 -m agent.fmlint <output.xml>`.

Assumptions about input:
  - File is already converted to fmxmlsnippet (use fm_xml_to_snippet.py
    first if you have SaXML).
  - Script has an existing # HISTORY block, OR you've pre-patched one
    in (this tool's _inject_history function expects the block to exist;
    it doesn't synthesize one).
  - Script has at least one Loop step (the outer pseudo-loop).
  - If the script has Perform JavaScript in Web Viewer steps, each
    needs its own error message (passed as --error-message; repeat for
    multiple).

Not in scope:
  - Transactional workflows (Open/Commit/Revert Transaction) — different
    shape; use TMPL_NewScript - Transactions as a manual reference.
  - ActionRouter scripts — use Pattern B HANDLE RESULT (different
    cascade entry); handle separately.
  - Scripts without an existing # # PURPOSE / # # HISTORY header —
    normalize the header manually before running this tool.

Each step writes to disk on success, so partial progress survives if a
later step throws.

Usage (CLI):

    python3 agent/scripts/rebuild_refresh_payload.py \\
        agent/sandbox/INV_Whatever_RefreshPayload.xml \\
        --history-description "Rebuilt on TMPL_NewScript..." \\
        --init-param-note "INIT PARAM (template convention; this script reads \\$\\$INV_Whatever_LaunchParam instead)" \\
        --error-message "Could not push Whatever payload to the web viewer" \\
        [--error-message "Could not push fallback payload"]

Usage (Python API):

    from rebuild_refresh_payload import rebuild
    rebuild(
        file_path="agent/sandbox/INV_Whatever_RefreshPayload.xml",
        desc="Rebuilt on TMPL_NewScript...",
        init_param_note="INIT PARAM (template convention; ...)",
        error_messages=["Could not push Whatever payload to the web viewer"],
    )

Multiple --error-message flags must match the number of PJSIWV steps
in the script (one error message per call, in source order).
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# Date the rebuild ran (used in the injected history entry). Defaults
# to today; override via CLI --date or by setting the module-level
# constant before calling rebuild() programmatically.
DATE = date.today().isoformat()
AUTHOR = "Special-Lite Developer / Claude"


def _strip_readme(content: str) -> str:
    readme_re = re.compile(
        r'  <Step enable="False" id="61" name="Insert Text">.*?</Step>\s*\n',
        re.DOTALL,
    )
    return readme_re.sub('', content, count=1)


def _remove_top_globals(content: str) -> str:
    top_global = (
        '  <Step enable="True" id="89" name="# (comment)"/>\n'
        '  <Step enable="True" id="85" name="Allow User Abort">\n'
        '    <Set state="False"/>\n'
        '  </Step>\n'
        '  <Step enable="True" id="86" name="Set Error Capture">\n'
        '    <Set state="False"/>\n'
        '  </Step>\n'
        '  <Step enable="True" id="89" name="# (comment)"/>\n'
    )
    return content.replace(top_global, '  <Step enable="True" id="89" name="# (comment)"/>\n', 1)


def _inject_history(content: str, desc: str) -> str:
    entry = (
        f'  <Step enable="True" id="89" name="# (comment)">\n'
        f'    <Text>Modified: {DATE} by {AUTHOR} — {desc}</Text>\n'
        f'  </Step>\n'
    )

    # Anchor 1: blank # (comment) before "# TODO " header
    anchor1 = re.compile(
        r'(\s*<Step enable="True" id="89" name="# \(comment\)"/>\s*\n'
        r'\s*<Step enable="True" id="89" name="# \(comment\)">\s*\n'
        r'\s*<Text># TODO )',
        re.MULTILINE,
    )
    m = anchor1.search(content)
    if m:
        return content[:m.start(1)] + entry + m.group(1) + content[m.end(1):]

    # Anchor 2: Created/Modified text step followed by blank # (comment)
    anchor2 = re.compile(
        r'(<Step enable="True" id="89" name="# \(comment\)">\s*\n'
        r'\s*<Text>(?:Created|Modified):[^<]*</Text>\s*\n'
        r'\s*</Step>\s*\n)'
        r'(\s*<Step enable="True" id="89" name="# \(comment\)"/>)',
        re.MULTILINE,
    )
    matches = list(anchor2.finditer(content))
    if matches:
        last = matches[-1]
        return content[:last.end(1)] + entry + content[last.end(1):]

    raise ValueError("Could not find HISTORY anchor")


def _insert_begin_loop_and_init(content: str, init_param_note: str) -> str:
    loop_step = (
        '  <Step enable="True" id="71" name="Loop">\n'
        '    <Restore state="False"/>\n'
        '    <FlushType value="Always"/>\n'
        '  </Step>\n'
    )
    loop_replacement = (
        '  <Step enable="True" id="89" name="# (comment)">\n'
        '    <Text>=======================================================================================</Text>\n'
        '  </Step>\n'
        '  <Step enable="True" id="89" name="# (comment)">\n'
        '    <Text>BEGIN PSEUDO LOOP</Text>\n'
        '  </Step>\n'
        '  <Step enable="True" id="89" name="# (comment)">\n'
        '    <Text>~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~</Text>\n'
        '  </Step>\n'
        + loop_step +
        '  <Step enable="True" id="89" name="# (comment)">\n'
        '    <Text>=======================================================================================</Text>\n'
        '  </Step>\n'
        '  <Step enable="True" id="89" name="# (comment)">\n'
        f'    <Text>{init_param_note}</Text>\n'
        '  </Step>\n'
        '  <Step enable="True" id="89" name="# (comment)">\n'
        '    <Text>---------------------------------------------------------------------------------------</Text>\n'
        '  </Step>\n'
        '  <Step enable="True" id="141" name="Set Variable">\n'
        '    <Value>\n'
        '      <Calculation><![CDATA[InitializeScriptParameterObject]]></Calculation>\n'
        '    </Value>\n'
        '    <Repetition>\n'
        '      <Calculation><![CDATA[1]]></Calculation>\n'
        '    </Repetition>\n'
        '    <Name>$scriptParameterObject</Name>\n'
        '  </Step>\n'
        '  <Step enable="True" id="68" name="If">\n'
        '    <Restore state="False"/>\n'
        '    <Calculation><![CDATA[not JSONIsValid ( $scriptParameterObject )]]></Calculation>\n'
        '  </Step>\n'
        '  <Step enable="True" id="141" name="Set Variable">\n'
        '    <Value>\n'
        '      <Calculation><![CDATA[CreateEventObject ( 2 ; %EVENT_TYPE_CODE_SL_CUSTOM_ERROR ; "{}" ; "{}" )]]></Calculation>\n'
        '    </Value>\n'
        '    <Repetition>\n'
        '      <Calculation><![CDATA[1]]></Calculation>\n'
        '    </Repetition>\n'
        '    <Name>$eventObject</Name>\n'
        '  </Step>\n'
        '  <Step enable="True" id="141" name="Set Variable">\n'
        '    <Value>\n'
        '      <Calculation><![CDATA[GetErrorMessage ( $eventObject )]]></Calculation>\n'
        '    </Value>\n'
        '    <Repetition>\n'
        '      <Calculation><![CDATA[1]]></Calculation>\n'
        '    </Repetition>\n'
        '    <Name>$errorMessage</Name>\n'
        '  </Step>\n'
        '  <Step enable="True" id="72" name="Exit Loop If">\n'
        '    <Calculation><![CDATA[True]]></Calculation>\n'
        '  </Step>\n'
        '  <Step enable="True" id="70" name="End If"/>\n'
        '  <Step enable="True" id="89" name="# (comment)"/>\n'
    )
    return content.replace(loop_step, loop_replacement, 1)


def _wrap_pjsiwv_calls(content: str, error_messages: list) -> str:
    pjsiwv_block = (
        '  <Step enable="True" id="175" name="Perform JavaScript in Web Viewer">\n'
        '    <ObjectName>\n'
        '      <Calculation><![CDATA["WV_CommonDialog"]]></Calculation>\n'
        '    </ObjectName>\n'
        '    <FunctionName>\n'
        '      <Calculation><![CDATA["receivePayload"]]></Calculation>\n'
        '    </FunctionName>\n'
        '    <Parameters Count="1">\n'
        '      <P>\n'
        '        <Calculation><![CDATA[$payload]]></Calculation>\n'
        '      </P>\n'
        '    </Parameters>\n'
        '  </Step>\n'
    )

    def wrap(error_msg: str) -> str:
        return (
            '  <Step enable="True" id="86" name="Set Error Capture">\n'
            '    <Set state="True"/>\n'
            '  </Step>\n'
            + pjsiwv_block +
            '  <Step enable="True" id="141" name="Set Variable">\n'
            '    <Value>\n'
            '      <Calculation><![CDATA[CreateEventObjectFmErrorsOnly]]></Calculation>\n'
            '    </Value>\n'
            '    <Repetition>\n'
            '      <Calculation><![CDATA[1]]></Calculation>\n'
            '    </Repetition>\n'
            '    <Name>$eventObjectFmErrorsOnly</Name>\n'
            '  </Step>\n'
            '  <Step enable="True" id="86" name="Set Error Capture">\n'
            '    <Set state="False"/>\n'
            '  </Step>\n'
            '  <Step enable="True" id="141" name="Set Variable">\n'
            '    <Value>\n'
            '      <Calculation><![CDATA[GetFmErrorCode ( $eventObjectFmErrorsOnly )]]></Calculation>\n'
            '    </Value>\n'
            '    <Repetition>\n'
            '      <Calculation><![CDATA[1]]></Calculation>\n'
            '    </Repetition>\n'
            '    <Name>$fmErrorCode</Name>\n'
            '  </Step>\n'
            '  <Step enable="True" id="68" name="If">\n'
            '    <Restore state="False"/>\n'
            '    <Calculation><![CDATA[$fmErrorCode <> 0]]></Calculation>\n'
            '  </Step>\n'
            '  <Step enable="True" id="141" name="Set Variable">\n'
            '    <Value>\n'
            '      <Calculation><![CDATA[CreateEventObjectMergeFmErrors ( $eventObjectFmErrorsOnly )]]></Calculation>\n'
            '    </Value>\n'
            '    <Repetition>\n'
            '      <Calculation><![CDATA[1]]></Calculation>\n'
            '    </Repetition>\n'
            '    <Name>$eventObject</Name>\n'
            '  </Step>\n'
            '  <Step enable="True" id="141" name="Set Variable">\n'
            f'    <Value>\n'
            f'      <Calculation><![CDATA["{error_msg}: " & GetErrorMessage ( $eventObject )]]></Calculation>\n'
            '    </Value>\n'
            '    <Repetition>\n'
            '      <Calculation><![CDATA[1]]></Calculation>\n'
            '    </Repetition>\n'
            '    <Name>$errorMessage</Name>\n'
            '  </Step>\n'
            '  <Step enable="True" id="72" name="Exit Loop If">\n'
            '    <Calculation><![CDATA[True]]></Calculation>\n'
            '  </Step>\n'
            '  <Step enable="True" id="70" name="End If"/>\n'
        )

    count = content.count(pjsiwv_block)
    if count != len(error_messages):
        raise ValueError(f"Expected {len(error_messages)} PJSIWV blocks, found {count}")
    # IMPORTANT: track the search position. The wrap() output contains the
    # pjsiwv_block as a substring, so naive find-from-start would re-find
    # the just-wrapped block on subsequent iterations (wrapping the same
    # PJSIWV multiple times and leaving later occurrences untouched).
    # Bit the 2026-05-15 INV_StandardCost_RefreshPayload rebuild.
    search_pos = 0
    for msg in error_messages:
        idx = content.find(pjsiwv_block, search_pos)
        if idx == -1:
            raise ValueError(f"PJSIWV block not found from position {search_pos}")
        wrapped = wrap(msg)
        content = content[:idx] + wrapped + content[idx + len(pjsiwv_block):]
        # Skip past the wrap we just inserted so the next find() starts
        # after the embedded pjsiwv_block.
        search_pos = idx + len(wrapped)
    return content


# Post-pseudo-loop block: END PSEUDO LOOP marker → CLEANUP section →
# DISPLAY NOTIFICATION / ERROR section → HANDLE RESULT & EXIT cascade.
# Always emitted by the rebuild helper; replaces any existing trailing
# Exit Script step.
HANDLE_RESULT_BLOCK = (
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>END PSEUDO LOOP</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>=======================================================================================</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)"/>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>=======================================================================================</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>CLEANUP</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>---------------------------------------------------------------------------------------</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)"/>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>=======================================================================================</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>DISPLAY NOTIFICATION / ERROR</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>---------------------------------------------------------------------------------------</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>No user notifications in this script.</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)"/>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>=======================================================================================</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>HANDLE RESULT &amp; EXIT</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>---------------------------------------------------------------------------------------</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="68" name="If">\n'
    '    <Restore state="False"/>\n'
    '    <Calculation><![CDATA[False]]></Calculation>\n'
    '  </Step>\n'
    '  <Step enable="True" id="161" name="Else If">\n'
    '    <Calculation><![CDATA[not IsEmpty ( $scriptResultObject )]]></Calculation>\n'
    '  </Step>\n'
    '  <Step enable="True" id="89" name="# (comment)">\n'
    '    <Text>Already defined. Just return and exit.</Text>\n'
    '  </Step>\n'
    '  <Step enable="True" id="161" name="Else If">\n'
    '    <Calculation><![CDATA[IsEmpty ( $errorMessage )]]></Calculation>\n'
    '  </Step>\n'
    '  <Step enable="True" id="141" name="Set Variable">\n'
    '    <Value>\n'
    '      <Calculation><![CDATA[OK]]></Calculation>\n'
    '    </Value>\n'
    '    <Repetition>\n'
    '      <Calculation><![CDATA[1]]></Calculation>\n'
    '    </Repetition>\n'
    '    <Name>$scriptResult</Name>\n'
    '  </Step>\n'
    '  <Step enable="True" id="141" name="Set Variable">\n'
    '    <Value>\n'
    '      <Calculation><![CDATA[JSONSetElement ( "{}" ; [ "scriptResult" ; $scriptResult ; JSONString ] )]]></Calculation>\n'
    '    </Value>\n'
    '    <Repetition>\n'
    '      <Calculation><![CDATA[1]]></Calculation>\n'
    '    </Repetition>\n'
    '    <Name>$scriptResultObject</Name>\n'
    '  </Step>\n'
    '  <Step enable="True" id="69" name="Else">\n'
    '    <Restore state="False"/>\n'
    '  </Step>\n'
    '  <Step enable="True" id="141" name="Set Variable">\n'
    '    <Value>\n'
    '      <Calculation><![CDATA[ERROR]]></Calculation>\n'
    '    </Value>\n'
    '    <Repetition>\n'
    '      <Calculation><![CDATA[1]]></Calculation>\n'
    '    </Repetition>\n'
    '    <Name>$scriptResult</Name>\n'
    '  </Step>\n'
    '  <Step enable="True" id="141" name="Set Variable">\n'
    '    <Value>\n'
    '      <Calculation><![CDATA[$errorMessage]]></Calculation>\n'
    '    </Value>\n'
    '    <Repetition>\n'
    '      <Calculation><![CDATA[1]]></Calculation>\n'
    '    </Repetition>\n'
    '    <Name>$scriptResultMessage</Name>\n'
    '  </Step>\n'
    '  <Step enable="True" id="141" name="Set Variable">\n'
    '    <Value>\n'
    '      <Calculation><![CDATA[JSONSetElement ( "{}"\n'
    '\t; [ "scriptResult" ; $scriptResult ; JSONString ]\n'
    '\t; [ "scriptResultMessage" ; $scriptResultMessage ; JSONString ]\n'
    '\t; [ "eventObject" ; If ( IsEmpty ( $eventObject ) ; CreateEventObjectFm ; $eventObject ) ; JSONObject ]\n'
    ')]]></Calculation>\n'
    '    </Value>\n'
    '    <Repetition>\n'
    '      <Calculation><![CDATA[1]]></Calculation>\n'
    '    </Repetition>\n'
    '    <Name>$scriptResultObject</Name>\n'
    '  </Step>\n'
    '  <Step enable="True" id="1" name="Perform Script">\n'
    '    <Calculation><![CDATA[PassSubscriptParameterObject ( $scriptResultObject )]]></Calculation>\n'
    '    <Script id="0" name="COM_ScriptResultHandler"/>\n'
    '  </Step>\n'
    '  <Step enable="True" id="70" name="End If"/>\n'
    '  <Step enable="True" id="103" name="Exit Script">\n'
    '    <Calculation><![CDATA[$scriptResultObject]]></Calculation>\n'
    '  </Step>\n'
)


def _append_end_loop_and_handle_result(content: str) -> str:
    # Find the LAST End Loop step (the outer pseudo-loop's End Loop).
    # After it, there may or may not be an existing trailing Exit Script.
    # Replace from "<End Loop> [optional Exit Script step] </fmxmlsnippet>"
    # with "<End Loop> + HANDLE RESULT block + </fmxmlsnippet>".

    # Pattern: match End Loop, optionally followed by an Exit Script step,
    # immediately before the closing </fmxmlsnippet>.
    tail_re = re.compile(
        r'(\s*<Step enable="True" id="73" name="End Loop"/>\s*\n)'
        r'(?:\s*<Step enable="True" id="103" name="Exit Script">.*?</Step>\s*\n)?'
        r'(</fmxmlsnippet>\s*\n?)$',
        re.DOTALL,
    )
    m = tail_re.search(content)
    if not m:
        raise ValueError("Could not find End Loop / </fmxmlsnippet> tail")

    new_tail = m.group(1) + HANDLE_RESULT_BLOCK + m.group(2)
    return content[:m.start()] + new_tail


def _collapse_calc_multispace(content: str) -> str:
    """Inside each <Calculation> CDATA block, collapse runs of 2+
    consecutive interior spaces down to a single space (preserving
    leading whitespace / tabs for indentation).

    Some FM developers add vertical-alignment padding to JSONSetElement
    arguments and similar calcs — banned by SL013 (team convention
    2026-05-15). This pass normalizes any pre-existing alignment
    padding in calcs that the rebuild helper preserves verbatim from
    source.
    """
    def collapse(match):
        prefix, body, suffix = match.group(1), match.group(2), match.group(3)
        # Walk lines, collapse 2+ interior spaces but preserve leading
        # whitespace (tabs/spaces that form line indentation).
        new_lines = []
        for line in body.split("\n"):
            stripped = line.lstrip(" \t")
            leading = line[: len(line) - len(stripped)]
            # Collapse 2+ runs of spaces within the stripped portion
            collapsed = re.sub(r" {2,}", " ", stripped)
            new_lines.append(leading + collapsed)
        return prefix + "\n".join(new_lines) + suffix

    cdata_re = re.compile(
        r"(<Calculation><!\[CDATA\[)(.*?)(\]\]></Calculation>)",
        re.DOTALL,
    )
    return cdata_re.sub(collapse, content)


def _collapse_adjacent_blanks(content: str) -> str:
    """Collapse runs of 2+ consecutive blank # (comment) steps down to a
    single blank. Section transitions inserted by other rebuild passes
    can end up adjacent to blanks that were already there, leaving
    visible double-blanks. SL011 flags those — this pass eliminates
    them as a final cleanup step.
    """
    blank = '  <Step enable="True" id="89" name="# (comment)"/>\n'
    while blank + blank in content:
        content = content.replace(blank + blank, blank)
    return content


def rebuild(file_path: str, desc: str, init_param_note: str, error_messages: list):
    p = Path(file_path)
    bn = p.name
    print(f"=== {bn} ===")

    # Step 1
    content = p.read_text()
    new = _strip_readme(content)
    if new != content:
        content = new
        p.write_text(content)
        print("  ✓ $README stripped")
    else:
        print("  (no $README block)")

    # Step 2
    new = _remove_top_globals(content)
    if new != content:
        content = new
        p.write_text(content)
        print("  ✓ top-level globals removed")
    else:
        print("  (no top-level globals)")

    # Step 3
    content = _inject_history(content, desc)
    p.write_text(content)
    print("  ✓ Modified history entry injected")

    # Steps 4-5
    content = _insert_begin_loop_and_init(content, init_param_note)
    p.write_text(content)
    print("  ✓ BEGIN PSEUDO LOOP + Init inserted")

    # Step 6
    content = _wrap_pjsiwv_calls(content, error_messages)
    p.write_text(content)
    print(f"  ✓ {len(error_messages)} PJSIWV block(s) wrapped")

    # Steps 7-8
    content = _append_end_loop_and_handle_result(content)
    p.write_text(content)
    print("  ✓ END PSEUDO LOOP + HANDLE RESULT appended")

    # Step 9: collapse adjacent blanks (SL011 cleanup)
    new = _collapse_adjacent_blanks(content)
    if new != content:
        content = new
        p.write_text(content)
        print("  ✓ adjacent blanks collapsed")

    # Step 10: collapse multi-space alignment padding inside calcs (SL013 cleanup)
    new = _collapse_calc_multispace(content)
    if new != content:
        content = new
        p.write_text(content)
        print("  ✓ calc alignment padding collapsed")


def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="rebuild_refresh_payload.py",
        description=(
            "Rebuild a RefreshPayload-style script onto TMPL_NewScript "
            "shape. Operates in place on an already-converted fmxmlsnippet "
            "file (use fm_xml_to_snippet.py first if you have SaXML). See "
            "the module docstring for full details."
        ),
        epilog=(
            "After running, validate the output with "
            "`python3 -m agent.fmlint <file>` — the rebuilt script should "
            "be clean against all SL fork rules (SL001–SL015)."
        ),
    )
    parser.add_argument(
        "file",
        help=(
            "Path to the fmxmlsnippet file to rebuild in place. Typically "
            "agent/sandbox/INV_<Name>_RefreshPayload.xml."
        ),
    )
    parser.add_argument(
        "-d", "--history-description",
        required=True,
        help=(
            "Text for the Modified history entry that gets injected into "
            "the script's # # HISTORY block. Describe what was changed and "
            "why (e.g., 'Rebuilt on TMPL_NewScript. ...')."
        ),
    )
    parser.add_argument(
        "-n", "--init-param-note",
        default="INIT PARAM (template convention; this script reads $$<launch_param_global> instead)",
        help=(
            "Comment text for the INIT PARAM section header in the "
            "pseudo-loop body. Use to clarify what global / launch param "
            "the script reads (since RefreshPayload scripts typically "
            "ignore the script parameter and read from "
            "$$INV_<Name>_LaunchParam)."
        ),
    )
    parser.add_argument(
        "-e", "--error-message",
        action="append",
        default=[],
        metavar="MESSAGE",
        help=(
            "Error message for a Perform JavaScript in Web Viewer call. "
            "Repeat for each PJSIWV step in the script, in source order. "
            "Used in the error path of the canonical Set Error Capture "
            "wrapper. Example: 'Could not push payload to the New Item "
            "web viewer'."
        ),
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Override the date stamped on the injected history entry (defaults to today).",
    )
    parser.add_argument(
        "--author",
        default=None,
        help="Override the author stamped on the injected history entry.",
    )
    args = parser.parse_args()

    # Apply optional date / author overrides
    global DATE, AUTHOR
    if args.date:
        DATE = args.date
    if args.author:
        AUTHOR = args.author

    try:
        rebuild(
            file_path=args.file,
            desc=args.history_description,
            init_param_note=args.init_param_note,
            error_messages=args.error_message,
        )
    except (ValueError, AssertionError, FileNotFoundError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
