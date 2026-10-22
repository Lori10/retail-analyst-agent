#!/usr/bin/env bash
# SessionStart(compact): re-orient after compaction — branch, dirty files, open
# OpenSpec change and its unchecked tasks. Plain stdout is added to context.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}"
branch=$(git branch --show-current 2>/dev/null)
echo "## Re-orientation after compaction"
echo "Branch: ${branch:-detached}"
echo "Uncommitted:"; git status --short | head -30
echo "Open OpenSpec changes:"
ls openspec/changes | grep -v '^archive$' | sed 's/^/  - /'
# Feature branches are usually named after the change (feat/add-golden-bucket).
change="${branch#*/}"
tasks="openspec/changes/$change/tasks.md"
if [[ -f "$tasks" ]]; then
  echo "Unchecked tasks in $tasks:"
  grep -n -- '- \[ \]' "$tasks" | head -20
fi
