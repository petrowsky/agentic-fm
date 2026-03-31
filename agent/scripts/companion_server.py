#!/usr/bin/env python3
"""
companion_server.py - Lightweight HTTP companion server for agentic-fm.

Lightweight HTTP companion server for shell command execution. FileMaker
calls this server via the native Insert from URL step (curl-compatible).

Usage:
    Start server:
        python agent/scripts/companion_server.py

    Start on custom port:
        python agent/scripts/companion_server.py --port 9000

    FileMaker calls it via Insert from URL:
        POST http://localhost:8765/explode
"""

import argparse
import json
import logging
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8765
BIND_HOST = os.environ.get("COMPANION_BIND_HOST", "127.0.0.1")
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/petrowsky/agentic-fm/main/version.txt"
FILE_WATCH_POLL_INTERVAL_SECONDS = 0.5
FILE_WATCH_MAX_EVENTS = 100
FILE_WATCH_MAX_IMPORTS = 20
IMPORT_LOG_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} ")
IMPORT_LOG_UNKNOWN_VALUE_RE = re.compile(r'Attribute value [“"](.+?)[”"] unknown\.')
IMPORT_LOG_ATTRIBUTE_MISSING_RE = re.compile(r'^Attribute [“"](.+?)[”"] missing\.$')
IMPORT_LOG_FIELD_MISSING_RE = re.compile(r'^Field [“"](.+?)[”"] missing\.$')
IMPORT_LOG_FIELD_REFERENCE_MISSING_RE = re.compile(r'^Field referred to in the calculation [“"](.+?)[”"] is missing\.$', re.S)
IMPORT_LOG_LAYOUT_MISSING_RE = re.compile(r'^Layout [“"](.+?)[”"] missing\.$')
IMPORT_LOG_SCRIPT_MISSING_RE = re.compile(r'^Script [“"](.+?)[”"] missing\.$')
IMPORT_LOG_FUNCTION_MISSING_RE = re.compile(r'^Function referred to in the calculation [“"](.+?)[”"] is missing\.$', re.S)
IMPORT_LOG_TABLE_MISSING_RE = re.compile(r'^Table referred to in the calculation [“"](.+?)[”"] is missing\.$', re.S)
IMPORT_LOG_UNKNOWN_ERROR_RE = re.compile(r'^Unknown Error: <unknown>\.$', re.I)

# Read version from version.txt at the repo root
def _read_local_version() -> str:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        version_file = os.path.join(here, "..", "..", "version.txt")
        with open(version_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"

VERSION = _read_local_version()

# ---------------------------------------------------------------------------
# Webviewer process state (module-level, shared across request threads)
# ---------------------------------------------------------------------------

_webviewer_proc: "subprocess.Popen | None" = None
_webviewer_lock = threading.Lock()

# Pending paste job — set by /trigger before firing AppleScript,
# consumed by Agentic-fm Paste via GET /pending.
# Shape: {"target": str, "auto_save": bool}
_pending_job: dict = {}
_pending_lock = threading.Lock()

_file_watch_thread: "threading.Thread | None" = None
_file_watch_stop_event: "threading.Event | None" = None
_file_watch_lock = threading.Lock()
_file_watch_condition = threading.Condition(_file_watch_lock)
_file_watch_state: dict = {
    "running": False,
    "path": "",
    "poll_interval": FILE_WATCH_POLL_INTERVAL_SECONDS,
    "start_at_end": True,
    "started_at": None,
    "last_checked_at": None,
    "last_change_at": None,
    "last_event_at": None,
    "offset": None,
    "file_exists": False,
    "revision": 0,
    "analyzer": {"type": "import_log"},
    "summary": {
        "events_total": 0,
        "errors_total": 0,
        "matches_by_rule": {},
        "errors_by_code": {},
        "current_import": {},
        "last_completed_import": {},
        "recent_imports": [],
        "imports_total": 0,
        "imports_with_errors": 0,
        "imports_without_errors": 0,
        "last_error": {},
    },
    "recent_events": [],
    "last_error": "",
}

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("companion_server")
SUBPROCESS_HEARTBEAT_SECONDS = 5


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clone_jsonable(data):
    return json.loads(json.dumps(data, ensure_ascii=False))


def _default_documents_dir() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
            if result == 0 and buffer.value:
                return buffer.value
        except Exception:
            pass

        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            return os.path.join(user_profile, "Documents")

    return os.path.expanduser("~/Documents")


def _split_initial_path(initial_path: str) -> tuple[str, str]:
    initial_path = os.path.expanduser(initial_path or "")
    initial_dir = ""
    initial_file = ""
    if initial_path:
        if os.path.isdir(initial_path):
            initial_dir = initial_path
        else:
            initial_dir = os.path.dirname(initial_path)
            initial_file = os.path.basename(initial_path)
    return initial_dir, initial_file


def _open_path_dialog_tk(selection_type: str, initial_path: str = "", title: str = "") -> str:
    import tkinter as tk
    from tkinter import filedialog

    initial_dir, initial_file = _split_initial_path(initial_path)

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    root.update()

    try:
        if selection_type == "directory":
            selected_path = filedialog.askdirectory(
                title=title or "Select folder",
                initialdir=initial_dir or _default_documents_dir(),
                parent=root,
                mustexist=True,
            )
        elif selection_type == "file":
            selected_path = filedialog.askopenfilename(
                title=title or "Select file",
                initialdir=initial_dir or _default_documents_dir(),
                initialfile=initial_file,
                parent=root,
                filetypes=[
                    ("Log and FileMaker files", "*.log *.fmp12"),
                    ("All files", "*"),
                ],
            )
        else:
            raise ValueError("selection_type must be file or directory")
    finally:
        root.destroy()

    return selected_path or ""


def _apple_script_quote(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _open_path_dialog_macos(selection_type: str, initial_path: str = "", title: str = "") -> str:
    initial_dir, _ = _split_initial_path(initial_path)
    if not initial_dir:
        initial_dir = _default_documents_dir()

    location_clause = ""
    if initial_dir and os.path.exists(initial_dir):
        location_clause = f' default location (POSIX file "{_apple_script_quote(initial_dir)}")'

    prompt = _apple_script_quote(title or ("Select folder" if selection_type == "directory" else "Select file"))
    if selection_type == "directory":
        choose_stmt = f'set chosenItem to choose folder with prompt "{prompt}"{location_clause}'
    elif selection_type == "file":
        choose_stmt = f'set chosenItem to choose file with prompt "{prompt}"{location_clause}'
    else:
        raise ValueError("selection_type must be file or directory")

    script = "\n".join(
        [
            "try",
            f"    {choose_stmt}",
            "    return POSIX path of chosenItem",
            "on error number -128",
            '    return ""',
            "end try",
        ]
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error_message = (result.stderr or result.stdout or "osascript path picker failed").strip()
        raise RuntimeError(error_message)
    return (result.stdout or "").strip()


def _powershell_single_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


def _open_path_dialog_windows(selection_type: str, initial_path: str = "", title: str = "") -> str:
    initial_dir, initial_file = _split_initial_path(initial_path)
    if not initial_dir:
        initial_dir = _default_documents_dir()

    if selection_type == "file":
        script = "\n".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
                f"$dialog.Title = '{_powershell_single_quote(title or 'Select file')}'",
                f"$dialog.InitialDirectory = '{_powershell_single_quote(initial_dir)}'",
                f"$dialog.FileName = '{_powershell_single_quote(initial_file)}'",
                "$dialog.Filter = 'Log and FileMaker files (*.log;*.fmp12)|*.log;*.fmp12|All files (*.*)|*.*'",
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.FileName }",
            ]
        )
    elif selection_type == "directory":
        script = "\n".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
                f"$dialog.Description = '{_powershell_single_quote(title or 'Select folder')}'",
                f"$dialog.SelectedPath = '{_powershell_single_quote(initial_dir)}'",
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }",
            ]
        )
    else:
        raise ValueError("selection_type must be file or directory")

    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error_message = (result.stderr or result.stdout or "PowerShell path picker failed").strip()
        raise RuntimeError(error_message)
    return (result.stdout or "").strip()


