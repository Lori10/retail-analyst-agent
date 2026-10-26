#!/usr/bin/env bash
# Every offline gate for this repo. Exit non-zero if any fails. Never calls live GCP/Gemini.
#   --if-stale  skip the gates when this exact clean tree already passed (used by /land-branch).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0

# A full pass on a clean tree stamps HEAD's tree hash; uncommitted work never gets a stamp.
stamp="$(git rev-parse --git-dir)/verify-pass"
tree=""
[[ -z "$(git status --porcelain)" ]] && tree=$(git rev-parse 'HEAD^{tree}')
if [[ "${1:-}" == "--if-stale" && -n "$tree" && "$(cat "$stamp" 2>/dev/null)" == "$tree" ]]; then
  echo "=== already verified: tree $tree passed every gate; nothing changed since"
  exit 0
fi
rm -f "$stamp"
gate() {
  local name=$1; shift
  echo "=== $name"
  if out=$("$@" 2>&1); then echo "$out" | tail -3; echo "--> PASS"
  else echo "$out" | tail -40; echo "--> FAIL"; fail=1; fi
}

gate "ruff" uv run --quiet ruff check src tests scripts eval
if (exec 3<>/dev/tcp/127.0.0.1/5432) 2>/dev/null; then
  gate "pytest" uv run --quiet pytest -q
else
  echo "=== pytest"; echo "Postgres not reachable on 127.0.0.1:5432 — run: docker compose up -d postgres"
  echo "--> FAIL"; fail=1
fi
gate "openspec validate" npx --yes @fission-ai/openspec@1.14.1 validate --all --strict

echo "=== weakened-test scan (vs main)"
suspects=$(git diff main -- tests/ | grep -E '^-\s*assert|^\+.*(pytest\.mark\.(skip|skipif|xfail)|pytest\.skip\(|pytest\.xfail\()|^-\s*def test_' || true)
if [[ -n "$suspects" ]]; then
  echo "$suspects"; echo "--> REVIEW (read these hunks; see reference.md)"
  # Judgment call, so not a failure; surface it on the PR in CI.
  [[ -n "${GITHUB_ACTIONS:-}" ]] && echo "::warning title=Possibly weakened test::Review the tests/ hunks listed in the check.sh log (removed assert/test or added skip/xfail)."
else
  echo "--> none"
fi

# A pass with test hunks still to review isn't stamped: /verify has to read them first.
[[ $fail -eq 0 && -z "$suspects" && -n "$tree" ]] && echo "$tree" > "$stamp"
exit $fail
