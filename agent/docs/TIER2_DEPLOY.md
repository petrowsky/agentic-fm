# Tier 2 Deploy Process

Audience: AI agents (and humans) using `agent/scripts/deploy.py --tier 2` to push fmxmlsnippet content into existing FileMaker scripts.

This document captures **all known prerequisites and failure modes** so an agent can deploy reliably without trial-and-error against the user's solution.

---

## Architecture in 60 seconds

Tier 2 is a four-actor pipeline:

```
deploy.py (CLI)
  ↓ HTTP /clipboard, /trigger
companion_server.py  (macOS host, runs osascript)
  ↓ AppleScript do-script (against target file by name)
Agentic-fm Paste     (FM script in the target file; uses MBS plugin)
  ↓ opens SW + ExpandScriptFolders + OpenScript(target_script)
deploy.py raw AppleScript phase 2
  ↓ tab-click + Cmd+A → Delete → Cmd+V → Cmd+S
```

Every actor's failure mode is silent unless deploy.py's pre-flight or post-deploy guard catches it.

---

## Prerequisites — verify ALL before deploying

| # | Requirement | How to check | What goes wrong if missing |
|---|---|---|---|
| 1 | Companion server running | `curl -s http://localhost:8765/health` | Trigger fails with connection refused |
| 2 | MBS plugin loaded in FM | Open Manage Plugins in FM | `MBS()` returns "?" → ExpandScriptFolders/OpenScript no-op |
| 3 | Target file open in FM | `osascript -e 'tell app id "com.filemaker.client.pro12" to name of every document'` | Pre-flight returns "target file not open" |
| 4 | Account has `fmextscriptaccess` | `cat agent/xml_parsed/extended_privileges/<solution>/fmextscriptaccess*.xml` — confirm your privilege set is in the ObjectList | -10004 privilege violation; **deploys silently misroute to whatever file IS frontmost & privileged** |
| 5 | Target script exists with the EXACT name | `grep "^<name>\|" agent/context/<solution>/scripts.index` | OpenScript fails, no tab opens, fail-fast guard fires |
| 6 | `Agentic-fm Paste` script in target file is the patched version | Search for `ExpandScriptFolders` and `Open Script Workspace` step | Bulk deploys race; nested-folder scripts can't be opened |

---

## CRITICAL: real script names ≠ sanitized filenames

The filesystem strips characters that aren't legal in macOS filenames:

| In FM | On disk |
|---|---|
| `DOJO: Create API Log` | `DOJO_ Create API Log - ID *.txt` |
| `Facebook: Email Campaigns` | `Facebook_ Email Campaigns - ID *.txt` |
| `Account \| Active Cases` | `Account _ Active Cases - ID *.txt` |

**Always resolve the real name from `agent/context/<solution>/scripts.index` (pipe-delimited, first column is the real name) before passing to `deploy.py`.** Passing the sanitized name causes `MBS OpenScript` to fail (script not found), no tab opens, and deploy.py's fail-fast guard reports "no SW tab matching" — but only if you're using a current deploy.py with the guard.

Helper: when grepping for empty `Exit Script []` or similar, post-process with the index to recover real names. See the `_agg.py` pattern in session history.

---

## Standard deploy invocation

```bash
python3 agent/scripts/deploy.py "<sandbox-snippet>.xml" "<exact-script-name>" \
    --tier 2 --replace --file <SolutionName>
```

- `--file` is the bare solution name (e.g. `A2X_General_data`), not the `.fmp12`.
- `--file` matching is now anchored — `A2X_General` no longer collides with `A2X_General_data`.
- `--replace` replaces all existing steps (Cmd+A → Delete before paste). Without it, the snippet is appended.
- Without `--file`, deploy.py auto-resolves from `agent/CONTEXT.json`'s `solution` field. **Pass `--file` explicitly when working on a solution other than the one CONTEXT.json was generated for.**

---

## Bulk deploy pattern

Multiple deploys in sequence MUST use halt-on-failure with `pipefail`, otherwise `python3 ... | tail -1` masks the exit code and the loop barrels through:

```bash
set -eo pipefail
for n in "Script A" "Script B" "Script C"; do
  out=$(python3 agent/scripts/deploy.py "agent/sandbox/${n}.xml" "$n" \
        --tier 2 --replace --file <Solution> 2>&1)
  rc=$?
  echo "$n → rc=$rc | $(echo "$out" | tail -1)"
  if [ $rc -ne 0 ]; then echo "HALT — $n failed"; break; fi
done
```

The pre-flight and post-deploy verification (added 2026-04) catch the most common silent failures, but `pipefail` is still the discipline that converts any non-zero exit into a halt.

---

## Failure modes — symptoms and fixes