def _open_path_dialog(selection_type: str, initial_path: str = "", title: str = "") -> str:
    try:
        return _open_path_dialog_tk(selection_type, initial_path=initial_path, title=title)
    except (ImportError, ModuleNotFoundError):
        if sys.platform == "darwin":
            return _open_path_dialog_macos(selection_type, initial_path=initial_path, title=title)
        if sys.platform == "win32":
            return _open_path_dialog_windows(selection_type, initial_path=initial_path, title=title)
        raise RuntimeError("No native path picker available on this Python installation. Enter the path manually.")


def _new_file_watch_summary() -> dict:
    return {
        "events_total": 0,
        "errors_total": 0,
        "matches_by_rule": {},
        "errors_by_code": {},
        "current_import": {},
        "last_completed_import": {},
        "recent_imports": [],
        "imports_total": 0,
        "imports_with_errors": 0,
        "imports_without_errors": 0,
        "last_error": {},
    }


def _snapshot_file_watch_state() -> dict:
    with _file_watch_lock:
        return _clone_jsonable(_file_watch_state)


def _watch_results_payload_from_state(state: dict) -> dict:
    return {
        "running": state["running"],
        "path": state["path"],
        "poll_interval": state["poll_interval"],
        "start_at_end": state["start_at_end"],
        "started_at": state["started_at"],
        "file_exists": state["file_exists"],
        "revision": state.get("revision", 0),
        "analyzer": state["analyzer"],
        "summary": state["summary"],
        "recent_events": state["recent_events"],
        "last_change_at": state["last_change_at"],
        "last_event_at": state["last_event_at"],
        "last_error": state["last_error"],
        "platform": {
            "system": platform.system(),
            "python_platform": sys.platform,
        },
        "defaults": {
            "documents_dir": _default_documents_dir(),
        },
    }


def _current_watch_results_payload() -> dict:
    return _watch_results_payload_from_state(_snapshot_file_watch_state())


def _bump_file_watch_revision_locked() -> int:
    _file_watch_state["revision"] = int(_file_watch_state.get("revision", 0)) + 1
    _file_watch_condition.notify_all()
    return _file_watch_state["revision"]


def _normalize_analyzer_config(raw_analyzer) -> dict:
    if not raw_analyzer:
        return {"type": "import_log"}

    if isinstance(raw_analyzer, str):
        analyzer = {"type": raw_analyzer}
    elif isinstance(raw_analyzer, dict):
        analyzer = dict(raw_analyzer)
    else:
        raise ValueError("analyzer must be a string or object")

    analyzer_type = analyzer.get("type", "import_log")
    if analyzer_type in ("import_log", "import_log_unknown_attributes"):
        return {"type": analyzer_type}

    if analyzer_type != "regex":
        raise ValueError("analyzer.type must be import_log, import_log_unknown_attributes, or regex")

    rules = analyzer.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("regex analyzer requires a non-empty rules array")

    normalized_rules = []
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ValueError(f"regex rule #{index} must be an object")
        name = str(rule.get("name") or f"rule_{index}")
        pattern = rule.get("pattern") or rule.get("regex")
        if not pattern:
            raise ValueError(f"regex rule {name!r} is missing pattern")
        flags = str(rule.get("flags", ""))
        severity = str(rule.get("severity", "info"))
        re_flags = 0
        if "i" in flags.lower():
            re_flags |= re.IGNORECASE
        try:
            re.compile(pattern, re_flags)
        except re.error as exc:
            raise ValueError(f"regex rule {name!r} is invalid: {exc}") from exc
        normalized_rules.append(
            {
                "name": name,
                "pattern": pattern,
                "flags": flags,
                "severity": severity,
            }
        )

    return {"type": "regex", "rules": normalized_rules}


def _build_analyzer_runtime(analyzer_config: dict) -> dict:
    if analyzer_config["type"] != "regex":
        return analyzer_config

    runtime_rules = []
    for rule in analyzer_config["rules"]:
        re_flags = 0
        if "i" in rule.get("flags", "").lower():
            re_flags |= re.IGNORECASE
        runtime_rules.append(
            {
                "name": rule["name"],
                "severity": rule["severity"],
                "compiled": re.compile(rule["pattern"], re_flags),
            }
        )
    return {"type": "regex", "rules": runtime_rules}


def _record_watch_event_locked(event: dict):
    summary = _file_watch_state["summary"]
    summary["events_total"] += 1
    if event.get("severity") == "error":
        summary["errors_total"] += 1
        summary["last_error"] = event
        code = str(event.get("code", "")).strip()
        if code:
            summary["errors_by_code"][code] = summary["errors_by_code"].get(code, 0) + 1
        current_import = summary.get("current_import") or {}
        if current_import:
            current_import["error_count"] = int(current_import.get("error_count", 0)) + 1
            summary["current_import"] = current_import

    rule = event.get("rule", "unknown")
    summary["matches_by_rule"][rule] = summary["matches_by_rule"].get(rule, 0) + 1

    _file_watch_state["last_event_at"] = event.get("detected_at")
    _file_watch_state["recent_events"].append(event)
    if len(_file_watch_state["recent_events"]) > FILE_WATCH_MAX_EVENTS:
        _file_watch_state["recent_events"] = _file_watch_state["recent_events"][-FILE_WATCH_MAX_EVENTS:]
    _bump_file_watch_revision_locked()


