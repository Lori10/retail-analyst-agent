#!/usr/bin/env bash
# PreToolUse(Bash): enforce the git workflow in CLAUDE.md. GitHub's ruleset on main
# (PR + green CI required) is the authoritative gate; this is the fast local layer.
#   - force-push, rebase, commit on main, push to main  -> deny
#   - --no-verify, gh pr merge --admin                  -> deny
#   - push that adds secret-looking files               -> ask
#   - git merge, gh pr merge                            -> ask (the human gate)
#   - commit / push on a feature branch                 -> allowed via settings.json
set -euo pipefail
cmd=$(jq -r '.tool_input.command // ""')

decide() {
  jq -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $r}}'
  exit 0
}

# Match git/gh subcommands anywhere in a compound command (a && git commit ...).
git_sub() { grep -Eq "(^|[;&|(]|\s)git(\s+-C\s+\S+)?\s+$1(\s|$)" <<<"$cmd"; }
gh_pr_merge() { grep -Eq "(^|[;&|(]|\s)gh\s+pr\s+merge(\s|$)" <<<"$cmd"; }

repo="${CLAUDE_PROJECT_DIR:-.}"
branch=$(git -C "$repo" branch --show-current 2>/dev/null || true)

if git_sub push && grep -Eq '(\s--force(-with-lease)?|\s-f)(\s|$)' <<<"$cmd"; then
  decide deny "Force-push is not allowed in this repo (CLAUDE.md git workflow)."
fi
if git_sub rebase; then
  decide deny "No rebasing unless the user explicitly asks; if they did, they run it themselves."
fi
if grep -Eq '\s--no-verify(\s|$)' <<<"$cmd"; then
  decide deny "--no-verify skips checks; fix the failure instead."
fi
if git_sub commit && [[ "$branch" == "main" ]]; then
  decide deny "Never commit on main. Create a feature branch first (/new-branch)."
fi
if git_sub push && { [[ "$branch" == "main" ]] || grep -Eq '(\s|:)main(\s|$)' <<<"$cmd"; }; then
  decide deny "Never push to main. Land changes through a PR (/finish-branch)."
fi
if gh_pr_merge && grep -Eq '\s--admin(\s|$)' <<<"$cmd"; then
  decide deny "gh pr merge --admin bypasses the required CI check."
fi
if git_sub push; then
  # Public repo: a push publishes. Ask before files that look like secrets leave the machine.
  base=$(git -C "$repo" merge-base origin/main HEAD 2>/dev/null || true)
  if [[ -n "$base" ]]; then
    risky=$(git -C "$repo" diff --name-only --diff-filter=A "$base" HEAD \
      | grep -Ei '(^|/)\.env|credential|secret|\.pem$|\.key$|service[-_]?account.*\.json$' || true)
    [[ -n "$risky" ]] && decide ask "Public repo: this push adds secret-looking files: ${risky//$'\n'/, }"
  fi
fi
if git_sub merge || gh_pr_merge; then
  decide ask "Merge is the human gate: needs explicit user confirmation (branch: ${branch:-?})."
fi
exit 0