| Symptom | Root cause | Fix |
|---|---|---|
| `Error: AppleScript privilege denied (-10004)` | Logged-in account in target file lacks `fmextscriptaccess` | Re-login as Full Access account, OR add your privilege set to fmextscriptaccess in Manage > Security > Extended Privileges |
| `Error: target file "X" is not open in FileMaker` | File not opened in FM session | User opens the file; deploy.py only operates on already-open files |
| `Error: ERROR: no SW tab matching "X"` | `MBS OpenScript` could not find the script — likely wrong script name (sanitized vs real) OR script doesn't exist OR collapsed-folder issue not resolved | Resolve real script name from `scripts.index`. If real name is correct, verify Agentic-fm Paste in target file has 3× `ExpandScriptFolders`. |
| `Error: Post-deploy verification failed: SW title "Script Workspace (A2X_Y)" does not contain target file "A2X_X"` | Cross-file SW context — paste went to the wrong file's SW | Patched Agentic-fm Paste (with `Open Script Workspace` step) is required in target file. Repaste it manually if needed. |
| `Error: Post-deploy verification failed: Step count mismatch: expected ~N, got M` | Paste landed in wrong tab OR didn't replace existing content (Select-All failed because focus was on browse window) | Investigate: does the file have a custom menu set hiding "Select All" from Edit menu? `_paste_applescript` clicks `[Standard FileMaker Menus]` first. If still failing, check the target script's parent file is correctly frontmost. |
| Dialog: "Before typing, press Tab or click in a field…" | Keystrokes hit FM's browse mode (no SW or wrong focus) | Same root cause as the verification failure above — SW context or focus. |
| Bulk loop: all snippets land in same tab | Tab-switching race (pre-fix); should NOT happen with current deploy.py | If it does, `_paste_applescript` lost the `whose description is target_script` tab-click step. |
| Refresh shows old content despite "deploy succeeded" | FM auto-save ran but Save-As-XML refresh hadn't flushed when checked | Wait a beat after deploy, then refresh. Verify via `stat -f "%Sm" <raw-xml>` to confirm export is fresh. |

---

## Post-deploy verification (built-in)

`deploy.py` now performs an automatic check after every Tier 2 paste:

1. SW window title contains target file name → confirms right file's SW is frontmost
2. Step editor row count is within ±1 of expected (counted from `<Step ` tags in the snippet) → confirms the paste replaced rather than appended or no-op'd

Failure causes deploy.py to return `success=False` with an actionable error. **Do not assume "Script steps replaced ... via Tier 2." means it actually landed correctly without the verification fingerprint** — older deploy.py versions reported success even on misroutes.

---

## When Tier 2 is the wrong tool

- **Replacing `Agentic-fm Paste` itself across all 7 files**: bootstrap problem if the current Paste is broken. Use Tier 1 (clipboard) and have the user paste manually into each.
- **Creating a new script (not replacing)**: use Tier 3, which uses keystroke `n` for "New Script" then renames it. Tier 2 only replaces existing scripts.
- **Multi-file solutions where target file lacks `fmextscriptaccess`**: Tier 2 is impossible without re-login or privilege change. Fall back to Tier 1.

---

## Debugging cheat sheet

```bash
# What documents are open?
curl -s -X POST http://localhost:8765/trigger -H 'Content-Type: application/json' \
  -d '{"raw_applescript":"tell app id \"com.filemaker.client.pro12\" to name of every document"}'

# What windows are visible?
curl -s -X POST http://localhost:8765/trigger -H 'Content-Type: application/json' \
  -d '{"raw_applescript":"tell app \"System Events\" to tell process \"FileMaker Pro\" to name of every window"}'

# What tabs are in the active SW?
curl -s -X POST http://localhost:8765/trigger -H 'Content-Type: application/json' \
  -d '{"raw_applescript":"tell app \"System Events\" to tell process \"FileMaker Pro\" to description of every button of splitter group 1 of (first window whose title contains \"Script Workspace\")"}'

# Hidden-file submenu?
curl -s -X POST http://localhost:8765/trigger -H 'Content-Type: application/json' \
  -d '{"raw_applescript":"tell app \"System Events\" to tell process \"FileMaker Pro\" to name of every menu item of menu \"Show Window\" of menu item \"Show Window\" of menu \"Window\" of menu bar 1"}'

# Privilege probe (catches -10004)
curl -s -X POST http://localhost:8765/trigger -H 'Content-Type: application/json' \
  -d '{"raw_applescript":"tell app \"FileMaker Pro\" to name of every document"}'
```

---

## See also

- `agent/scripts/deploy.py` — source of truth for the deploy flow.
- `agent/scripts/companion_server.py` — the macOS-host-side trigger handler.
- `agent/sandbox/agentic_fm_paste_updated.xml` — the patched `Agentic-fm Paste` snippet (must be deployed to every target file before bulk Tier 2 will work).
- `agent/docs/AUTOMATION.md` — broader automation context including OData/server flows.
