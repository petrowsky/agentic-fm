# scripts/ — repo-level CI and developer tooling

This folder holds scripts that operate on the **repository itself** (sanity
checks, git hooks) — as opposed to `agent/scripts/`, which is reserved for
scripts the agent/LLM invokes at runtime to operate on a FileMaker solution
(`companion_server.py`, `deploy.py`, `clipboard.py`, etc.).

## What's here

- **`ci_checks.py`** — validates that every `agent/catalogs/*.json` file
  parses as valid JSON, then runs the SaXML→fmxmlsnippet converter's test
  suite and the fmlint test suite (including the param-fidelity corpus
  smoke test against `agent/snippet_examples/`). Exit 0 = all green, 1 = at
  least one check failed.
- **`hooks/pre-push`** — a git pre-push hook. Whenever a push touches a
  critical artifact (a catalog under `agent/catalogs/`, the SaXML/HR
  converters, or anything under `agent/fmlint/`), it runs `ci_checks.py` and
  blocks the push if it fails.
- **`install-hooks.sh`** — installs the hooks from `scripts/hooks/` into the
  repo's real hooks directory. Worktree-aware: resolves the target via
  `git rev-parse --git-common-dir` rather than assuming a bare `.git/hooks`,
  so it installs correctly even when run from inside a `git worktree`
  (where `.git` is a pointer file, not a directory).

## Setup

If you're an agent wiring this up in a fresh clone (this is the common
case — a human collaborator rarely runs this by hand), the whole thing is
one command from the repo root:

```bash
bash scripts/install-hooks.sh
```

This copies every file under `scripts/hooks/` into the repo's real hooks
directory and marks them executable. Expected output:

```
✅ Installed: .git/hooks/pre-push

agentic-fm hooks installed (1). Protection active in this repo.
```

Re-run it any time a hook under `scripts/hooks/` changes — it always
overwrites the installed copy with the current source.

### Running the checks directly

You don't need the hook installed to run the checks — they're just as
useful on demand or wired into CI:

```bash
python3 scripts/ci_checks.py            # full suite
python3 scripts/ci_checks.py --quick    # catalogs only, fast path
```

### What the hook gates on

`hooks/pre-push` inspects the full set of files touched by every commit in
the push range (not just the tip commit) against:

```
^(agent/catalogs/.*\.json$|agent/scripts/fm_xml_to_snippet\.py$|agent/scripts/snippet_to_hr\.py$|agent/fmlint/)
```

If none of the pushed commits touch a path matching that, the hook is a
silent no-op — most pushes pay no cost at all.

### Emergency bypass

```bash
git push --no-verify
```

Use this only when you're certain the failure is a false positive you'll
fix in a follow-up push — the checks exist because a single missing comma
in a catalog JSON file once silently broke the linter for every consumer,
with nothing catching it before it reached collaborators.