def _parse_import_log_line(line: str) -> dict | None:
    parts = line.split("\t", 3)
    if len(parts) != 4:
        return None

    timestamp_str, source, code, message = (part.strip() for part in parts)
    if not IMPORT_LOG_TIMESTAMP_RE.match(timestamp_str):
        return None

    parsed = {
        "timestamp": timestamp_str,
        "source": source,
        "code": code,
        "message": message,
        "raw_line": line,
    }

    source_parts = source.split("::")
    if len(source_parts) >= 1:
        parsed["script_name"] = source_parts[0]
    if len(source_parts) >= 2:
        parsed["script_line"] = source_parts[1]
    if len(source_parts) >= 3:
        parsed["step_name"] = source_parts[2]
    if len(source_parts) >= 4:
        parsed["attribute_name"] = source_parts[3]

    unknown_match = IMPORT_LOG_UNKNOWN_VALUE_RE.search(message)
    if unknown_match:
        parsed["unknown_value"] = unknown_match.group(1)

    imported_match = re.search(r"script steps imported\s*:\s*(\d+)", message)
    if imported_match:
        parsed["imported_steps"] = int(imported_match.group(1))

    return parsed


def _classify_import_log_issue(parsed: dict) -> dict:
    message = parsed["message"]
    code = parsed["code"]

    if "unknown_value" in parsed:
        return {
            "rule": "unknown_attribute_value",
            "category": "unknown_attribute_value",
            "attribute_value": parsed.get("unknown_value", ""),
        }

    attribute_missing_match = IMPORT_LOG_ATTRIBUTE_MISSING_RE.match(message)
    if attribute_missing_match:
        return {
            "rule": "missing_attribute",
            "category": "missing_attribute",
            "attribute_name": attribute_missing_match.group(1),
        }

    field_missing_match = IMPORT_LOG_FIELD_MISSING_RE.match(message)
    if field_missing_match:
        return {
            "rule": "missing_field",
            "category": "missing_field",
            "field_name": field_missing_match.group(1),
        }

    field_reference_missing_match = IMPORT_LOG_FIELD_REFERENCE_MISSING_RE.match(message)
    if field_reference_missing_match:
        return {
            "rule": "missing_field_reference",
            "category": "missing_field_reference",
            "field_reference": field_reference_missing_match.group(1),
        }

    layout_missing_match = IMPORT_LOG_LAYOUT_MISSING_RE.match(message)
    if layout_missing_match:
        return {
            "rule": "missing_layout",
            "category": "missing_layout",
            "layout_name": layout_missing_match.group(1),
        }

    script_missing_match = IMPORT_LOG_SCRIPT_MISSING_RE.match(message)
    if script_missing_match:
        return {
            "rule": "missing_script",
            "category": "missing_script",
            "script_reference": script_missing_match.group(1),
        }

    function_missing_match = IMPORT_LOG_FUNCTION_MISSING_RE.match(message)
    if function_missing_match:
        return {
            "rule": "missing_function",
            "category": "missing_function",
            "function_reference": function_missing_match.group(1),
        }

    table_missing_match = IMPORT_LOG_TABLE_MISSING_RE.match(message)
    if table_missing_match:
        return {
            "rule": "missing_table_reference",
            "category": "missing_table_reference",
            "table_reference": table_missing_match.group(1),
        }

    if IMPORT_LOG_UNKNOWN_ERROR_RE.match(message):
        return {
            "rule": "unknown_error",
            "category": "unknown_error",
        }

    return {
        "rule": f"import_log_code_{code}",
        "category": "other",
    }


def _import_issue_label(issue: dict) -> str:
    labels = {
        "unknown_attribute_value": "Unknown attribute value",
        "missing_attribute": "Missing attribute",
        "missing_field": "Missing field",
        "missing_field_reference": "Missing field reference",
        "missing_layout": "Missing layout",
        "missing_script": "Missing script",
        "missing_function": "Missing function",
        "missing_table_reference": "Missing table reference",
        "unknown_error": "Unknown error",
        "other": "Other error",
    }
    return labels.get(issue.get("category", "other"), "Import error")


def _set_current_import_script_name(current_import: dict, parsed: dict):
    script_name = str(parsed.get("script_name", "") or "").strip()
    source = str(parsed.get("source", "") or "").strip()
    database_name = str(current_import.get("database_name", "") or "").strip()
    if not script_name and source and "::" not in source and source != database_name:
        script_name = source
    if script_name:
        current_import["script_name"] = script_name


def _append_import_error_locked(current_import: dict, parsed: dict, issue: dict):
    errors = current_import.setdefault("errors", [])
    errors.append(
        {
            "timestamp": parsed["timestamp"],
            "script_name": parsed.get("script_name", ""),
            "line_number": parsed.get("script_line", ""),
            "step_name": parsed.get("step_name", ""),
            "attribute_name": parsed.get("attribute_name", ""),
            "code": parsed["code"],
            "rule": issue["rule"],
            "category": issue["category"],
            "label": _import_issue_label(issue),
            "message": parsed["message"],
            "raw_line": parsed["raw_line"],
        }
    )


def _append_recent_import_locked(import_summary: dict):
    recent_imports = _file_watch_state["summary"].setdefault("recent_imports", [])
    recent_imports.append(import_summary)
    if len(recent_imports) > FILE_WATCH_MAX_IMPORTS:
        _file_watch_state["summary"]["recent_imports"] = recent_imports[-FILE_WATCH_MAX_IMPORTS:]


def _record_import_lifecycle_event_locked(parsed: dict, rule: str, severity: str, message: str | None = None, **extra) -> dict:
    event = {
        "detected_at": _utc_now_iso(),
        "event_type": "import_log_lifecycle",
        "rule": rule,
        "severity": severity,
        "timestamp": parsed["timestamp"],
        "source": parsed["source"],
        "code": parsed["code"],
        "message": message if message is not None else parsed["message"],
        "script_name": parsed.get("script_name", ""),
        "script_line": parsed.get("script_line", ""),
        "line_number": parsed.get("script_line", ""),
        "step_name": parsed.get("step_name", ""),
        "attribute_name": parsed.get("attribute_name", ""),
        "unknown_value": parsed.get("unknown_value", ""),
        "raw_line": parsed["raw_line"],
    }
    event.update(extra)
    _record_watch_event_locked(event)
    return event


