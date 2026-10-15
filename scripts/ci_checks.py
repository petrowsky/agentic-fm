#!/usr/bin/env python3
"""Repo sanity checks for agentic-fm.

Guards the machine-readable artifacts that every other tool depends on:

  1. catalogs   — every agent/catalogs/*.json parses as valid JSON
                  (a single missing comma silently breaks every consumer)
  2. converter  — unit tests for the SaXML → fmxmlsnippet translator
  3. fmlint     — unit tests for the linter (incl. the param-fidelity
                  corpus smoke test against agent/snippet_examples/)

Usage:
  python3 scripts/ci_checks.py            # run everything
  python3 scripts/ci_checks.py --quick    # catalogs only (fast path)

Exit code 0 = all green; 1 = at least one check failed.
Designed to be run by hand, from the pre-push hook, or from CI.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def check_catalogs() -> list:
    """Validate every JSON catalog parses. Returns list of failures."""
    failures = []
    catalog_dir = REPO_ROOT / "agent" / "catalogs"
    files = sorted(catalog_dir.glob("*.json"))
    if not files:
        return [f"no JSON catalogs found in {catalog_dir}"]
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: invalid JSON — {exc}")
        except OSError as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: unreadable — {exc}")
    return failures


def _check_catalogs_wrapped() -> tuple:
    failures = check_catalogs()
    return ("FAIL" if failures else "OK"), failures


def run_cmd(label: str, cmd: list, required_path: Path) -> tuple:
    """Run a test command from the repo root. Returns (status, failures).

    Fail-open on absence: if `required_path` doesn't exist, the suite it
    would exercise simply isn't present in this checkout (e.g. a test
    module not yet merged) — that's a SKIP, not a FAIL, so this check
    never blocks a push/CI run over a suite it can't find.
    """
    if not required_path.exists():
        return "SKIP", []
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-12:])
        return "FAIL", [f"{label} failed (exit {proc.returncode}):\n{tail}"]
    return "OK", []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="catalog JSON validation only (skip test suites)")
    args = parser.parse_args()

    checks = [("catalogs", _check_catalogs_wrapped)]
    if not args.quick:
        checks.append(("converter tests", lambda: run_cmd(
            "converter tests",
            [sys.executable, "agent/scripts/test_fm_xml_to_snippet.py"],
            REPO_ROOT / "agent" / "scripts" / "test_fm_xml_to_snippet.py")))
        checks.append(("fmlint tests", lambda: run_cmd(
            "fmlint tests",
            [sys.executable, "-m", "unittest", "discover",
             "-s", "agent/fmlint/tests", "-t", "."],
            REPO_ROOT / "agent" / "fmlint" / "tests")))

    all_failures = []
    for label, fn in checks:
        status, failures = fn()
        print(f"[{status}] {label}")
        all_failures.extend(failures)

    if all_failures:
        print("\n--- failures ---", file=sys.stderr)
        for f in all_failures:
            print(f, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
