#!/usr/bin/env bash
# Every offline gate for this repo. Exit non-zero if any fails. Never calls live GCP/Gemini.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0
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
else
  echo "--> none"
fi

exit $fail