def _process_import_log_line(line: str, analyzer_type: str) -> list[dict]:
    parsed = _parse_import_log_line(line)
    if not parsed:
        return []

    events = []
    with _file_watch_lock:
        summary = _file_watch_state["summary"]
        message = parsed["message"]
        code = parsed["code"]
        source = parsed["source"]

        if message == "Import of script steps from clipboard started":
            summary["current_import"] = {
                "source": source,
                "database_name": source,
                "started_at": parsed["timestamp"],
                "error_count": 0,
                "errors": [],
            }
            events.append(
                _record_import_lifecycle_event_locked(
                    parsed,
                    "import_started",
                    "info",
                    import_status="running",
                )
            )

        if "imported_steps" in parsed:
            current_import = summary.get("current_import") or {}
            if not current_import:
                current_import = {
                    "source": source,
                    "database_name": source,
                    "started_at": parsed["timestamp"],
                    "error_count": 0,
                    "errors": [],
                }
            _set_current_import_script_name(current_import, parsed)
            current_import["imported_steps"] = parsed["imported_steps"]
            summary["current_import"] = current_import
            events.append(
                _record_import_lifecycle_event_locked(
                    parsed,
                    "import_steps_imported",
                    "info",
                    import_status="running",
                    imported_steps=parsed["imported_steps"],
                )
            )

        if message == "Import completed":
            current_import = dict(summary.get("current_import") or {})
            completed_import = {}
            if current_import:
                current_import["completed_at"] = parsed["timestamp"]
                current_import["status"] = "with_errors" if int(current_import.get("error_count", 0)) > 0 else "ok"
                summary["last_completed_import"] = current_import
                summary["imports_total"] = int(summary.get("imports_total", 0)) + 1
                if current_import["status"] == "with_errors":
                    summary["imports_with_errors"] = int(summary.get("imports_with_errors", 0)) + 1
                else:
                    summary["imports_without_errors"] = int(summary.get("imports_without_errors", 0)) + 1
                _append_recent_import_locked(current_import)
                completed_import = current_import
            summary["current_import"] = {}
            events.append(
                _record_import_lifecycle_event_locked(
                    parsed,
                    "import_completed",
                    "warn" if int(completed_import.get("error_count", 0)) > 0 else "info",
                    message=(
                        f"Import completed with {int(completed_import.get('error_count', 0))} errors."
                        if completed_import
                        else "Import completed."
                    ),
                    source=completed_import.get("source", source),
                    database_name=completed_import.get("database_name", source),
                    script_name=completed_import.get("script_name", ""),
                    import_status=completed_import.get("status", "unknown"),
                    imported_steps=completed_import.get("imported_steps"),
                    error_count=int(completed_import.get("error_count", 0)),
                )
            )

        if code == "0":
            return events

        if analyzer_type == "import_log_unknown_attributes" and "unknown_value" not in parsed:
            return events

        issue = _classify_import_log_issue(parsed)
        current_import = summary.get("current_import") or {}
        if current_import:
            _set_current_import_script_name(current_import, parsed)
            _append_import_error_locked(current_import, parsed, issue)
            summary["current_import"] = current_import
        event = {
            "detected_at": _utc_now_iso(),
            "event_type": "import_log_issue",
            "rule": issue["rule"],
            "severity": "error",
            "timestamp": parsed["timestamp"],
            "source": source,
            "code": code,
            "category": issue["category"],
            "label": _import_issue_label(issue),
            "message": message,
            "script_name": parsed.get("script_name", ""),
            "script_line": parsed.get("script_line", ""),
            "line_number": parsed.get("script_line", ""),
            "step_name": parsed.get("step_name", ""),
            "attribute_name": parsed.get("attribute_name", ""),
            "unknown_value": parsed.get("unknown_value", ""),
            "raw_line": parsed["raw_line"],
        }
        event.update(issue)
        _record_watch_event_locked(event)
        events.append(event)

    return events


def _process_regex_line(line: str, analyzer_runtime: dict) -> list[dict]:
    events = []
    for rule in analyzer_runtime["rules"]:
        match = rule["compiled"].search(line)
        if not match:
            continue
        event = {
            "detected_at": _utc_now_iso(),
            "event_type": "regex_match",
            "rule": rule["name"],
            "severity": rule["severity"],
            "message": line,
            "groups": match.groupdict() or {str(index): value for index, value in enumerate(match.groups(), start=1)},
            "raw_line": line,
        }
        with _file_watch_lock:
            _record_watch_event_locked(event)
        events.append(event)
    return events


def _process_watch_lines(lines: list[str], analyzer_runtime: dict) -> list[dict]:
    events = []
    import_log_lines = []
    for line in lines:
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        if analyzer_runtime["type"] in ("import_log", "import_log_unknown_attributes"):
            if _parse_import_log_line(stripped):
                import_log_lines.append(stripped)
            elif import_log_lines:
                import_log_lines[-1] = f"{import_log_lines[-1]}\n{stripped}"
        elif analyzer_runtime["type"] == "regex":
            events.extend(_process_regex_line(stripped, analyzer_runtime))
    if analyzer_runtime["type"] in ("import_log", "import_log_unknown_attributes"):
        for import_log_line in import_log_lines:
            events.extend(_process_import_log_line(import_log_line, analyzer_runtime["type"]))
    return events


def _watch_file_loop(path: str, poll_interval: float, start_at_end: bool, analyzer_runtime: dict, stop_event: threading.Event):
    carryover = ""

    while True:
        if stop_event.wait(poll_interval):
            return

        file_exists = os.path.exists(path)
        now = _utc_now_iso()
        with _file_watch_lock:
            file_exists_changed = _file_watch_state["file_exists"] != file_exists
            _file_watch_state["last_checked_at"] = now
            _file_watch_state["file_exists"] = file_exists
            if file_exists_changed:
                _bump_file_watch_revision_locked()

        if not file_exists:
            continue

        try:
            with _file_watch_lock:
                current_offset = _file_watch_state["offset"]
            file_size = os.path.getsize(path)

            if current_offset is not None and file_size < current_offset:
                carryover = ""
                with _file_watch_lock:
                    _file_watch_state["offset"] = 0
                current_offset = 0
                log.info("File watch reset offset after truncation: %s", path)

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if current_offset is None:
                    if start_at_end:
                        f.seek(0, os.SEEK_END)
                        new_offset = f.tell()
                        with _file_watch_lock:
                            _file_watch_state["offset"] = new_offset
                        log.info("File watch attached to %s at end of file", path)
                        continue
                    current_offset = 0

                f.seek(current_offset)
                chunk = f.read()
                new_offset = f.tell()

            if new_offset == current_offset:
                continue

            text = carryover + chunk
            split_lines = text.splitlines(keepends=True)
            complete_lines = []
            carryover = ""
            for index, split_line in enumerate(split_lines):
                is_last = index == len(split_lines) - 1
                if is_last and not split_line.endswith(("\n", "\r")):
                    carryover = split_line
                else:
                    complete_lines.append(split_line)

            with _file_watch_lock:
                _file_watch_state["offset"] = new_offset
                _file_watch_state["last_change_at"] = now
                _bump_file_watch_revision_locked()

            events = _process_watch_lines(complete_lines, analyzer_runtime)
            for event in events:
                if event["event_type"] == "import_log_issue":
                    log.warning(
                        "File watch match [%s]: script=%s line=%s step=%s attribute=%s value=%s message=%s",
                        event["rule"],
                        event.get("script_name", ""),
                        event.get("script_line", ""),
                        event.get("step_name", ""),
                        event.get("attribute_name", ""),
                        event.get("unknown_value", ""),
                        event.get("message", ""),
                    )
                else:
                    log.warning(
                        "File watch match [%s]: %s",
                        event["rule"],
                        event.get("message", ""),
                    )
        except Exception as exc:
            with _file_watch_lock:
                _file_watch_state["last_error"] = str(exc)
                _bump_file_watch_revision_locked()
            log.warning("File watch error for %s: %s", path, exc)


