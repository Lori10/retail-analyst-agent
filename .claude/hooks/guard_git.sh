#!/usr/bin/env bash
# PreToolUse(Bash): enforce the git workflow in CLAUDE.md.
#   - no commit/push/merge while on main          -> deny
#   - no force-push, no rebase                    -> deny
#   - every other commit/push/merge               -> ask (explicit user confirmation)
set -euo pipefail
cmd=$(jq -r '.tool_input.command // ""')

decide() {
  jq -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $r}}'
  exit 0
}

# Match git subcommands anywhere in a compound command (a && git commit ...).
git_sub() { grep -Eq "(^|[;&|(]|\s)git(\s+-C\s+\S+)?\s+$1(\s|$)" <<<"$cmd"; }

if git_sub push && grep -Eq '(\s--force(-with-lease)?|\s-f)(\s|$)' <<<"$cmd"; then
  decide deny "Force-push is not allowed in this repo (CLAUDE.md git workflow)."
fi
if git_sub rebase; then
  decide deny "No rebasing unless the user explicitly asks; if they did, they run it themselves."
fi
if git_sub commit || git_sub push || git_sub merge; then
  branch=$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null || true)
  # `git merge` on main is the normal /finish-branch step; commit/push on main are not
  # (`git push origin main` after a merge is the exception, still confirmed via ask).
  if [[ "$branch" == "main" ]] && git_sub commit; then
    decide deny "Never commit on main. Create a feature branch first (/new-branch)."
  fi
  decide ask "Git workflow: commit/push/merge needs explicit user confirmation (branch: ${branch:-?})."
fi
exit 0
