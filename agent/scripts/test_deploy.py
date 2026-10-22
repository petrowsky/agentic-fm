#!/usr/bin/env python3
"""
test_deploy.py - Interactive deployment test suite for agentic-fm.

Validates all deployment tiers systematically with on-the-fly fixture
generation and developer-verified pass/fail for each test.

Usage:
    python3 agent/scripts/test_deploy.py                  # run all phases
    python3 agent/scripts/test_deploy.py --phase 1        # run Phase 1 only
    python3 agent/scripts/test_deploy.py --test T2-R      # run a single test
    python3 agent/scripts/test_deploy.py --list            # list all tests

Phases:
    1 (FG)  — Foreground, single file
    2 (BG)  — FM backgrounded
    3 (MF)  — Multi-file targeting
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SANDBOX = os.path.join(REPO_ROOT, "agent", "sandbox")
DEBUG_DIR = os.path.join(REPO_ROOT, "agent", "debug")
VALIDATOR = os.path.join(HERE, "validate_snippet.py")
RESULTS_FILE = os.path.join(DEBUG_DIR, "test-deploy-results.json")

# Import deploy module
sys.path.insert(0, HERE)
from deploy import deploy, _load_config, _resolve_companion_url, _resolve_target_file, _post_json


# ---------------------------------------------------------------------------
# Fixture generation
# ---------------------------------------------------------------------------

def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")


def _make_fixture(test_id: str, tier: int, mode: str, ts: str) -> str:
    """Generate a unique fmxmlsnippet XML fixture for a test.

    Each fixture contains identifiable comment steps and a Set Variable
    so the developer can visually confirm the correct content landed.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<fmxmlsnippet type="FMObjectList">\n'
        f'  <Step enable="True" id="89" name="# (comment)">\n'
        f'    <Text>TEST {test_id} | Tier {tier} | Mode: {mode} | {ts}</Text>\n'
        f'  </Step>\n'
        f'  <Step enable="True" id="141" name="Set Variable">\n'
        f'    <Value>\n'
        f'      <Calculation><![CDATA["{test_id}"]]></Calculation>\n'
        f'    </Value>\n'
        f'    <Repetition>\n'
        f'      <Calculation><![CDATA[1]]></Calculation>\n'
        f'    </Repetition>\n'
        f'    <Name>$testId</Name>\n'
        f'  </Step>\n'
        f'  <Step enable="True" id="89" name="# (comment)">\n'
        f'    <Text>END {test_id}</Text>\n'
        f'  </Step>\n'
        '</fmxmlsnippet>\n'
    )


