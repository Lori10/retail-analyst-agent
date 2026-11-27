#!/usr/bin/env bash
# PreToolUse(Bash): live, billed or cloud-touching runs need the user's go-ahead each time
# (CLAUDE.md: live checks are billed; the user runs or approves them). Offline pytest, ruff
# and the stop/verify gates are untouched: they never reach Gemini or BigQuery.
#   - scripts/run_eval.py, scripts/approve_trio.py        -> ask (Gemini + BigQuery / embeddings)
#   - pytest naming tests/integration                      -> ask (live smoke + PII tests)
#   - uv run retail-agent | retail-mcp, python -m retail_agent -> ask (live CLI / MCP session)
#   - loading .env or live keys into the shell             -> ask (turns offline runs live)
#   - bq, gcloud                                            -> ask (bq rm stays denied in settings)
set -euo pipefail
cmd=$(jq -r '.tool_input.command // ""')

ask() {
  jq -n --arg r "$1" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $r}}'
  exit 0
}
# Command-position match anywhere in a compound command (a && bq query ...).
# Command position only (start, or after ; && || | ( ), so `grep pytest …` or `echo bq` don't prompt.
at_cmd() { grep -Eq "(^|[;&|(])\s*$1(\s|$)" <<<"$cmd"; }
# pytest however it is launched: pytest, uv run [--flags] pytest, python -m pytest.
PYTEST='(uv\s+run\s+(--\S+\s+)*)?(python3?\s+-m\s+)?pytest'

if grep -Eq '(^|[/[:space:]])(run_eval|approve_trio)\.py(\s|$)' <<<"$cmd"; then
  ask "Billed run: live Gemini + BigQuery (CLAUDE.md: the user runs or approves live checks)."
fi
if at_cmd "$PYTEST" && grep -Eq '(^|\s|=)tests/integration(/|\s|$)' <<<"$cmd"; then
  ask "Billed run: tests/integration calls live Gemini + BigQuery."
fi
if grep -Eq '(^|[;&|(]|\s)uv\s+run\s+(--\S+\s+)*(retail-agent|retail-mcp)(\s|$)' <<<"$cmd" \
   || grep -Eq '(^|[;&|(]|\s)(uv\s+run\s+)?python3?\s+-m\s+retail_agent(\.\w+)?(\s|$)' <<<"$cmd"; then
  ask "Live session: the agent/MCP server calls Gemini + BigQuery for real."
fi
if grep -Eq '(^|[;&|(])\s*(source|\.)\s+\S*\.env(\s|$|[;&|])|(^|\s)set\s+-a(\s|$)|--env-file(\s|=)|(^|[;&|(]|\s)dotenv\s+run|(^|\s)(GOOGLE_CLOUD_PROJECT|GEMINI_API_KEY|GOOGLE_API_KEY)=' <<<"$cmd"; then
  ask "Loads live credentials into the shell, which turns otherwise-offline runs live (billed)."
fi
if at_cmd 'bq' || at_cmd 'gcloud'; then
  ask "Direct GCP call (bq/gcloud): cloud-touching, needs the user's go-ahead."
fi
exit 0
