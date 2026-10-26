#!/usr/bin/env bash
# Stop: refuse to end the turn while the offline test suite fails.
# Live tests (tests/integration, scripts/run_eval.py) are never run here.
set -uo pipefail
input=$(cat)
# Already continuing because of this hook: don't loop forever.
[[ "$(jq -r '.stop_hook_active // false' <<<"$input")" == "true" ]] && exit 0

cd "${CLAUDE_PROJECT_DIR:-.}"
# Only gate turns that touched code or tests.
git status --porcelain -- src tests pyproject.toml | grep -q . || exit 0

if ! (exec 3<>/dev/tcp/127.0.0.1/5432) 2>/dev/null; then
  jq -n '{systemMessage: "Stop hook: Postgres is down, so tests were NOT run. Start it with `docker compose up -d postgres`."}'
  exit 0
fi

if ! out=$(timeout 300 uv run --quiet pytest -q -x 2>&1); then
  # Only tests/ changed: the tests-first group's expected red state. Report it, don't block,
  # so the user can review the failing tests before any implementation.
  if ! git status --porcelain -- src pyproject.toml | grep -q .; then
    jq -n --arg t "$(tail -5 <<<"$out")" \
      '{systemMessage: ("Stop hook: tests fail with only tests/ changed (tests-first red state, not blocking):\n" + $t)}'
    exit 0
  fi
  echo "Tests fail — fix them before finishing (never weaken a test to pass):" >&2
  tail -40 <<<"$out" >&2
  exit 2
fi
exit 0
