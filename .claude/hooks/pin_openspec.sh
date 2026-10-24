#!/usr/bin/env bash
# PreToolUse(Bash): rewrite a bare `openspec ...` to the pinned CLI instead of
# blocking it. The generated /opsx:* skills call bare `openspec`.
set -euo pipefail
input=$(cat)
cmd=$(jq -r '.tool_input.command // ""' <<<"$input")
PINNED="npx @fission-ai/openspec@1.14.1"

# Only command-position `openspec` (start, or after ; && || | ( ), not paths like openspec/specs.
pattern='(^|[;&|(]\s*)openspec(\s|$)'
grep -Pq "$pattern" <<<"$cmd" || exit 0

new=$(PINNED="$PINNED" perl -pe 's/(^|[;&|(]\s*)openspec(?=\s|$)/$1$ENV{PINNED}/g' <<<"$cmd")
# Auto-approve only a lone read-only call; anything else is rewritten but still asks,
# since "allow" here would bypass the normal permission prompt for the whole command.
decision=ask
if ! grep -q '[;&|`$(]' <<<"$cmd" && grep -Eq '^\s*openspec\s+(list|show|validate|status|instructions|view)(\s|$)' <<<"$cmd"; then
  decision=allow
fi

# updatedInput replaces the whole input object: carry every other field through.
jq --arg c "$new" --arg d "$decision" '{hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: $d,
    permissionDecisionReason: "Rewrote bare openspec to the pinned CLI",
    updatedInput: (.tool_input + {command: $c})}}' <<<"$input"
