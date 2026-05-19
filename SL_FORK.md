# Special-Lite Internal Fork

This repository is **Special-Lite's internal fork** of [petrowsky/agentic-fm](https://github.com/petrowsky/agentic-fm). It is consumed as a git submodule from `special-lite/sl-inventory` and (eventually) other SL FileMaker module repos.

## Why a fork

Two reasons:

1. **SL-specific lint rules and gotchas** — the upstream project is shared with other teams. Patterns that bit us repeatedly during SL Inventory audit-fix passes (Pattern A polarity bugs, empty PSOS parameter calcs, etc.) live as fmlint rules here. See `agent/fmlint/` for the rule sources and `docs/patterns/` in sl-inventory for the prose write-ups.

2. **Bug fixes we need today** — when we hit a bug that's been bothering us for weeks (e.g., the `tx_perform_script_on_server` / `tx_new_window` translator gap in `fm_xml_to_snippet.py`) we ship the fix here immediately rather than waiting on an upstream PR review cycle.

## Do not PR changes back to `petrowsky/agentic-fm`

**This is the firm rule.** Changes made on this fork stay on this fork. Upstream improvements may be pulled in, but our changes are not sent up.

The exception: if upstream itself opens a discussion about a feature that overlaps with something we've built, we can share our implementation as a reference. But no automated/proactive PR-back.

## Pulling upstream improvements

```bash
cd path/to/sl-inventory/agentic-fm
git fetch upstream                    # `upstream` should already point at petrowsky/agentic-fm
git checkout main
git merge upstream/main               # or rebase; merge is fine
# resolve any conflicts (rare — divergence is in fmlint/ and a few translator additions)
git push origin main
```

After pushing to the fork's `main`, bump the submodule pointer in sl-inventory:

```bash
cd path/to/sl-inventory
git add agentic-fm
git commit -m "submodule: bump agentic-fm to pull upstream changes through DATE"
```

## Intentional divergences from upstream

A short ledger of what differs from `petrowsky/agentic-fm`. Update this whenever a new SL-only change lands.

| Date | Change | Reason |
|---|---|---|
| 2026-05-15 | `agent/scripts/fm_xml_to_snippet.py` — added `tx_perform_script_on_server` and `tx_new_window` translators | Catalog-driven generic translator silently dropped PSOS script reference + parameter calc, and dropped New Window's Style/Name/Bounds/Options. Both broke transactional workflows on every SaXML → fmxmlsnippet round-trip. |
| 2026-05-15 | `agent/catalogs/step-catalog-en.json` — added `CurrentLayout`, `LayoutNameByCalculation`, `LayoutNumberByCalculation` to the `LayoutDestination` enum | Verified via FM round-trip 2026-05-14 (see sl-inventory `docs/patterns/fm-fmxmlsnippet-gotchas.md` §1). Upstream catalog only listed `SelectedLayout`. |
| 2026-05-15 | `agent/fmlint/rules/sl_*` — SL-specific lint rules | Pattern A polarity bug (`Set Error Capture [OFF]` before record-mutating step) and empty PSOS parameter calc are both silent-failure classes that have bitten this codebase 3+ times. Lint catches them deterministically. |
| 2026-05-15 | `agent/docs/CODING_CONVENTIONS.md` — deprecated `Insert Text [$README]` doc-block pattern + ban `PARAMETER FORMAT` / `RESULT JSON` / `INPUT:` / `OUTPUT:` / `PARAMS:` / `RETURNS:` header comments | Header parameter/result documentation drifts the moment the script is modified — second source of truth has to be maintained in lockstep with the script's own `JSONGetElement` / HANDLE RESULT JSON. SL_Core convention is comment-step-only headers. Enforced by `fmlint` SL018. |
| 2026-05-19 | `agent/docs/CODING_CONVENTIONS.md` — added "Custom Function return contract" section | CFs follow native FM calc idioms (return value or `"?"` on failure), **not** the script `$scriptResultObject` JSON contract. Discovered during UOM v2 Phase 2b authoring: the first version of `ConvertToItemBase` returned `"ERROR: ..."` strings, which Trevor flagged as non-native. The native pattern (`Number ( "abc" ) = "?"`, etc.) is what CF callers expect. Doc captures the two CF categories (pure math vs. lookup) and the caller pattern for detecting `"?"`. |

## Cross-references

- **SL FileMaker patterns and gotchas** → [`sl-inventory/docs/patterns/`](https://fmdev.special-lite.com:8080/dev/inventory/patterns/) (hosted in sl-docs)
- **SL team workflow rules** → [`sl-inventory/CLAUDE.md`](../CLAUDE.md) and [`sl-docs/dev/team-workflow.md`](https://fmdev.special-lite.com:8080/dev/team-workflow/)
- **Upstream project** → [`petrowsky/agentic-fm`](https://github.com/petrowsky/agentic-fm)

## For Claude sessions opening this folder cold

If you're an agent reading this on session start: this is a fork. The SL-specific rules in [`sl-inventory/CLAUDE.md`](../CLAUDE.md) (`No subagents for FM script generation`, etc.) apply. Read [`sl-inventory/docs/patterns/`](../docs/patterns/) before editing any FM scripts. Do not propose PRs back to petrowsky/agentic-fm.
