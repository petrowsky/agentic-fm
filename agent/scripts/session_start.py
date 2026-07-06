#!/usr/bin/env python3
"""Unified session-start check for agentic-fm.

Runs every once-per-session startup check described in AGENTS.md's "Session
startup" section in a single command, and prints a compact, one-line-per-check
summary the agent (or a human) can act on:

  1. git       — pending commits on origin/main
  2. env       — platform + AppleScript availability (sandbox detection)
  3. companion — companion server health on :8765 + AgenticFM plug-in block
                 (usable / installed / absent)
  4. project   — PROJECT.md presence (local-only context)
  5. context   — CONTEXT.json presence, age, and task description

Replaces four-plus separate round-trips at session start with one:

  python3 agent/scripts/session_start.py           # human summary
  python3 agent/scripts/session_start.py --json    # machine-readable

Every check is isolated and failure-tolerant: network being down or a
service being absent yields WARN/SKIP, never a crash. Exit code is 0
unless --strict is passed (then 1 if any check FAILs).

Standard library only.
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"


def _run(cmd, timeout=15):
    """Run a command from the repo root; returns (rc, stdout) — never raises."""
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001 — any failure is a soft-skip
        return -1, str(exc)


def _http_json(url, timeout=4):
    """GET a URL and parse JSON; returns dict or None — never raises."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Checks — each returns (status, message, data)
# ---------------------------------------------------------------------------

def check_git():
    rc, _ = _run(["git", "rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        return SKIP, "not a git repo", {}
    _run(["git", "fetch", "origin", "--quiet"], timeout=30)

    rc, out = _run(["git", "rev-list", "HEAD..origin/main", "--count"])
    if rc != 0 or not out.isdigit():
        return SKIP, "no comparable remote (offline or detached repo)", {}

    behind = int(out)
    if behind > 0:
        return WARN, f"{behind} commit(s) behind origin/main — run 'git pull --ff-only' before continuing", {"behind": behind}
    return OK, "up to date with origin/main", {"behind": 0}


def check_env():
    system = platform.system()
    osascript = shutil.which("osascript") is not None
    data = {"system": system, "osascript": osascript}
    if system != "Darwin" or not osascript:
        return WARN, (f"{system}, osascript={'yes' if osascript else 'no'} — sandboxed/"
                      f"non-macOS environment: read agent/docs/SANDBOXED_ENVIRONMENT.md"), data
    return OK, "native macOS with osascript", data


def check_companion():
    health = None
    for base in ("http://127.0.0.1:8765", "http://local.hub:8765"):
        health = _http_json(base + "/health")
        if health:
            break
    if not health:
        return WARN, "companion not responding on :8765 — Tier 2/3 automation unavailable (manual paste)", {}
    plugin = health.get("plugin") or {}
    data = {"version": health.get("version"), "plugin": plugin}
    if plugin.get("usable"):
        return OK, (f"companion v{health.get('version', '?')} · AgenticFM plug-in USABLE → "
                    f"plugin-preferred mode (read agent/docs/PLUGIN_INTEGRATION.md)"), data
    if plugin.get("installed"):
        return OK, (f"companion v{health.get('version', '?')} · plug-in installed but not usable "
                    f"(license/server) → OSS path"), data
    return OK, f"companion v{health.get('version', '?')} · no plug-in → OSS path", data


def check_project_md():
    if (REPO_ROOT / "PROJECT.md").exists():
        return OK, "PROJECT.md present — read it (local meta-project context)", {"exists": True}
    return SKIP, "no PROJECT.md (normal in collaborator clones)", {"exists": False}


def check_context():
    ctx_path = REPO_ROOT / "agent" / "CONTEXT.json"
    if not ctx_path.exists():
        return SKIP, "CONTEXT.json absent (normal until a solution has been explored)", {"exists": False}
    try:
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return FAIL, f"CONTEXT.json unreadable: {exc}", {"exists": True}
    age_h = (time.time() - ctx_path.stat().st_mtime) / 3600
    task = (data.get("task") or "")[:80]
    layout = (data.get("current_layout") or {}).get("name", "?")
    info = {"exists": True, "age_hours": round(age_h, 1), "task": task, "layout": layout}
    msg = f'layout "{layout}" · task "{task}" · {age_h:.1f} h old'
    if age_h > 24:
        return WARN, msg + " — possibly stale; consider a fresh Push Context", info
    return OK, msg, info


CHECKS = [
    ("git", check_git),
    ("env", check_env),
    ("companion", check_companion),
    ("project", check_project_md),
    ("context", check_context),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any check FAILs")
    args = parser.parse_args()

    results = {}
    for name, fn in CHECKS:
        try:
            status, msg, data = fn()
        except Exception as exc:  # noqa: BLE001 — one check must never take down the rest
            status, msg, data = FAIL, f"check crashed: {exc}", {}
        results[name] = {"status": status, "message": msg, "data": data}

    counts = {s: sum(1 for r in results.values() if r["status"] == s)
              for s in (OK, WARN, FAIL, SKIP)}

    if args.json:
        print(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"),
                          "results": results, "counts": counts},
                         ensure_ascii=False, indent=2))
    else:
        print(f"agentic-fm session start — {datetime.now():%Y-%m-%d %H:%M}")
        for name, r in results.items():
            print(f"  [{r['status']:>4}] {name:<10} {r['message']}")
        verdict = "ready"
        if counts[FAIL]:
            verdict = "with FAILURES"
        elif counts[WARN]:
            verdict = f"ready ({counts[WARN]} warning(s))"
        print(f"Verdict: {verdict}")

    return 1 if (args.strict and counts[FAIL]) else 0


if __name__ == "__main__":
    sys.exit(main())
