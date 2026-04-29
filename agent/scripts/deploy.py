#!/usr/bin/env python3
"""
deploy.py - Pluggable deployment module for agentic-fm.

Loads a validated fmxmlsnippet XML file to the FileMaker clipboard and
optionally triggers an automated paste into the Script Workspace.

Tier 1 (universal):  companion /clipboard → developer pastes manually
Tier 2 (MBS):        companion /clipboard + /trigger → Agentic-fm Paste auto-pastes
Tier 3 (MBS + AS):   companion /trigger creates placeholder → then Tier 2

Usage (CLI):
    python3 agent/scripts/deploy.py <xml_path> [target_script] [--tier N]

Usage (module):
    from deploy import deploy
    result = deploy("agent/sandbox/MyScript.xml", target_script="My Script")

Result dict keys:
    success       — bool
    tier_used     — int (1, 2, or 3; may differ from requested if fallback)
    instructions  — str (Tier 1 and fallback cases — present to developer)
    message       — str (Tier 2/3 success — for logging)
    fallback_from — int (present when fell back from a higher tier)
    fallback_reason — str (why the fallback occurred)
    error         — str (present on failure)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "default_tier": 1,
    "auto_save": False,
    "fm_app_name": "FileMaker Pro",
    "companion_url": "http://local.hub:8765",
}


def _load_config() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(here, "..", "config", "automation.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
    except (OSError, ValueError):
        return DEFAULT_CONFIG.copy()


def _resolve_target_file(config: dict) -> str | None:
    """Auto-resolve the FM file name to target for multi-file deploys.

    Priority:
      1. CONTEXT.json → 'solution' field (scoped to what the developer is working on)
      2. automation.json → 'solutions' keys (only if exactly 1 solution configured)

    Returns None if the file cannot be unambiguously determined.
    """
    # Try CONTEXT.json first
    here = os.path.dirname(os.path.abspath(__file__))
    context_path = os.path.join(here, "..", "CONTEXT.json")
    try:
        with open(context_path, "r", encoding="utf-8") as f:
            ctx = json.load(f)
        solution = ctx.get("solution", "")
        if solution:
            return solution
    except (OSError, ValueError):
        pass

    # Fall back to automation.json solutions keys
    solutions = config.get("solutions", {})
    if len(solutions) == 1:
        return next(iter(solutions))

    return None


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {"success": False, "error": f"HTTP {exc.code}: {raw}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Pre-flight & verification helpers
# ---------------------------------------------------------------------------

def _preflight_check(companion_url: str, fm_app_name: str, target_file: str) -> tuple[bool, str]:
    """Verify FM is ready for Tier 2 deploy to target_file.

    Checks (in order):
      1. Target file is open as an FM document (open exact-name match).
      2. AppleScript privilege gate passes (no -10004) when target file is frontmost.
         Catches the common "user logged in without fmextscriptaccess" case.

    Returns (ok, error_message). On ok=True, error_message is empty.
    """
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    tf = _esc(target_file)
    # Address the target document directly. Avoids enumerating siblings,
    # which can fail with -10004 if any open file is locked (e.g. an
    # unopenable hosted file). The target-doc id read also exercises the
    # AppleScript privilege gate for the target's account specifically.
    probe = (
        f'try\n'
        f'    tell application id "com.filemaker.client.pro12"\n'
        f'        tell (first document whose name is "{tf}.fmp12" or name is "{tf}")\n'
        f'            set _n to name\n'
        f'        end tell\n'
        f'    end tell\n'
        f'    return "OK " & _n\n'
        f'on error errMsg number errNum\n'
        f'    if errNum is -10004 then\n'
        f'        return "ERROR: AppleScript privilege denied (-10004) for {tf}. Log in to that file as a Full Access account, OR grant fmextscriptaccess to your privilege set in Manage > Security > Extended Privileges."\n'
        f'    else if errNum is -1728 then\n'
        f'        return "ERROR: target file \\"{tf}\\" is not open in FileMaker"\n'
        f'    else\n'
        f'        return "ERROR: pre-flight probe failed (" & errNum & "): " & errMsg\n'
        f'    end if\n'
        f'end try'
    )
    result = _post_json(f"{companion_url}/trigger", {"raw_applescript": probe})
    stdout = (result.get("stdout") or "").strip()
    if stdout.startswith("OK"):
        return True, ""
    return False, stdout or f"Pre-flight check failed (no output). stderr={result.get('stderr', '')}"


def _verify_paste(
    companion_url: str,
    fm_app_name: str,
    target_file: str,
    target_script: str,
    expected_step_count: int,
) -> tuple[bool, str]:
    """Verify the paste landed in the right place.

    Checks:
      1. SW window title contains target_file (right file's SW is frontmost).
      2. Active tab description matches target_script.
      3. Step editor row count matches expected (within ±1 tolerance for empty-step rendering).

    Returns (ok, message). On ok=False, message describes the mismatch.
    """
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    tf, ts = _esc(target_file), _esc(target_script)
    probe = (
        f'tell application "System Events"\n'
        f'    tell process "FileMaker Pro"\n'
        f'        set wsWins to (windows whose title contains "Script Workspace")\n'
        f'        if (count of wsWins) = 0 then return "ERROR: no Script Workspace window after paste"\n'
        f'        set wsWin to item 1 of wsWins\n'
        f'        set winTitle to title of wsWin\n'
        f'        if winTitle does not contain "{tf}" then return "ERROR: SW title \\"" & winTitle & "\\" does not contain target file \\"{tf}\\" — paste went to wrong file"\n'
        f'        try\n'
        f'            set rowCount to (count of rows of table 1 of scroll area 2 of splitter group 1 of wsWin)\n'
        f'        on error\n'
        f'            return "ERROR: step editor not visible — could not count rows"\n'
        f'        end try\n'
        f'        return "OK rows=" & rowCount\n'
        f'    end tell\n'
        f'end tell'
    )
    result = _post_json(f"{companion_url}/trigger", {"raw_applescript": probe})
    stdout = (result.get("stdout") or "").strip()
    if stdout.startswith("ERROR"):
        return False, stdout
    if not stdout.startswith("OK rows="):
        return False, f"Verification probe returned unexpected: {stdout!r}"
    try:
        actual = int(stdout.split("=", 1)[1])
    except (ValueError, IndexError):
        return False, f"Could not parse row count: {stdout!r}"
    # Allow ±1 tolerance — FM sometimes adds a trailing empty-step row in the editor.
    if abs(actual - expected_step_count) > 1:
        return False, (
            f"Step count mismatch: expected ~{expected_step_count}, got {actual}. "
            f"The paste may have landed in the wrong tab or been appended."
        )
    return True, f"verified ({actual} rows, expected ~{expected_step_count})"


def _count_snippet_steps(xml: str) -> int:
    """Count <Step> elements in an fmxmlsnippet body. Used for paste verification."""
    # Crude but reliable: count opening Step tags. Self-closing and open both start with "<Step ".
    return xml.count("<Step ")


def _switch_to_document(
    companion_url: str,
    fm_app_name: str,
    target_file: str,
) -> dict:
    """Bring the target file's window to front via System Events.

    FM gates AppleScript do-script privilege checks on the frontmost
    document. If the wrong file is frontmost and lacks fmextscriptaccess,
    do-script fails with -10004 even when targeting the correct document.
    This helper switches the frontmost window before any do-script call.

    Uses the Tools > Custom Menus > [Standard FileMaker Menus] guard to
    ensure the Window menu is available, then clicks the target file's
    entry in the Window menu.
    """
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    fm_process = fm_app_name.split(" \u2014 ")[0].strip()

    applescript = (
        f'tell application "{_esc(fm_app_name)}"\n'
        f'    activate\n'
        f'end tell\n'
        f'\n'
        f'delay 0.3\n'
        f'\n'
        f'tell application "System Events"\n'
        f'    tell process "{_esc(fm_process)}"\n'
        # Switch to standard menus so the Window menu is available
        f'        try\n'
        f'            click menu item "[Standard FileMaker Menus]" of menu "Custom Menus" of menu item "Custom Menus" of menu "Tools" of menu bar 1\n'
        f'            delay 0.3\n'
        f'        end try\n'
        # Click the target file in the Window menu (visible files).
        # Anchored matching: `name contains "A2X_General"` would also match
        # "A2X_General_data" — use exact / prefix-with-space-paren match instead.
        # Also exclude Script Workspace entries — they sort first and would
        # be picked instead of the database window.
        f'        set _switched to false\n'
        f'        try\n'
        f'            set _menuItems to every menu item of menu "Window" of menu bar 1 whose (name is "{_esc(target_file)}" or name starts with "{_esc(target_file)} (") and name does not start with "Script Workspace "\n'
        f'            if (count of _menuItems) > 0 then\n'
        f'                click (item 1 of _menuItems)\n'
        f'                delay 0.5\n'
        f'                set _switched to true\n'
        f'            end if\n'
        f'        end try\n'
        # Fallback: hidden files live in Window > Show Window submenu.
        # Click to unhide and bring forward.
        f'        if not _switched then\n'
        f'            try\n'
        f'                set _showItems to every menu item of menu "Show Window" of menu item "Show Window" of menu "Window" of menu bar 1 whose (name is "{_esc(target_file)}" or name starts with "{_esc(target_file)} (")\n'
        f'                if (count of _showItems) > 0 then\n'
        f'                    click (item 1 of _showItems)\n'
        f'                    delay 0.7\n'
        f'                end if\n'
        f'            end try\n'
        f'        end if\n'
        f'    end tell\n'
        f'end tell\n'
    )

    return _post_json(
        f"{companion_url}/trigger",
        {"raw_applescript": applescript},
    )


# ---------------------------------------------------------------------------
# Tier 1
# ---------------------------------------------------------------------------

def _tier1(
    xml: str,
    companion_url: str,
    target_script: str | None,
    target_file: str | None = None,
) -> dict:
    """Write XML to clipboard via companion, return paste instructions."""
    result = _post_json(f"{companion_url}/clipboard", {"xml": xml})
    if not result.get("success"):
        return {
            "success": False,
            "tier_used": 1,
            "error": result.get("error", "Clipboard write failed"),
        }

    file_hint = f" in **{target_file}**" if target_file else ""
    if target_script:
        instructions = (
            f"Script loaded to clipboard.\n"
            f"  1. In FM Pro open '{target_script}'{file_hint} in Script Workspace\n"
            f"  2. Select all steps (⌘A)\n"
            f"  3. Paste (⌘V)"
        )
    else:
        instructions = (
            f"Script loaded to clipboard.\n"
            f"  Paste (⌘V) into the target script{file_hint} in Script Workspace."
        )

    return {"success": True, "tier_used": 1, "instructions": instructions}


# ---------------------------------------------------------------------------
# Tier 2
# ---------------------------------------------------------------------------

def _paste_applescript(fm_app_name: str, target_script: str, select_all: bool, auto_save: bool) -> str:
    """Build the raw AppleScript for Phase 2: AXPress tab + paste.

    This runs from outside FM (via companion osascript), not from within
    a Perform AppleScript step. AXPress only works from outside FM —
    Perform AppleScript within FM causes Script Workspace to lose focus.
    """
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    fm_process = fm_app_name.split(" \u2014 ")[0].strip()

    # Build the select+delete block if replacing
    if select_all:
        paste_block = (
            f'        keystroke "a" using {{command down}}\n'
            f'        delay 0.2\n'
            f'        key code 51\n'
            f'        delay 0.2\n'
            f'        keystroke "v" using {{command down}}\n'
        )
    else:
        paste_block = (
            f'        keystroke "v" using {{command down}}\n'
        )

    # Build auto-save block
    save_block = ""
    if auto_save:
        save_block = (
            f'        delay 0.5\n'
            f'        keystroke "s" using {{command down}}\n'
        )

    return (
        f'tell application "{_esc(fm_app_name)}"\n'
        f'    activate\n'
        f'end tell\n'
        f'\n'
        f'delay 0.3\n'
        f'\n'
        f'tell application "System Events"\n'
        f'    tell process "{_esc(fm_process)}"\n'
        # AXPress the script tab to move focus to step editor.
        # If no tab matches target_script, abort with ERROR so deploy.py
        # surfaces the misroute instead of pasting into whatever's focused.
        f'        set wsWindows to windows whose title contains "Script Workspace"\n'
        f'        if (count of wsWindows) = 0 then\n'
        f'            return "ERROR: no Script Workspace window open"\n'
        f'        end if\n'
        f'        tell item 1 of wsWindows\n'
        f'            tell splitter group 1\n'
        f'                set tabButtons to every button whose description is "{_esc(target_script)}"\n'
        f'                if (count of tabButtons) = 0 then\n'
        f'                    return "ERROR: no SW tab matching \\"{_esc(target_script)}\\" — script likely not in current SW context (wrong file frontmost) or in a collapsed folder; aborting paste to avoid clobbering wrong tab"\n'
        f'                end if\n'
        f'                perform action "AXPress" of item 1 of tabButtons\n'
        f'            end tell\n'
        f'        end tell\n'
        f'        delay 0.5\n'
        # Paste sequence
        f'{paste_block}'
        f'{save_block}'
        f'    end tell\n'
        f'end tell\n'
    )


def _tier2(
    xml: str,
    companion_url: str,
    fm_app_name: str,
    target_script: str | None,
    auto_save: bool = False,
    select_all: bool = True,
    target_file: str | None = None,
) -> dict:
    """Two-phase deploy: FM opens the script tab, companion pastes from outside.

    Phase 1 — FM-side (do script "Agentic-fm Paste"):
      Activates FM, opens Script Workspace, opens the target script tab
      via MBS ScriptWorkspace.OpenScript. Then exits.

    Phase 2 — Companion-side (raw AppleScript via osascript):
      AXPress the tab button to focus the step editor, then
      Cmd+A → Delete → Cmd+V (or just Cmd+V for append).
      AXPress must run from outside FM — Perform AppleScript within FM
      causes Script Workspace to lose focus on the step editor.
    """
    # Step 1: load clipboard
    clip_result = _post_json(f"{companion_url}/clipboard", {"xml": xml})
    if not clip_result.get("success"):
        return {
            "success": False,
            "tier_used": 2,
            "error": clip_result.get("error", "Clipboard write failed"),
        }

    if not target_script:
        return {
            "success": True,
            "tier_used": 2,
            "instructions": (
                "Script loaded to clipboard. No target script specified — paste manually (⌘V)."
            ),
        }

    # Step 2: if targeting a specific file, switch its window to front first.
    # FM gates do-script privilege checks on the frontmost document — if the
    # wrong file is frontmost and lacks fmextscriptaccess, do-script fails
    # with -10004 even when the tell-document targets the correct file.
    if target_file:
        _switch_to_document(companion_url, fm_app_name, target_file)
        # Pre-flight: file open + AppleScript privilege gate
        ok, msg = _preflight_check(companion_url, fm_app_name, target_file)
        if not ok:
            return {
                "success": False,
                "tier_used": 2,
                "error": msg,
            }

    # Phase 1: trigger FM Pro to run Agentic-fm Paste (opens script tab only)
    trigger_payload = {
        "fm_app_name": fm_app_name,
        "script": "Agentic-fm Paste",
        "parameter": target_script,
    }
    if target_file:
        trigger_payload["target_file"] = target_file

    trigger_result = _post_json(f"{companion_url}/trigger", trigger_payload)
    if not trigger_result.get("success"):
        # Fall back to Tier 1 instructions — clipboard is already loaded
        file_hint = f" in **{target_file}**" if target_file else ""
        return {
            "success": True,
            "tier_used": 1,
            "fallback_from": 2,
            "fallback_reason": trigger_result.get("error", "Trigger failed"),
            "instructions": (
                f"Auto-paste unavailable — clipboard is loaded, paste manually.\n"
                f"  1. In FM Pro open '{target_script}'{file_hint} in Script Workspace\n"
                f"  2. Select all steps (⌘A)\n"
                f"  3. Paste (⌘V)"
            ),
        }

    # Phase 2: AXPress tab + paste from outside FM
    paste_as = _paste_applescript(fm_app_name, target_script, select_all, auto_save)
    paste_result = _post_json(
        f"{companion_url}/trigger",
        {"raw_applescript": paste_as},
    )
    paste_stdout = (paste_result.get("stdout") or "").strip()
    if paste_stdout.startswith("ERROR:"):
        # Phase 2 AppleScript aborted before paste (no SW window or no matching tab)
        return {
            "success": False,
            "tier_used": 2,
            "error": paste_stdout,
        }
    if not paste_result.get("success"):
        return {
            "success": True,
            "tier_used": 1,
            "fallback_from": 2,
            "fallback_reason": f"Script opened but paste failed: {paste_result.get('error', 'unknown')}",
            "instructions": (
                f"Script '{target_script}' is open but paste failed.\n"
                f"  Clipboard is loaded — paste manually (⌘A → Delete → ⌘V)."
            ),
        }

    # Post-deploy verification — confirm paste landed in the right SW + tab,
    # and step count is in the expected range. Catches silent misroutes that
    # the AppleScript "completed without error" path doesn't.
    if target_file:
        expected = _count_snippet_steps(xml)
        ok, vmsg = _verify_paste(companion_url, fm_app_name, target_file, target_script, expected)
        if not ok:
            return {
                "success": False,
                "tier_used": 2,
                "error": f"Post-deploy verification failed: {vmsg}",
            }

    mode = "replaced" if select_all else "appended to"
    return {
        "success": True,
        "tier_used": 2,
        "message": f"Script steps {mode} '{target_script}' via Tier 2.",
    }


# ---------------------------------------------------------------------------
# Tier 3
# ---------------------------------------------------------------------------

def _is_local_macos() -> bool:
    """True when deploy.py is running natively on macOS, not in a container.

    When False, osascript is not available locally. All AppleScript execution
    is delegated to the companion server on the macOS host via /trigger.
    The Accessibility pre-flight check is skipped — the companion's terminal
    process (not the agent's container) must hold Accessibility permission.
    """
    return sys.platform == "darwin"


def _check_accessibility() -> tuple[bool, str]:
    """Check whether the calling process has macOS Accessibility permission.

    Only meaningful when running natively on macOS (_is_local_macos() is True).
    In a container or non-macOS environment, skip this check entirely — the
    companion server on the macOS host runs osascript, and its process is the
    one that needs Accessibility permission.

    Runs a minimal System Events AppleScript. If Accessibility access has not
    been granted to the terminal / shell executing this script, macOS blocks
    the call and returns an error containing 'not authorized' or error code
    -1743. The check is fast (~0.3 s) and silent on success.

    Returns:
        (True, "")            — permission granted, safe to proceed
        (False, reason_str)   — permission denied; reason_str is a human-
                                readable explanation with remediation steps
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True, ""
        err = result.stderr.strip().lower()
        if "not authorized" in err or "1743" in err or "accessibility" in err or "assistive" in err:
            terminal = os.environ.get("TERM_PROGRAM") or os.environ.get("LC_TERMINAL") or "your terminal app"
            return False, (
                f"Tier 3 requires Accessibility permission for '{terminal}'.\n"
                f"\n"
                f"  1. Open System Settings → Privacy & Security → Accessibility\n"
                f"  2. Add '{terminal}' (or the app running this shell) and enable it\n"
                f"  3. Re-run the deploy command\n"
                f"\n"
                f"  If the app is already listed but toggled off, toggle it off and back on.\n"
                f"  macOS may have shown an authorization dialog — check for it behind other windows."
            )
        return False, f"System Events error: {result.stderr.strip()}"
    except FileNotFoundError:
        return False, "osascript not found — Tier 3 requires macOS."
    except subprocess.TimeoutExpired:
        return False, "Accessibility check timed out."


def _tier3(
    xml: str,
    companion_url: str,
    fm_app_name: str,
    target_script: str | None,
    auto_save: bool = False,
    target_file: str | None = None,
) -> dict:
    """Create and name a script via monolithic AppleScript, then paste steps.

    Loads XML to clipboard first, then runs a raw AppleScript on the host
    (synchronous — waits for completion):
      0. Switch to Standard FileMaker Menus via Tools > Custom Menus
         (guards against custom menu sets that hide the Scripts menu)
      1. Open Script Workspace if not already open
      2. Cmd+N  → creates "New Script"
      3. Scripts menu → Rename Script → type target name → Return
      4. Cmd+S  → save (required before do script, or FM blocks with dialog)
      5. Cmd+A  → select all steps
      6. Delete  → remove default step
      7. Cmd+V  → paste from clipboard (already loaded in step 0)
      8. Cmd+S  → save after paste (always — new scripts are always saved)

    Notes:
      - tell application uses fm_app_name (versioned, with em dash)
      - tell process uses the base name only ("FileMaker Pro") — System Events
        process names never include the version suffix
      - raw_applescript is synchronous; clipboard must be loaded before firing
      - paste is done inline via System Events Cmd+V, not via Agentic-fm Paste
      - Custom menu guard ensures Scripts menu is always available
    """
    if not target_script:
        return _tier2(xml, companion_url, fm_app_name, target_script, auto_save, target_file=target_file)

    # Pre-flight: verify Accessibility permission before doing any work.
    # Only when running natively on macOS — in a container, AppleScript
    # runs on the companion host and its process needs the permission.
    if _is_local_macos():
        accessible, reason = _check_accessibility()
        if not accessible:
            return {
                "success": False,
                "tier_used": 3,
                "error": f"Accessibility permission required for Tier 3.\n{reason}",
            }

    # Step 0: load clipboard before firing the AppleScript
    clip_result = _post_json(f"{companion_url}/clipboard", {"xml": xml})
    if not clip_result.get("success"):
        return {
            "success": False,
            "tier_used": 3,
            "error": clip_result.get("error", "Clipboard write failed"),
        }

    def _esc(s: str) -> str:
        """Escape a string for embedding inside an AppleScript double-quoted string."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    # System Events process name — always the base app name without version suffix.
    # "FileMaker Pro — 22.0.4.406" → "FileMaker Pro"
    fm_process = fm_app_name.split(" \u2014 ")[0].strip()

    # Build the document-targeting preamble. When target_file is set:
    #   1. Switch to Standard FM Menus (so Window menu is available)
    #   2. Use Window menu to bring the target file's window to front
    #   3. Switch to Standard FM Menus again (the target file may have
    #      its own custom menus that replaced the menu bar on switch)
    # When no target_file, just do the standard menu switch once.
    if target_file:
        doc_targeting = (
            # First: switch to standard menus on whatever file is frontmost
            # so the Window menu becomes available
            f'        try\n'
            f'            click menu item "[Standard FileMaker Menus]" of menu "Custom Menus" of menu item "Custom Menus" of menu "Tools" of menu bar 1\n'
            f'            delay 0.3\n'
            f'        end try\n'
            # Use Window menu to bring the target file's window to front.
            # Anchored matching avoids prefix collisions (e.g. A2X_General vs
            # A2X_General_data). Show Window submenu fallback for hidden files.
            f'        set _switched to false\n'
            f'        try\n'
            f'            set _menuItems to every menu item of menu "Window" of menu bar 1 whose (name is "{_esc(target_file)}" or name starts with "{_esc(target_file)} (") and name does not start with "Script Workspace "\n'
            f'            if (count of _menuItems) > 0 then\n'
            f'                click (item 1 of _menuItems)\n'
            f'                delay 0.5\n'
            f'                set _switched to true\n'
            f'            end if\n'
            f'        end try\n'
            f'        if not _switched then\n'
            f'            try\n'
            f'                set _showItems to every menu item of menu "Show Window" of menu item "Show Window" of menu "Window" of menu bar 1 whose (name is "{_esc(target_file)}" or name starts with "{_esc(target_file)} (")\n'
            f'                if (count of _showItems) > 0 then\n'
            f'                    click (item 1 of _showItems)\n'
            f'                    delay 0.7\n'
            f'                end if\n'
            f'            end try\n'
            f'        end if\n'
            # Now the target file is frontmost — switch its menus to
            # standard too (it may have its own custom menu set)
            f'        try\n'
            f'            click menu item "[Standard FileMaker Menus]" of menu "Custom Menus" of menu item "Custom Menus" of menu "Tools" of menu bar 1\n'
            f'            delay 0.3\n'
            f'        end try\n'
        )
    else:
        doc_targeting = (
            # No multi-file targeting — just ensure standard menus
            f'        try\n'
            f'            click menu item "[Standard FileMaker Menus]" of menu "Custom Menus" of menu item "Custom Menus" of menu "Tools" of menu bar 1\n'
            f'            delay 0.3\n'
            f'        end try\n'
        )

    applescript = (
        f'tell application "{_esc(fm_app_name)}"\n'
        f'    activate\n'
        f'end tell\n'
        f'\n'
        f'delay 0.5\n'
        f'\n'
        f'tell application "System Events"\n'
        f'    tell process "{_esc(fm_process)}"\n'
        f'{doc_targeting}'
        # Open Script Workspace (try/end try — may already be open)
        f'        try\n'
        f'            click menu item "Script Workspace..." of menu "Scripts" of menu bar 1\n'
        f'            delay 1.0\n'
        f'        end try\n'
        # Create new script
        f'        keystroke "n" using {{command down}}\n'
        f'        delay 0.5\n'
        # Rename the new script
        f'        click menu item "Rename Script" of menu "Scripts" of menu bar 1\n'
        f'        delay 1.0\n'
        f'        keystroke "{_esc(target_script)}"\n'
        f'        delay 0.2\n'
        f'        key code 36\n'
        f'        delay 0.5\n'
        # Paste → Save (new script has no existing steps — no select/delete needed)
        f'        keystroke "v" using {{command down}}\n'
        f'        delay 0.5\n'
        f'        keystroke "s" using {{command down}}\n'
        f'        delay 0.3\n'
        f'    end tell\n'
        f'end tell\n'
    )

    create_result = _post_json(
        f"{companion_url}/trigger",
        {"raw_applescript": applescript},
    )
    if not create_result.get("success"):
        # Script creation failed — fall through to Tier 2 (paste into existing)
        # Clipboard is already loaded so Tier 2 can skip the clipboard step.
        tier2_result = _tier2(
            xml, companion_url, fm_app_name, target_script, auto_save,
            target_file=target_file,
        )
        return {
            **tier2_result,
            "fallback_from": 3,
            "fallback_reason": create_result.get("error", "Script creation failed"),
        }

    return {
        "success": True,
        "tier_used": 3,
        "message": f"Script '{target_script}' created, steps pasted, and saved via Tier 3.",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def deploy(
    xml_path: str,
    target_script: str | None = None,
    tier: int | None = None,
    auto_save: bool | None = None,
    select_all: bool = True,
    target_file: str | None = None,
) -> dict:
    """
    Deploy a validated fmxmlsnippet XML file to FileMaker.

    Args:
        xml_path:      Path to the fmxmlsnippet XML file.
        target_script: Name of the script to paste into (Tier 2/3).
        tier:          Override the configured default tier (1, 2, or 3).
        auto_save:     Override the configured auto_save setting.
        select_all:    Replace (True) or append (False) existing steps (Tier 2).
        target_file:   FM file name to target (for multi-file solutions).
                       Auto-resolved from CONTEXT.json or automation.json if None.

    Returns:
        Result dict — always contains 'success' and 'tier_used'.
        Tier 1 / fallback: also contains 'instructions' to show the developer.
        Tier 2/3 success: also contains 'message' for logging.
    """
    config = _load_config()
    effective_tier = tier if tier is not None else config.get("default_tier", 1)
    effective_auto_save = auto_save if auto_save is not None else bool(config.get("auto_save", False))
    companion_url = config.get("companion_url", "http://local.hub:8765").rstrip("/")
    fm_app_name = config.get("fm_app_name", "FileMaker Pro")

    # Auto-resolve target file if not provided
    if target_file is None:
        target_file = _resolve_target_file(config)

    try:
        with open(xml_path, "r", encoding="utf-8") as f:
            xml = f.read()
    except OSError as exc:
        return {"success": False, "error": f"Cannot read {xml_path}: {exc}"}

    if effective_tier == 3:
        return _tier3(xml, companion_url, fm_app_name, target_script, effective_auto_save, target_file)
    elif effective_tier == 2:
        return _tier2(xml, companion_url, fm_app_name, target_script, effective_auto_save, select_all, target_file)
    else:
        return _tier1(xml, companion_url, target_script, target_file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Deploy a validated fmxmlsnippet XML file to FileMaker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("xml_path", help="Path to the fmxmlsnippet XML file")
    parser.add_argument(
        "target_script", nargs="?", help="Script name to paste into (Tier 2/3)"
    )
    parser.add_argument(
        "--tier", type=int, choices=[1, 2, 3], help="Override deployment tier"
    )
    parser.add_argument(
        "--auto-save", action="store_true", default=None, dest="auto_save",
        help="Auto-save the script after paste (Tier 2/3 only)"
    )
    parser.add_argument(
        "--no-auto-save", action="store_false", dest="auto_save",
        help="Do not auto-save after paste (overrides config)"
    )
    parser.add_argument(
        "--file", dest="target_file", default=None,
        help="FM file name to target (for multi-file solutions). Auto-resolved if omitted."
    )
    paste_group = parser.add_mutually_exclusive_group()
    paste_group.add_argument(
        "--replace", action="store_true", default=False,
        help="Replace all existing steps without prompting (Tier 2 only)"
    )
    paste_group.add_argument(
        "--append", action="store_true", default=False,
        help="Append after existing steps without prompting (Tier 2 only)"
    )
    args = parser.parse_args()

    # Tier 2 targeting an existing script is destructive — always confirm unless
    # --replace or --append bypasses the prompt explicitly.
    select_all = True
    effective_tier = args.tier or _load_config().get("default_tier", 1)
    if effective_tier == 2 and args.target_script:
        if args.append:
            select_all = False
        elif not args.replace:
            print(f"\nScript '{args.target_script}' will be modified.")
            print("  [r] Replace — select all existing steps and paste (destructive)")
            print("  [a] Append  — paste after existing steps")
            print("  [c] Cancel")
            try:
                choice = input("Choice [r/a/c]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                sys.exit(0)
            if choice == "c":
                print("Cancelled.")
                sys.exit(0)
            elif choice == "a":
                select_all = False

    result = deploy(args.xml_path, args.target_script, args.tier, args.auto_save, select_all, args.target_file)

    # Human-friendly output
    if result.get("instructions"):
        print(result["instructions"])
    elif result.get("message"):
        print(result["message"])
    elif result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)

    if result.get("fallback_from"):
        print(
            f"(Fell back from Tier {result['fallback_from']}: {result.get('fallback_reason', '')})",
            file=sys.stderr,
        )

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
