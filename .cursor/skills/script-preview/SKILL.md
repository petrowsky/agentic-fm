---
name: script-preview
description: Generates a human-readable preview of a proposed FileMaker script before XML generation. Use when the user wants to preview, outline, draft, or review script steps in plain English before committing to fmxmlsnippet output. Triggers on phrases like "preview the script", "show me the steps", "outline the logic", "draft the script", or "before you generate".
---

# Script Preview

Produce a human-readable script outline for review and iteration before generating fmxmlsnippet XML.

## Deterministic line-number map from SaXML

Before generating a preview of an existing script (or when verifying line numbers), run:

```bash
python3 agent/scripts/saxmlpreview.py agent/xml_parsed/scripts/<solution>/<path/to/script.xml>
```

This parses the SaXML file and produces **one output line per `<Step>` element**, numbered from 1. Because each step maps to exactly one Script Workspace row, the line numbers are 1:1 with what a developer sees in FileMaker. Use this as the authoritative line map when discussing specific steps or referencing insertion points.

The script uses `xml.etree.ElementTree` (standard library, no dependencies). Output format matches Script Workspace (Script Workspace–format syntax, disabled steps prefixed with `//`, blank comment steps rendered as `# `).

---

## Step 1: Read context

Read `agent/CONTEXT.json`. Extract:

- `task` — what the script should do
- `current_layout` — the starting context
- Any relevant fields, scripts, layouts, and value lists needed

## Step 2: Output the preview

Format the script exactly as it would appear in FileMaker Script Workspace. This is the **absolute truth** format that developers read and compare against. Rules:

- **Include line numbers** — Script Workspace displays line numbers and developers use them for reference. Format each line as `N\tstep` (tab-separated number and step text), matching the output of `saxmlpreview.py`.
- Nested blocks (If/End If, Loop/End Loop, etc.) are indented with **4 spaces** per level
- Blank lines appear as an empty line — exactly as Script Workspace displays them. (If the developer provides any examples of HR scripts and has used the MBS plug-in it copies them as `# ` but that is a copy artifact, not the visual presentation.)
- Disabled/commented-out steps use `//` prefix: `// Set Variable [ ... ]`
- Lead with the script name as a heading
- **Never use `<placeholder>` text for calculations** — show the full calculation or a faithful abbreviated form ending with `…` for very long ones (matching how Script Workspace truncates long calc text in the step list)

**When pushing a script to the webviewer Monaco editor**, strip the line numbers — Monaco provides its own line numbering. The line-numbered format is for chat display only.

**Step syntax rules — match Script Workspace exactly:**

| Step              | Script Workspace format                                            |
| ----------------- | ------------------------------------------------------------------ |
| Set Variable      | `Set Variable [ $name ; Value: expression ]` — no `[1]` repetition |
| Exit Script       | `Exit Script [ Text Result: ]`                                     |
| Perform Script    | `Perform Script [ "Name" ; Specified: From list ; Parameter: ]`    |
| If / Else If      | `If [ condition ]` — no `Collapsed: OFF` prefix                    |
| Loop              | `Loop [ Flush: Always ]` — no `Collapsed: OFF` prefix              |
| Allow User Abort  | `Allow User Abort [ Off ]` / `Allow User Abort [ On ]`             |
| Set Error Capture | `Set Error Capture [ On ]` / `Set Error Capture [ Off ]`           |
| Commit Records    | `Commit Records/Requests [ With dialog: Off ]`                     |
| Freeze Window     | `Freeze Window`                                                    |
| Comments          | `# comment text`                                                   |

**Example format:**

```
Script: Process Invoice

1	# PURPOSE: Validate and commit a single invoice record.
2
3	Allow User Abort [ Off ]
4	Set Error Capture [ On ]
5
6	Set Variable [ $invoiceID ; Value: Get ( ScriptParameter ) ]
7	If [ IsEmpty ( $invoiceID ) ]
8	    Show Custom Dialog [ "No invoice ID provided." ]
9	    Exit Script [ Text Result:    ]
10	End If
11
12	Go to Layout [ "Invoice Details" ; Animation: None ]
13	Perform Find [ Restore ]
14
15	If [ Get ( FoundCount ) = 0 ]
16	    Show Custom Dialog [ "Invoice not found." ]
17	    Exit Script [ Text Result:    ]
18	End If
19
20	Set Field [ Invoice::Status ; "Processed" ]
21	Commit Records/Requests [ With dialog: Off ]
22	Exit Script [ Text Result: True ]
```

## Step 3: Invite iteration

After the preview, ask:

```
AskQuestion:
{
  "question": "Does this logic look right?",
  "options": [
    { "id": "good", "label": "Looks good — generate the XML" },
    { "id": "changes", "label": "I have changes to make" }
  ]
}
```

- If **changes**: ask the user to describe them, update the preview, and loop back to Step 3.
- If **good**: proceed directly to full fmxmlsnippet generation following the standard script creation workflow in AGENTS.md.

## Notes

- The preview is a planning artifact, but **step syntax must match Script Workspace format** — a developer will compare this directly against what they see in FileMaker
- When iterating, show the full updated preview each time (not just the changed lines)
- The sanitized scripts in `xml_parsed/scripts_sanitized/` use a different format (e.g. `Collapsed: OFF`, `[1]` repetitions, `Exit Script []`) — do **not** use that as the format reference; always target Script Workspace format