def _stop_file_watch() -> bool:
    global _file_watch_thread, _file_watch_stop_event
    thread = None
    with _file_watch_lock:
        if _file_watch_stop_event is not None:
            _file_watch_stop_event.set()
        thread = _file_watch_thread

    if thread is not None:
        thread.join(timeout=2)

    with _file_watch_lock:
        was_running = _file_watch_state["running"]
        _file_watch_thread = None
        _file_watch_stop_event = None
        _file_watch_state["running"] = False
        _bump_file_watch_revision_locked()

    return was_running


def _start_file_watch(path: str, poll_interval: float, start_at_end: bool, analyzer_config: dict) -> dict:
    global _file_watch_thread, _file_watch_stop_event

    _stop_file_watch()

    expanded_path = os.path.expanduser(path)
    analyzer_runtime = _build_analyzer_runtime(analyzer_config)
    initial_offset = None
    if os.path.exists(expanded_path):
        with open(expanded_path, "r", encoding="utf-8", errors="replace") as f:
            if start_at_end:
                f.seek(0, os.SEEK_END)
                initial_offset = f.tell()
            else:
                initial_offset = 0

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_watch_file_loop,
        args=(expanded_path, poll_interval, start_at_end, analyzer_runtime, stop_event),
        daemon=True,
    )

    with _file_watch_lock:
        _file_watch_stop_event = stop_event
        _file_watch_thread = thread
        _file_watch_state["running"] = True
        _file_watch_state["path"] = expanded_path
        _file_watch_state["poll_interval"] = poll_interval
        _file_watch_state["start_at_end"] = start_at_end
        _file_watch_state["started_at"] = _utc_now_iso()
        _file_watch_state["last_checked_at"] = None
        _file_watch_state["last_change_at"] = None
        _file_watch_state["last_event_at"] = None
        _file_watch_state["offset"] = initial_offset
        _file_watch_state["file_exists"] = os.path.exists(expanded_path)
        _file_watch_state["revision"] = 0
        _file_watch_state["analyzer"] = _clone_jsonable(analyzer_config)
        _file_watch_state["summary"] = _new_file_watch_summary()
        _file_watch_state["recent_events"] = []
        _file_watch_state["last_error"] = ""
        _bump_file_watch_revision_locked()

    thread.start()
    return _snapshot_file_watch_state()


def _resolve_import_log_path(payload: dict) -> dict:
    explicit_path = payload.get("import_log_path", "")
    if explicit_path:
        return {
            "location": "explicit",
            "path": os.path.expanduser(explicit_path),
        }

    database_path = payload.get("database_path", "")
    database_dir = payload.get("database_dir", "")
    location = str(payload.get("location", "") or payload.get("mode", "")).strip().lower()
    if not location:
        location = "local" if database_path or database_dir else "server"

    if location == "server":
        documents_dir = os.path.expanduser(payload.get("documents_dir") or _default_documents_dir())
        return {
            "location": "server",
            "path": os.path.join(documents_dir, "Import.log"),
        }

    if location == "local":
        local_root = database_dir or database_path
        if not local_root:
            raise ValueError("database_path or database_dir is required when location is local")
        expanded_local_root = os.path.expanduser(local_root)
        if database_dir:
            resolved_database_dir = expanded_local_root
        elif expanded_local_root.endswith(os.sep) or os.path.isdir(expanded_local_root):
            resolved_database_dir = expanded_local_root
        elif expanded_local_root.lower().endswith(".fmp12"):
            resolved_database_dir = os.path.dirname(expanded_local_root)
        elif "." not in os.path.basename(expanded_local_root):
            resolved_database_dir = expanded_local_root
        else:
            resolved_database_dir = os.path.dirname(expanded_local_root)
        if not resolved_database_dir:
            resolved_database_dir = os.getcwd()
        return {
            "location": "local",
            "path": os.path.join(resolved_database_dir, "Import.log"),
        }

    raise ValueError("location must be server or local")


def _build_watch_ui_html() -> str:
    ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "companion_watch_ui.html")
    with open(ui_path, "r", encoding="utf-8") as f:
        return f.read()


def _stream_pipe(pipe, level, prefix, output_buffer, state):
    """Copy a subprocess pipe to the logger in real time while buffering it."""
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            output_buffer.append(line)
            with state["lock"]:
                state["last_output_at"] = time.monotonic()
            level("%s%s", prefix, line.rstrip("\n"))
    finally:
        pipe.close()