def _write_fixture(test_id: str, tier: int, mode: str, ts: str) -> str:
    """Write a fixture XML file and validate it. Returns the file path."""
    xml = _make_fixture(test_id, tier, mode, ts)
    filename = f"test-{test_id.lower()}.xml"
    path = os.path.join(SANDBOX, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)

    # Validate
    result = subprocess.run(
        [sys.executable, VALIDATOR, path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  FIXTURE VALIDATION FAILED for {test_id}:")
        print(f"  {result.stdout.strip()}")
        print(f"  {result.stderr.strip()}")
        return ""

    return path


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _health_check(companion_url: str) -> bool:
    """Check companion server is reachable."""
    try:
        req = urllib.request.Request(f"{companion_url}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Interactive verification
# ---------------------------------------------------------------------------

def _verify(test_id: str, description: str) -> str:
    """Prompt the developer to verify a test result.

    Returns: 'pass', 'fail', or 'skip'
    """
    print(f"\n  ▸ {description}")
    print(f"    [{test_id}] Verify in FileMaker, then:")
    print(f"    [p]ass  [f]ail  [s]kip")
    try:
        choice = input(f"    Result: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n    Skipped.")
        return "skip"

    if choice in ("p", "pass"):
        return "pass"
    elif choice in ("f", "fail"):
        note = input("    Failure note (optional): ").strip()
        return f"fail: {note}" if note else "fail"
    else:
        return "skip"


# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------

# Each test is a dict with:
#   id, phase, tier, mode, target, description, setup_prompt, verify_prompt
TESTS = [
    # Phase 1: Foreground, single file
    {
        "id": "T1",
        "phase": 1,
        "tier": 1,
        "mode": "clipboard",
        "target": None,
        "description": "Tier 1 — clipboard only, developer pastes manually",
        "setup_prompt": "FM Pro foregrounded, Script Workspace open on any script.",
        "verify_prompt": "Paste (⌘V) into a script. See the TEST T1 comment steps?",
    },
    {
        "id": "T2-R",
        "phase": 1,
        "tier": 2,
        "mode": "replace",
        "target": "Sandbox",
        "description": "Tier 2 — replace existing steps in Sandbox",
        "setup_prompt": "FM Pro foregrounded. Sandbox script exists.",
        "verify_prompt": "Open Sandbox. Contains ONLY: TEST T2-R comment + Set Variable + END comment?",
    },
    {
        "id": "T2-A",
        "phase": 1,
        "tier": 2,
        "mode": "append",
        "target": "Sandbox",
        "description": "Tier 2 — append steps to Sandbox (after T2-R)",
        "setup_prompt": "Sandbox should still have T2-R steps from previous test.",
        "verify_prompt": "Open Sandbox. T2-R steps still there, with T2-A steps appended after?",
    },
    {
        "id": "T2-AS",
        "phase": 1,
        "tier": 2,
        "mode": "replace+auto-save",
        "target": "Sandbox",
        "description": "Tier 2 — replace with auto-save (tab should lose * after paste)",
        "setup_prompt": "FM Pro foregrounded.",
        "verify_prompt": "Sandbox tab has no unsaved indicator (*)?",
    },
    {
        "id": "T3",
        "phase": 1,
        "tier": 3,
        "mode": "create-new",
        "target": None,  # generated with timestamp
        "description": "Tier 3 — create new script, paste steps, save",
        "setup_prompt": "FM Pro foregrounded. No special prep needed.",
        "verify_prompt": "New script created with correct name? Contains TEST T3 steps?",
    },
    # Phase 2: Backgrounded
    {
        "id": "T2-R-BG",
        "phase": 2,
        "tier": 2,
        "mode": "replace",
        "target": "Sandbox",
        "description": "Tier 2 — replace with FM backgrounded",
        "setup_prompt": "Background FM Pro (Cmd+H or click another app).",
        "verify_prompt": "FM came to front? Sandbox replaced with T2-R-BG steps?",
    },
    {
        "id": "T3-BG",
        "phase": 2,
        "tier": 3,
        "mode": "create-new",
        "target": None,
        "description": "Tier 3 — create new with FM backgrounded",
        "setup_prompt": "Background FM Pro again.",
        "verify_prompt": "FM came to front? New script created with T3-BG steps?",
    },
    # Phase 3: Multi-file
    {
        "id": "T2-MF",
        "phase": 3,
        "tier": 2,
        "mode": "replace",
        "target": "Sandbox",
        "description": "Tier 2 — replace with wrong file in front",
        "setup_prompt": "Open a SECOND FM file. Put it in front (not the target solution).",
        "verify_prompt": "Steps landed in the correct file's Sandbox (not the front file)?",
    },
    {
        "id": "T3-MF",
        "phase": 3,
        "tier": 3,
        "mode": "create-new",
        "target": None,
        "description": "Tier 3 — create new with wrong file in front",
        "setup_prompt": "Second FM file still in front.",
        "verify_prompt": "New script created in the correct file (not the front file)?",
    },
]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_test(test: dict, ts: str, config: dict) -> dict:
    """Run a single test. Returns a result dict."""
    test_id = test["id"]
    tier = test["tier"]
    mode = test["mode"]
    target = test["target"]

    # For Tier 3 tests, generate a timestamped script name
    if tier == 3 and target is None:
        target = f"Test {test_id} {ts}"

    print(f"\n{'='*60}")
    print(f"  TEST: {test_id} — {test['description']}")
    print(f"{'='*60}")

    # Setup prompt
    print(f"\n  SETUP: {test['setup_prompt']}")
    try:
        input("  Press Enter when ready (or Ctrl+C to skip)...")
    except (KeyboardInterrupt, EOFError):
        print("\n  Skipped.")
        return {"id": test_id, "result": "skip", "tier": tier}

    # Generate fixture
    fixture_path = _write_fixture(test_id, tier, mode, ts)
    if not fixture_path:
        return {"id": test_id, "result": "fail: fixture validation failed", "tier": tier}

    print(f"  Fixture: {os.path.basename(fixture_path)}")

    # Resolve target_file — always resolve for visibility, but only
    # pass it explicitly for Phase 3 tests. For Phase 1/2, let deploy()
    # auto-resolve internally (tests whether auto-resolution works).
    resolved_file = _resolve_target_file(config)
    target_file = None
    if test["phase"] == 3:
        target_file = resolved_file
        if not target_file:
            # Try automation.json solutions keys
            solutions = config.get("solutions", {})
            if solutions:
                keys = list(solutions.keys())
                print(f"  Available solutions: {', '.join(keys)}")
                try:
                    tf = input(f"  Enter target file name: ").strip()
                except (KeyboardInterrupt, EOFError):
                    tf = ""
                if tf:
                    target_file = tf
                else:
                    print("  No target file — skipping multi-file test.")
                    return {"id": test_id, "result": "skip: no target_file", "tier": tier}
        print(f"  Target file: {target_file}")
    else:
        print(f"  Auto-resolved file: {resolved_file or '(none — will use document 1)'}")

    # Determine deploy kwargs
    deploy_kwargs = {
        "xml_path": fixture_path,
        "target_script": target,
        "tier": tier,
        "target_file": target_file,
    }

    if mode == "append":
        deploy_kwargs["select_all"] = False
    elif mode == "replace+auto-save":
        deploy_kwargs["auto_save"] = True

    # Deploy
    print(f"  Deploying... (tier={tier}, target={target or '(clipboard only)'}, file={deploy_kwargs.get('target_file') or '(auto)'})")
    result = deploy(**deploy_kwargs)

    # Show result
    if result.get("instructions"):
        print(f"\n  {result['instructions']}")
    elif result.get("message"):
        print(f"\n  ✓ {result['message']}")
    elif result.get("error"):
        print(f"\n  ✗ Error: {result['error']}")

    if result.get("fallback_from"):
        print(f"  (Fell back from Tier {result['fallback_from']}: {result.get('fallback_reason', '')})")

    tier_used = result.get("tier_used", tier)

    # Verify
    verification = _verify(test_id, test["verify_prompt"])

    return {
        "id": test_id,
        "result": verification,
        "tier_requested": tier,
        "tier_used": tier_used,
        "mode": mode,
        "target": target,
        "target_file_explicit": target_file,
        "target_file_resolved": resolved_file,
        "deploy_success": result.get("success", False),
        "fallback_from": result.get("fallback_from"),
        "fallback_reason": result.get("fallback_reason"),
    }


def run_phase(phase: int, ts: str, config: dict) -> list[dict]:
    """Run all tests in a phase."""
    phase_names = {1: "Foreground (single file)", 2: "Backgrounded", 3: "Multi-file"}
    phase_tests = [t for t in TESTS if t["phase"] == phase]

    if not phase_tests:
        print(f"\nNo tests defined for Phase {phase}.")
        return []

    print(f"\n{'#'*60}")
    print(f"  PHASE {phase}: {phase_names.get(phase, 'Unknown')}")
    print(f"  {len(phase_tests)} test(s)")
    print(f"{'#'*60}")

    results = []
    for test in phase_tests:
        result = run_test(test, ts, config)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Results logging
# ---------------------------------------------------------------------------

def _save_results(results: list[dict], ts: str):
    """Save results to JSON file."""
    os.makedirs(DEBUG_DIR, exist_ok=True)

    # Load existing results if present
    existing = []
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing = data.get("runs", [])
        except (OSError, ValueError):
            pass

    run_entry = {
        "timestamp": ts,
        "tests": results,
    }
    existing.append(run_entry)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"runs": existing}, f, indent=2, ensure_ascii=False)

    print(f"\n  Results saved to {RESULTS_FILE}")


def _print_summary(results: list[dict]):
    """Print a summary table of test results."""
    if not results:
        return

    passed = sum(1 for r in results if r["result"] == "pass")
    failed = sum(1 for r in results if r["result"].startswith("fail"))
    skipped = sum(1 for r in results if r["result"].startswith("skip"))

    print(f"\n{'='*60}")
    print(f"  SUMMARY: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*60}")
    print(f"  {'ID':<12} {'Tier':<6} {'Mode':<20} {'Result'}")
    print(f"  {'-'*11} {'-'*5} {'-'*19} {'-'*20}")
    for r in results:
        tier_info = str(r.get("tier_requested", "?"))
        if r.get("fallback_from"):
            tier_info += f"→{r.get('tier_used', '?')}"
        status = "✓" if r["result"] == "pass" else ("✗" if r["result"].startswith("fail") else "—")
        print(f"  {r['id']:<12} {tier_info:<6} {r.get('mode', ''):<20} {status} {r['result']}")

    # Tier 3 cleanup reminder
    tier3_tests = [r for r in results if r.get("tier_requested") == 3 and r["result"] == "pass"]
    if tier3_tests:
        print(f"\n  ⚠ Tier 3 cleanup: delete these test scripts from Script Workspace:")
        for r in tier3_tests:
            print(f"    - {r.get('target', '?')}")


# ---------------------------------------------------------------------------
# Fixture cleanup
# ---------------------------------------------------------------------------

def _cleanup_fixtures():
    """Remove test fixture files from sandbox."""
    for f in os.listdir(SANDBOX):
        if f.startswith("test-t") and f.endswith(".xml"):
            path = os.path.join(SANDBOX, f)
            os.unlink(path)
            print(f"  Cleaned up: {f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Interactive deployment test suite for agentic-fm.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--phase", type=int, choices=[1, 2, 3],
        help="Run only this phase (1=FG, 2=BG, 3=MF)"
    )
    parser.add_argument(
        "--test", dest="test_id",
        help="Run a single test by ID (e.g. T2-R)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all tests without running"
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Remove test fixture files from sandbox"
    )
    args = parser.parse_args()

    if args.list:
        print(f"\n  {'ID':<12} {'Phase':<7} {'Tier':<6} {'Mode':<20} Description")
        print(f"  {'-'*11} {'-'*6} {'-'*5} {'-'*19} {'-'*30}")
        for t in TESTS:
            print(f"  {t['id']:<12} {t['phase']:<7} {t['tier']:<6} {t['mode']:<20} {t['description']}")
        return

    if args.cleanup:
        _cleanup_fixtures()
        return

    # Load config and check companion
    config = _load_config()
    companion_url = _resolve_companion_url(config)

    print(f"\n  Deployment Test Suite")
    print(f"  Companion: {companion_url}")

    if not _health_check(companion_url):
        print(f"\n  ✗ Companion server not reachable at {companion_url}")
        print(f"    Start it: python3 agent/scripts/companion_server.py")
        sys.exit(1)
    print(f"  ✓ Companion server healthy")

    ts = _timestamp()
    all_results = []

    if args.test_id:
        # Run a single test
        test = next((t for t in TESTS if t["id"] == args.test_id.upper()), None)
        if not test:
            print(f"\n  Unknown test ID: {args.test_id}")
            print(f"  Available: {', '.join(t['id'] for t in TESTS)}")
            sys.exit(1)
        result = run_test(test, ts, config)
        all_results.append(result)
    elif args.phase:
        all_results = run_phase(args.phase, ts, config)
    else:
        # Run all phases
        for phase in [1, 2, 3]:
            phase_results = run_phase(phase, ts, config)
            all_results.extend(phase_results)

    # Save and summarize
    if all_results:
        _save_results(all_results, ts)
        _print_summary(all_results)
        print()


if __name__ == "__main__":
    main()
