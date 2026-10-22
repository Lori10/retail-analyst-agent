#!/usr/bin/env bash
# PostToolUse(Edit|Write): ruff-check the edited Python file; exit 2 feeds errors back to Claude.
set -uo pipefail
path=$(jq -r '.tool_input.file_path // ""')
[[ "$path" == *.py && -f "$path" ]] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"
if ! out=$(uv run --quiet ruff check --force-exclude "$path" 2>&1); then
  echo "ruff found problems in $path — fix them:" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