def _run_command_streaming(cmd, *, cwd, env, label):
    """Run a command, stream its output to the server log, and capture it."""
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state = {"last_output_at": time.monotonic(), "lock": threading.Lock()}

    stdout_thread = threading.Thread(
        target=_stream_pipe,
        args=(process.stdout, log.info, f"[{label} stdout] ", stdout_chunks, state),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_pipe,
        args=(process.stderr, log.warning, f"[{label} stderr] ", stderr_chunks, state),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    last_heartbeat_at = time.monotonic()
    while True:
        try:
            return_code = process.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            with state["lock"]:
                silence_for = now - state["last_output_at"]
            if (
                silence_for >= SUBPROCESS_HEARTBEAT_SECONDS
                and now - last_heartbeat_at >= SUBPROCESS_HEARTBEAT_SECONDS
            ):
                log.info(
                    "%s still running... (%ds since last output)",
                    label,
                    int(silence_for),
                )
                last_heartbeat_at = now

    stdout_thread.join()
    stderr_thread.join()

    return {
        "returncode": return_code,
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
    }


# ---------------------------------------------------------------------------
# Threading HTTP server (handles concurrent requests)
# ---------------------------------------------------------------------------

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with thread-per-request concurrency."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class CompanionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        """Route access log through the standard logger."""
        log.info("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._handle_health()
        elif path == "/pending":
            self._handle_pending_get()
        elif path == "/watch/status":
            self._handle_watch_status()
        elif path == "/watch/results":
            self._handle_watch_results()
        elif path == "/watch/stream":
            self._handle_watch_stream()
        elif path == "/watch/ui":
            self._handle_watch_ui()
        elif path == "/webviewer/status":
            self._handle_webviewer_status()
        else:
            self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/explode":
            self._handle_explode()
        elif path == "/context":
            self._handle_context()
        elif path == "/debug":
            self._handle_debug()
        elif path == "/clipboard":
            self._handle_clipboard()
        elif path == "/trigger":
            self._handle_trigger()
        elif path == "/pending":
            self._handle_pending_post()
        elif path == "/watch/import-log/start":
            self._handle_watch_import_log_start()
        elif path == "/watch/pick-path":
            self._handle_watch_pick_path()
        elif path == "/watch/stop":
            self._handle_watch_stop()
        elif path == "/webviewer/start":
            self._handle_webviewer_start()
        elif path == "/webviewer/stop":
            self._handle_webviewer_stop()
        elif path == "/webviewer/push":
            self._handle_webviewer_push()
        elif self.path == "/lint":
            self._handle_lint()
        else:
            self._send_json({"error": "Not found"}, status=404)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_health(self):
        self._send_json({"status": "ok", "version": VERSION})

    def _handle_watch_status(self):
        self._send_json(_snapshot_file_watch_state())

    def _handle_watch_results(self):
        self._send_json(_current_watch_results_payload())

    def _handle_watch_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_revision = -1
        try:
            while True:
                with _file_watch_lock:
                    current_revision = _file_watch_state.get("revision", 0)
                    if current_revision == last_revision:
                        _file_watch_condition.wait(timeout=15)
                        current_revision = _file_watch_state.get("revision", 0)
                    payload = _watch_results_payload_from_state(_clone_jsonable(_file_watch_state))

                if current_revision == last_revision:
                    self._send_sse_event("ping", {"timestamp": _utc_now_iso()})
                    continue

                self._send_sse_event("results", payload, event_id=current_revision)
                last_revision = current_revision
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout, OSError):
            return

    def _handle_watch_ui(self):
        self._send_html(_build_watch_ui_html())

    def _handle_watch_import_log_start(self):
        try:
            body = self._read_body()
            payload = json.loads(body) if body else {}
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "exit_code": -1, "error": f"Invalid request: {exc}"}, status=400)
            return

        try:
            poll_interval = float(payload.get("poll_interval", FILE_WATCH_POLL_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            self._send_json({"success": False, "exit_code": -1, "error": "poll_interval must be a number"}, status=400)
            return

        if poll_interval <= 0:
            self._send_json({"success": False, "exit_code": -1, "error": "poll_interval must be greater than 0"}, status=400)
            return

        start_at_end = bool(payload.get("start_at_end", True))

        try:
            resolved = _resolve_import_log_path(payload)
            analyzer_config = _normalize_analyzer_config(payload.get("analyzer", "import_log"))
            state = _start_file_watch(resolved["path"], poll_interval, start_at_end, analyzer_config)
        except ValueError as exc:
            self._send_json({"success": False, "exit_code": -1, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            log.exception("Failed to start Import.log watch: %s", exc)
            self._send_json({"success": False, "exit_code": -1, "error": str(exc)}, status=500)
            return

        log.info(
            "Import.log watch started: location=%s path=%s analyzer=%s",
            resolved["location"],
            state["path"],
            state["analyzer"]["type"],
        )
        self._send_json(
            {
                "success": True,
                "location": resolved["location"],
                "resolved_path": state["path"],
                "watch": state,
            }
        )

    def _handle_watch_pick_path(self):
        try:
            body = self._read_body()
            payload = json.loads(body) if body else {}
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        selection_type = str(payload.get("selection_type", "file") or "file").strip().lower()
        initial_path = str(payload.get("initial_path", "") or "")
        title = str(payload.get("title", "") or "")

        try:
            selected_path = _open_path_dialog(selection_type, initial_path=initial_path, title=title)
        except ValueError as exc:
            self._send_json({"success": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            log.exception("Failed to open watch path picker: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)
            return

        self._send_json(
            {
                "success": True,
                "selected_path": selected_path,
                "cancelled": not bool(selected_path),
            }
        )

    def _handle_watch_stop(self):
        try:
            self._read_body()
        except OSError:
            pass
        was_running = _stop_file_watch()
        if was_running:
            log.info("File watch stopped")
        self._send_json({"success": True, "status": "stopped" if was_running else "not_running"})

    def _handle_explode(self):
        # Read and parse request body
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json(
                {"success": False, "exit_code": -1, "error": f"Invalid request: {exc}"},
                status=400,
            )
            return

        # Validate required fields
        missing = [
            f for f in ("solution_name", "export_file_path", "repo_path")
            if not payload.get(f)
        ]
        if missing:
            self._send_json(
                {
                    "success": False,
                    "exit_code": -1,
                    "error": f"Missing required fields: {', '.join(missing)}",
                },
                status=400,
            )
            return

        solution_name = payload["solution_name"]
        export_file_path = payload["export_file_path"]
        repo_path = payload["repo_path"]
        exploder_bin_path = payload.get("exploder_bin_path", "")

        # Expand ~ in paths
        repo_path = os.path.expanduser(repo_path)
        export_file_path = os.path.expanduser(export_file_path)

        # Build environment for subprocess
        env = os.environ.copy()
        if exploder_bin_path:
            env["FM_XML_EXPLODER_BIN"] = os.path.expanduser(exploder_bin_path)

        # Build command: {repo_path}/fmparse.sh -s "{solution_name}" "{export_file_path}"
        fmparse = os.path.join(repo_path, "fmparse.sh")
        cmd = [fmparse, "-s", solution_name, export_file_path]

        log.info(
            "Running fmparse.sh: solution=%r export=%r cwd=%r",
            solution_name,
            export_file_path,
            repo_path,
        )

        try:
            result = _run_command_streaming(
                cmd,
                cwd=repo_path,
                env=env,
                label="fmparse.sh",
            )

            success = result["returncode"] == 0
            response = {
                "success": success,
                "exit_code": result["returncode"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
            status = 200 if success else 500

            log.info(
                "fmparse.sh exited with code %d", result["returncode"]
            )

        except Exception as exc:
            log.exception("Exception running fmparse.sh: %s", exc)
            response = {
                "success": False,
                "exit_code": -1,
                "error": str(exc),
            }
            status = 500

        self._send_json(response, status=status)

    def _handle_context(self):
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        missing = [f for f in ("repo_path", "context") if not payload.get(f)]
        if missing:
            self._send_json(
                {"success": False, "error": f"Missing required fields: {', '.join(missing)}"},
                status=400,
            )
            return

        repo_path = os.path.expanduser(payload["repo_path"])
        context = payload["context"]

        # Accept context as a pre-serialised string or a parsed object
        if isinstance(context, str):
            try:
                json.loads(context)  # validate only
            except ValueError as exc:
                self._send_json({"success": False, "error": f"Invalid context JSON: {exc}"}, status=400)
                return
            context_str = context
        else:
            context_str = json.dumps(context, indent=2, ensure_ascii=False)

        output_path = os.path.join(repo_path, "agent", "CONTEXT.json")

        # Check context_version and warn if outdated
        CONTEXT_VERSION_CURRENT = 2
        try:
            ctx_data = json.loads(context_str) if isinstance(context_str, str) else context
            ctx_version = ctx_data.get("context_version")
            if ctx_version is None or ctx_version < CONTEXT_VERSION_CURRENT:
                log.warning(
                    "CONTEXT.json has context_version=%s (current is %s). "
                    "Update the Context() custom function in your solution.",
                    ctx_version, CONTEXT_VERSION_CURRENT,
                )
        except (ValueError, TypeError, AttributeError):
            pass

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(context_str)
            log.info("CONTEXT.json written to %s", output_path)
            self._send_json({"success": True, "path": output_path})
        except Exception as exc:
            log.exception("Failed to write CONTEXT.json: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_pending_get(self):
        """Return and clear the pending paste job."""
        global _pending_job
        with _pending_lock:
            job = _pending_job.copy()
            _pending_job = {}
        self._send_json(job)

    def _handle_pending_post(self):
        """Set the pending paste job."""
        global _pending_job
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return
        target = payload.get("target", "")
        auto_save = bool(payload.get("auto_save", False))
        select_all = bool(payload.get("select_all", True))
        with _pending_lock:
            _pending_job = {"target": target, "auto_save": auto_save, "select_all": select_all}
        log.info("Pending job set: target=%r auto_save=%s select_all=%s", target, auto_save, select_all)
        self._send_json({"success": True})

    def _handle_trigger(self):
        """
        Trigger FM Pro to perform a named script via osascript.

        Payload: { "fm_app_name": "FileMaker Pro — ...", "script": "name", "parameter": "..." }
        Returns: { "success": bool, "stdout": str, "stderr": str }
        """
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        fm_app = payload.get("fm_app_name", "FileMaker Pro")
        script = payload.get("script", "")
        parameter = payload.get("parameter", "")
        target_file = payload.get("target_file", "")

        def as_str(s):
            """Escape double-quotes for use inside an AppleScript double-quoted string."""
            return s.replace("\\", "\\\\").replace('"', '\\"')

        # raw_applescript bypasses the FM do script path — no script name required
        raw = payload.get("raw_applescript", "")
        if raw:
            applescript = raw
        elif not script:
            self._send_json({"success": False, "error": "Missing required field: script"}, status=400)
            return
        else:
            # Store the target and auto_save flag in the pending slot so the
            # FM script can retrieve them via GET /pending (AppleScript parameter
            # passing via "given parameter:" is unreliable in FM Pro 22).
            if parameter:
                global _pending_job
                auto_save = bool(payload.get("auto_save", False))
                select_all = bool(payload.get("select_all", True))
                with _pending_lock:
                    _pending_job = {"target": parameter, "auto_save": auto_save, "select_all": select_all}
                log.info("Pending job set: target=%r auto_save=%s select_all=%s", parameter, auto_save, select_all)

            # When target_file is provided, address the specific FM document
            # by name instead of positional document 1. This ensures the
            # correct file is targeted when multiple files are open.
            if target_file:
                doc_clause = f'tell (first document whose name contains "{as_str(target_file)}")'
                log.info("Trigger: targeting document %r", target_file)
            else:
                doc_clause = "tell document 1"
                log.info("Trigger: no target_file — using document 1")

            applescript = (
                f'tell application "{as_str(fm_app)}"\n'
                f'    activate\n'
                f'    {doc_clause}\n'
                f'        do script "{as_str(script)}"\n'
                f'    end tell\n'
                f'end tell'
            )

        try:
            result = subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True, text=True, timeout=30
            )
            success = result.returncode == 0
            if success:
                log.info("Trigger: ran '%s' in %s", script, fm_app)
            else:
                log.error("Trigger failed: %s", result.stderr)
            self._send_json({
                "success": success,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            })
        except subprocess.TimeoutExpired:
            self._send_json({"success": False, "error": "osascript timed out after 30s"}, status=500)
        except FileNotFoundError:
            self._send_json({"success": False, "error": "osascript not found — is this macOS?"}, status=500)
        except Exception as exc:
            log.exception("Trigger handler error: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_clipboard(self):
        """Accept XML content and write it to the macOS clipboard via clipboard.py."""
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        xml = payload.get("xml", "")
        if not xml:
            self._send_json({"success": False, "error": "Missing required field: xml"}, status=400)
            return

        import tempfile
        script_dir = os.path.dirname(os.path.abspath(__file__))
        clipboard_py = os.path.join(script_dir, "clipboard.py")

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(xml)
                tmp_path = tmp.name

            result = subprocess.run(
                ["python3", clipboard_py, "write", tmp_path],
                capture_output=True, text=True
            )
            os.unlink(tmp_path)

            if result.returncode == 0:
                log.info("Clipboard write succeeded")
                self._send_json({"success": True})
            else:
                log.error("Clipboard write failed: %s", result.stderr)
                self._send_json(
                    {"success": False, "error": result.stderr or "clipboard.py returned non-zero"},
                    status=500,
                )
        except Exception as exc:
            log.exception("Clipboard handler error: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_debug(self):
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        # Resolve repo root from script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(os.path.dirname(script_dir))
        debug_dir = os.path.join(repo_root, "agent", "debug")
        os.makedirs(debug_dir, exist_ok=True)
        output_path = os.path.join(debug_dir, "output.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        log.info("Debug output written to %s", output_path)
        self._send_json({"success": True, "path": output_path})

    def _handle_webviewer_status(self):
        global _webviewer_proc
        with _webviewer_lock:
            running = _webviewer_proc is not None and _webviewer_proc.poll() is None
        self._send_json({"running": running})

    def _handle_webviewer_start(self):
        global _webviewer_proc
        try:
            body = self._read_body()
            payload = json.loads(body) if body else {}
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        repo_path = payload.get("repo_path", "")
        if not repo_path:
            self._send_json({"success": False, "error": "Missing required field: repo_path"}, status=400)
            return

        repo_path = os.path.expanduser(repo_path)
        webviewer_path = os.path.join(repo_path, "webviewer")

        with _webviewer_lock:
            if _webviewer_proc is not None and _webviewer_proc.poll() is None:
                self._send_json({"success": True, "status": "already_running"})
                return

            try:
                proc = subprocess.Popen(
                    ["npm", "run", "dev"],
                    cwd=webviewer_path,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                _webviewer_proc = proc
                log.info("Started webviewer (pid=%d) in %s", proc.pid, webviewer_path)
                self._send_json({"success": True, "status": "started", "pid": proc.pid})
            except Exception as exc:
                log.exception("Failed to start webviewer: %s", exc)
                self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_webviewer_stop(self):
        global _webviewer_proc
        with _webviewer_lock:
            if _webviewer_proc is None or _webviewer_proc.poll() is not None:
                self._send_json({"success": True, "status": "not_running"})
                return

            try:
                pgid = os.getpgid(_webviewer_proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                _webviewer_proc = None
                log.info("Stopped webviewer (process group %d)", pgid)
                self._send_json({"success": True, "status": "stopped"})
            except Exception as exc:
                log.exception("Failed to stop webviewer: %s", exc)
                self._send_json({"success": False, "error": str(exc)}, status=500)

    def _handle_webviewer_push(self):
        """
        Write an agent output payload for the webviewer to pick up via polling.

        Payload: { "type": "preview"|"diff"|"result"|"diagram"|"layout-preview", "content": "...", "before": "...", "styles": "...", "repo_path": "..." }
        Returns: { "success": bool }
        """
        try:
            body = self._read_body()
            payload = json.loads(body)
        except (ValueError, OSError) as exc:
            self._send_json({"success": False, "error": f"Invalid request: {exc}"}, status=400)
            return

        payload_type = payload.get("type", "")
        if payload_type not in ("preview", "diff", "result", "diagram", "layout-preview"):
            self._send_json({"success": False, "error": f"Unknown type: {payload_type!r}. Must be preview, diff, result, diagram, or layout-preview."}, status=400)
            return

        repo_path = payload.get("repo_path", "")
        if not repo_path:
            self._send_json({"success": False, "error": "Missing required field: repo_path"}, status=400)
            return

        repo_path = os.path.expanduser(repo_path)
        output_path = os.path.join(repo_path, "agent", "config", ".agent-output.json")

        import time
        output = {
            "type": payload_type,
            "content": payload.get("content", ""),
            "before": payload.get("before", ""),
            "timestamp": time.time(),
        }
        # Include optional styles field for layout-preview payloads
        if payload.get("styles"):
            output["styles"] = payload["styles"]

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            log.info("Agent output written to %s (type=%s)", output_path, payload_type)
            self._send_json({"success": True, "path": output_path})
        except Exception as exc:
            log.exception("Failed to write agent output: %s", exc)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _handle_lint(self):
        """Lint FileMaker code via FMLint engine.

        POST /lint
        Body: { "content": "...", "format": "xml"|"hr"|null, "tier": 1|2|3|null,
                "disable": ["N003", ...] }
        Returns: LintResult as JSON
        """
        try:
            body = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return

        content = body.get("content", "")
        if not content:
            self._send_json({"error": "Missing 'content' field"}, status=400)
            return

        fmt = body.get("format")
        tier = body.get("tier")
        disabled = body.get("disable", [])

        try:
            # Resolve project root from this script's location
            here = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.join(here, "..", "..")

            sys.path.insert(0, project_root)
            from agent.fmlint import lint

            config = {}
            if disabled:
                config["disable"] = disabled
            if tier is not None:
                config["max_tier"] = tier

            result = lint(
                content,
                fmt=fmt,
                project_root=project_root,
                config=config,
            )
            self._send_json(result.to_dict())
        except Exception as e:
            logging.exception("FMLint error")
            self._send_json({"error": str(e)}, status=500)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_body: str, status: int = 200):
        body = html_body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_event(self, event_name: str, data, event_id=None):
        if isinstance(data, str):
            payload = data
        else:
            payload = json.dumps(data, ensure_ascii=False)
        chunks = []
        if event_id is not None:
            chunks.append(f"id: {event_id}\n")
        if event_name:
            chunks.append(f"event: {event_name}\n")
        for line in payload.splitlines() or [""]:
            chunks.append(f"data: {line}\n")
        chunks.append("\n")
        self.wfile.write("".join(chunks).encode("utf-8"))
        self.wfile.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _check_for_updates():
    """Fetch the remote version.txt and warn if a newer version is available."""
    try:
        with urllib.request.urlopen(REMOTE_VERSION_URL, timeout=5) as resp:
            remote = resp.read().decode("utf-8").strip()
        if remote and remote != VERSION:
            local_parts = tuple(int(x) for x in VERSION.split(".") if x.isdigit())
            remote_parts = tuple(int(x) for x in remote.split(".") if x.isdigit())
            if remote_parts > local_parts:
                log.warning(
                    "A new version is available: v%s (you have v%s). "
                    "Run 'git pull --ff-only' in your agentic-fm folder to update, "
                    "then restart the server. See UPDATES.md for details.",
                    remote,
                    VERSION,
                )
    except Exception:
        pass  # No network, rate-limited, etc. — fail silently


def parse_args():
    parser = argparse.ArgumentParser(
        description="agentic-fm companion server — exposes fmparse.sh over HTTP for FileMaker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    return parser.parse_args()


def _format_local_url(host: str, port: int, path: str = "") -> str:
    display_host = (host or "127.0.0.1").strip()
    if display_host in {"0.0.0.0", "::", ""}:
        display_host = "127.0.0.1"
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    normalized_path = path if path.startswith("/") or not path else f"/{path}"
    return f"http://{display_host}:{port}{normalized_path}"


def main():
    args = parse_args()
    requested_port = args.port

    server = ThreadingHTTPServer((BIND_HOST, requested_port), CompanionHandler)
    actual_port = int(server.server_address[1])
    watch_ui_url = _format_local_url(BIND_HOST, actual_port, "/watch/ui")

    log.info("companion_server v%s listening on %s:%d", VERSION, BIND_HOST, actual_port)
    log.info("Watch UI: %s", watch_ui_url)
    threading.Thread(target=_check_for_updates, daemon=True).start()
    log.info("Endpoints: GET /health  GET /pending  GET /watch/status  GET /watch/results  GET /watch/stream  GET /watch/ui  GET /webviewer/status  POST /explode  POST /context  POST /clipboard  POST /trigger  POST /debug  POST /pending  POST /watch/import-log/start  POST /watch/pick-path  POST /watch/stop  POST /webviewer/start  POST /webviewer/stop  POST /webviewer/push")
    log.info("Press Ctrl-C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        _stop_file_watch()
        server.server_close()
        with _webviewer_lock:
            if _webviewer_proc is not None and _webviewer_proc.poll() is None:
                try:
                    pgid = os.getpgid(_webviewer_proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    log.info("Stopped webviewer (process group %d)", pgid)
                except Exception as exc:
                    log.warning("Failed to stop webviewer on shutdown: %s", exc)


if __name__ == "__main__":
    main()
