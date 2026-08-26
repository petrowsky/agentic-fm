#!/usr/bin/env bash
# Verify the four agent-instructions paths all resolve to the same file content.
#
# Canonical file: .claude/CLAUDE.md
# Symlinks:       .cursor/AGENTS.md (Cursor IDE)
#                 AGENTS.md (root — Aider, Cline, Continue.dev, generic)
#                 .github/copilot-instructions.md (GitHub Copilot)
#
# All four paths must resolve to the same underlying content. If they diverge
# (e.g. someone replaced a symlink with a regular file), this script fails.
# Useful as a pre-commit check or CI gate.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

declare -a PATHS=(
    "$REPO_ROOT/.claude/CLAUDE.md"
    "$REPO_ROOT/.cursor/AGENTS.md"
    "$REPO_ROOT/AGENTS.md"
    "$REPO_ROOT/.github/copilot-instructions.md"
)

declare -a missing=()
declare -a hashes=()

for p in "${PATHS[@]}"; do
    if [ ! -e "$p" ]; then
        missing+=("$p")
        continue
    fi
    hashes+=("$(md5 -q "$p"):$p")
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: missing agent-instructions paths:" >&2
    printf '  %s\n' "${missing[@]}" >&2
    exit 1
fi

# Compare hashes — first column should all match
first_hash="${hashes[0]%%:*}"
ok=1
for entry in "${hashes[@]}"; do
    h="${entry%%:*}"
    p="${entry#*:}"
    if [ "$h" != "$first_hash" ]; then
        echo "ERROR: $p has different content from canonical" >&2
        ok=0
    fi
done

if [ $ok -eq 1 ]; then
    echo "OK — all 4 agent-instructions paths resolve to the same content (md5 $first_hash)"
else
    echo "" >&2
    echo "Restore symlinks with:" >&2
    echo "  ln -sfn .claude/CLAUDE.md AGENTS.md" >&2
    echo "  ln -sfn ../.claude/CLAUDE.md .github/copilot-instructions.md" >&2
    echo "  ln -sfn ../.claude/CLAUDE.md .cursor/AGENTS.md" >&2
    exit 1
fi
