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


COMPANION_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "companion.json"
)


def _load_config() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(here, "..", "config", "automation.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
    except (OSError, ValueError):
        return DEFAULT_CONFIG.copy()


def _load_companion_config() -> dict:
    """Read the `companion` block from agent/config/companion.json (fail-open → {})."""
    try:
        with open(COMPANION_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    comp = data.get("companion") if isinstance(data, dict) else None
    return comp if isinstance(comp, dict) else {}


def _resolve_companion_url(config: dict) -> str:
    """Client-reach resolution — the URL deploy dials to reach the companion.

    Precedence (highest wins):
      1. COMPANION_URL env var.
      2. companion.json  companion.advertise_host + companion.port  (canonical).
      3. automation.json companion_url  (legacy — honored during the deprecation window).
      4. built-in default.
    The users[account] layer (#76) slots in above #2 once GET /whoami lands.
    """
    env = os.environ.get("COMPANION_URL")
    if env:
        return env.rstrip("/")
    comp = _load_companion_config()
    host, port = comp.get("advertise_host"), comp.get("port")
    if host and port:
        return f"http://{host}:{port}"
    legacy = config.get("companion_url")
    if legacy:
        return legacy.rstrip("/")
    return "http://local.hub:8765"


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
# Plug-in capability detection (optional commercial plug-in)
# ---------------------------------------------------------------------------

def _check_plugin(companion_url: str) -> dict:
    """Return the plug-in capability block from the companion's /health.

    The companion is the single detection broker: one GET /health tells us
    whether the AgenticFM plug-in is *usable* (installed + reachable +
    licensed), how to reach it (server.base + bearer token), and which
    solutions it has indexed. Detection only — routing the deploy through the
    plug-in's direct script-install API is a later phase. Never raises; on any
    failure the block is empty and deploy stays on the OSS tiers.

    Returns the `plugin` block, e.g.:
      {"installed": true, "usable": true,
       "server": {"reachable": true, "base": "http://127.0.0.1:8766", "token": "..."},
       "license": {"status": "trial", ...}, "solutions": [...]}
    or {} when the companion is unreachable or reports no plug-in.
    """
    try:
        req = urllib.request.Request(f"{companion_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("plugin", {}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Window switching helper
# ---------------------------------------------------------------------------

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
        # Click the target file in the Window menu (visible files)
        f'        set _switched to false\n'
        f'        try\n'
        f'            set _menuItems to every menu item of menu "Window" of menu bar 1 whose (name is "{_esc(target_file)}" or name starts with "{_esc(target_file)} (")\n'
        f'            if (count of _menuItems) > 0 then\n'
        f'                click (item 1 of _menuItems)\n'
        f'                delay 0.5\n'
        f'                set _switched to true\n'
        f'            end if\n'
        f'        end try\n'
        # Fallback: hidden files live in Window > Show Window submenu — click to unhide
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
    """Build the raw AppleScript for Phase 2: wait for step editor, then paste.

    Phase 1 (Agentic-fm Paste FM script) already opened the correct script tab
    via ScriptWorkspace.ExpandScriptFolders + ScriptWorkspace.OpenScript.
    Phase 2 just needs to focus the step editor and paste.
    """
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    fm_process = fm_app_name.split(" — ")[0].strip()

    if select_all:
        paste_block = (
            # Use menu actions instead of keystrokes to avoid triggering
            # OnLayoutKeystroke script triggers on the active FM layout
            '        click menu item "Select All" of menu "Edit" of menu bar 1\n'
            '        delay 0.3\n'
            '        key code 51\n'  # Delete selected steps (not a layout shortcut)
            '        delay 0.3\n'
            '        click menu item "Paste" of menu "Edit" of menu bar 1\n'
        )
    else:
        paste_block = '        click menu item "Paste" of menu "Edit" of menu bar 1\n'

    save_block = ""
    if auto_save:
        save_block = (
            '        delay 0.5\n'
            '        keystroke "s" using {command down}\n'
        )

    app = _esc(fm_app_name)
    proc = _esc(fm_process)

    lines = [
        f'tell application "{app}"',
        '    activate',
        'end tell',
        'delay 0.5',
        '',
        'tell application "System Events"',
        f'    tell process "{proc}"',
        '        set wsWindows to windows whose title contains "Script Workspace"',
        '        if (count of wsWindows) = 0 then return',
        '        set wsWin to item 1 of wsWindows',
        '        -- Poll until scroll area 2 (step editor) appears',
        '        -- Phase 1 already opened the correct script via MBS OpenScript',
        '        set editorReady to false',
        '        repeat 20 times',
        '            if (count of scroll areas of splitter group 1 of wsWin) >= 2 then',
        '                set editorReady to true',
        '                exit repeat',
        '            end if',
        '            delay 0.3',
        '        end repeat',
        '        if not editorReady then return',
        '        delay 1.0',
        f'        -- Activate the target script tab (description matches "{_esc(target_script)}")',
        f'        set tabBtns to (buttons of splitter group 1 of wsWin whose description is "{_esc(target_script)}")',
        '        if (count of tabBtns) = 0 then',
        f'            return "ERROR: no SW tab matching \\"{_esc(target_script)}\\" — script likely in collapsed folder; aborting paste to avoid clobbering wrong tab"',
        '        end if',
        '        click (item 1 of tabBtns)',
        '        delay 0.5',
        '        -- After tab switch, wait for the new tab\'s step editor to settle',
        '        repeat 15 times',
        '            try',
        '                if (count of rows of table 1 of scroll area 2 of splitter group 1 of wsWin) > 0 then exit repeat',
        '            end try',
        '            delay 0.2',
        '        end repeat',
        '        delay 0.5',
        '        -- Give the step editor keyboard focus',
        '        tell splitter group 1 of wsWin',
        '            tell scroll area 2',
        '                tell table 1',
        '                    -- Try set focused first',
        '                    try',
        '                        set focused of it to true',
        '                    end try',
        '                    -- Then click the first cell in row 1 for physical focus',
        '                    if (count of rows) > 0 then',
        '                        try',
        '                            click UI element 1 of row 1',
        '                        on error',
        '                            click row 1',
        '                        end try',
        '                    else',
        '                        click',
        '                    end if',
        '                end tell',
        '            end tell',
        '        end tell',
        '        delay 0.5',
        '        -- Paste into focused step editor',
    ]
    script = "\n".join(lines) + "\n"
    script += paste_block
    script += save_block
    script += "    end tell\nend tell\n"
    return script

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

    # Step 2: if targeting a specific file, switch its window to front first,
    # then open Script Workspace from that file's context.
    # FM gates do-script privilege checks on the frontmost document.
    # Opening SW while the target file is frontmost ensures SW shows that
    # file's scripts — not agentic-fm's — when Agentic-fm Paste runs.
    if target_file:
        _switch_to_document(companion_url, fm_app_name, target_file)
        # Open Script Workspace while target file is frontmost
        def _esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')
        fm_process = fm_app_name.split(" — ")[0].strip()
        open_sw_script = (
            f'tell application "System Events"\n'
            f'    tell process "{_esc(fm_process)}"\n'
            f'        try\n'
            f'            click menu item "Script Workspace..." of menu "Scripts" of menu bar 1\n'
            f'            delay 1.0\n'
            f'        end try\n'
            f'    end tell\n'
            f'end tell\n'
        )
        _post_json(f"{companion_url}/trigger", {"raw_applescript": open_sw_script})

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

    # Phase 2: wait for Agentic-fm Paste to finish opening Script Workspace, then paste
    import time as _time; _time.sleep(1.0)
    paste_as = _paste_applescript(fm_app_name, target_script, select_all, auto_save)
    paste_result = _post_json(
        f"{companion_url}/trigger",
        {"raw_applescript": paste_as},
    )
    paste_stdout = paste_result.get("stdout", "") or ""
    if paste_stdout.startswith("ERROR:"):
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

    # Bring Claude back to the front after a successful deploy
    subprocess.run(["osascript", "-e", 'tell application "Claude" to activate'],
                   capture_output=True)

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
            # Menu item name is the window title which may differ from
            # the file name, but typically contains it.
            f'        set _switched to false\n'
            f'        try\n'
            f'            set _menuItems to every menu item of menu "Window" of menu bar 1 whose (name is "{_esc(target_file)}" or name starts with "{_esc(target_file)} (")\n'
            f'            if (count of _menuItems) > 0 then\n'
            f'                click (item 1 of _menuItems)\n'
            f'                delay 0.5\n'
            f'                set _switched to true\n'
            f'            end if\n'
            f'        end try\n'
            # Fallback: hidden files live in Window > Show Window submenu — click to unhide
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
    companion_url = _resolve_companion_url(config)
    fm_app_name = config.get("fm_app_name", "FileMaker Pro")

    # Plug-in awareness (no hardcoded endpoints). When the plug-in is usable,
    # the zero-keystroke install path is for the agent to pick a script-write
    # endpoint from the plug-in's /api/discover suite (see PLUGIN_INTEGRATION.md
    # — e.g. create new, insert steps, or read-then-fmpatch). This CLI stays the
    # OSS clipboard fallback; surface the steer once and continue.
    if _check_plugin(companion_url).get("usable"):
        print(
            "(AgenticFM plug-in is usable — for a zero-keystroke install, choose a "
            "script-write endpoint from its /api/discover suite per "
            "agent/docs/PLUGIN_INTEGRATION.md. Continuing on the OSS clipboard path.)",
            file=sys.stderr,
        )

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
