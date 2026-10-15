#!/bin/bash
# agentic-fm install-hooks.sh
# Installs (or reinstalls) the repo's protective git hooks.
#
# Usage:
#   bash scripts/install-hooks.sh
#
# Hooks are versioned in scripts/hooks/ and copied to .git/hooks/ in
# the current repo. Re-run this script after pulling if a hook has changed.

set -euo pipefail

# Find the repo root
if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    echo "❌ Not inside a git repository."
    exit 1
fi

HOOKS_SRC="$REPO_ROOT/scripts/hooks"

# Resolve the COMMON git directory (supports git worktrees, where .git is a
# pointer file and hooks live in the main repo's .git instead).
GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || echo "$REPO_ROOT/.git")"
case "$GIT_COMMON_DIR" in
    /*) : ;;                                   # already absolute
    *) GIT_COMMON_DIR="$REPO_ROOT/$GIT_COMMON_DIR" ;;  # relative to repo cwd
esac
HOOKS_DST="$GIT_COMMON_DIR/hooks"

if [ ! -d "$HOOKS_SRC" ]; then
    echo "❌ Could not find $HOOKS_SRC"
    echo "   Are you in an agentic-fm repo checked out from main?"
    exit 1
fi

mkdir -p "$HOOKS_DST"

installed=0
for hook in "$HOOKS_SRC"/*; do
    [ -f "$hook" ] || continue
    name="$(basename "$hook")"
    dst="$HOOKS_DST/$name"

    cp "$hook" "$dst"
    chmod +x "$dst"
    echo "✅ Installed: .git/hooks/$name"
    installed=$((installed + 1))
done

if [ $installed -eq 0 ]; then
    echo "⚠️  No hooks found in $HOOKS_SRC"
    exit 1
fi

echo ""
echo "agentic-fm hooks installed ($installed). Protection active in this repo."
