#!/usr/bin/env bash
# PreToolUse(Bash|Edit|Write): any dependency change needs the user's approval.
set -euo pipefail
input=$(cat)
tool=$(jq -r '.tool_name' <<<"$input")

ask() {
  jq -n --arg r "$1" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $r}}'
  exit 0
}

if [[ "$tool" == "Bash" ]]; then
  cmd=$(jq -r '.tool_input.command // ""' <<<"$input")
  if grep -Eq '(^|[;&|]|\s)(uv\s+(add|remove|pip\s+install)|pip3?\s+install|npm\s+(install|i)\s+\S)' <<<"$cmd"; then
    ask "Dependency change — needs user approval (CLAUDE.md: ask before adding a dependency)."
  fi
else
  path=$(jq -r '.tool_input.file_path // ""' <<<"$input")
  case "$path" in
    */pyproject.toml|*/uv.lock|*/package.json)
      ask "Editing $(basename "$path") can change dependencies — needs user approval." ;;
  esac
fi
exit 0
