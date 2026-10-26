# Retail Data Analysis Agent

CLI chat agent (LangGraph + Gemini) over BigQuery `bigquery-public-data.thelook_ecommerce`
for non-technical store managers. Also an MCP server (`mcp_server.py`, `uv run retail-mcp`)
and MCP client (`mcp_client.py`, `AGENT_MCP_SERVERS`).

## Where things are
- `openspec/specs/<capability>/spec.md` — what the system must do. Read the relevant spec before changing a capability.
- `openspec/changes/archive/2026-10-13-baseline-current-system/` — the "baseline design" cited in code comments. `proposal.md` has the eight requirements, what is coded vs designed only, and the scope matrix. Read it before starting work. `design.md` has architecture, rejected alternatives and known gaps.
- `openspec/changes/<name>/` — open changes. Each change records its decisions in its own `design.md`. `add-golden-bucket` is merged but not archived yet; its live checks 7.1–7.2 are still open.
- `CONTRIBUTING.md` — the change loop, step by step.

## Constraints
- Python 3.11. Manage deps with `uv`, not pip.
- LLM: Gemini only, via `langchain-google-genai` `ChatGoogleGenerativeAI`. Add no fallback provider.
- LangGraph is used for real: `interrupt()`/`Command(resume=...)` for confirm-before-delete, checkpointing for conversation state. Don't add graph nodes decoratively.
- BigQuery is read-only. **IMPORTANT:** query `users` with an explicit column list, never `SELECT *`.
- **IMPORTANT:** PII columns `email, first_name, last_name, street_address, latitude, longitude, postal_code, user_geom` never appear in any output, log, trace or MCP response. Strip them in `BigQueryTool`, not in the prompt. `user_geom` encodes the same point as lat/long; `postal_code` is a quasi-identifier.
- Every MCP guarantee (PII strip, owner scope, delete confirmation) is enforced server-side. MCP deletion is `preview_delete` → `confirm_delete` with a single-use token, because an MCP client never passes through `interrupt()`.
- CLI only, no web UI. Prefer simple and working over clever.
- `src/provided/bq_runner.py` is a company example kept verbatim and unused. `BigQueryTool` owns its own `bigquery.Client`.

## Workflow
- Spec before code: `/opsx:propose` → user approves the artifacts → `/opsx:apply` → `/verify` → `/opsx:archive` on the branch → `/finish-branch`. Don't write source until the artifacts are approved.
- Tests first: write failing tests from the change's spec scenarios, then implement. Never weaken an existing test unless its spec changed (`/verify` scans for this).
- `uv run pytest` is offline only and needs `docker compose up -d postgres`. Live checks (`tests/integration`, `scripts/run_eval.py`) are billed; the user runs or approves them.
- Lint: `uv run ruff check` (E9 + F only).

## Enforced by hooks (`.claude/hooks/`, wired in `.claude/settings.json`)
- `guard_git.sh`: no commit or push on `main`, no force-push, rebase, `--no-verify` or `gh pr merge --admin`; a push adding secret-looking files asks; every merge asks. Commits and feature-branch pushes run without asking (user decision, 2026-10-26).
- `guard_deps.sh`: adding, removing or editing a dependency asks first.
- `pin_openspec.sh`: a bare `openspec` is rewritten to `npx @fission-ai/openspec@1.14.1`.
- `lint_py.sh` (after each edit) and `stop_tests.sh` (pytest before a turn ends).

## Git
- Branch off up-to-date `main` (`feat/…`, `fix/…`, `chore/…`, `docs/…`). Commit after each task group that passes `/verify`. `main` is protected on GitHub (PR + green `checks` required, no bypass); land work with `/finish-branch`. No rebase or squash unless asked.
- No `Co-Authored-By: Claude` trailer on commits and no "Generated with Claude Code" line on PRs (user decision, 2026-10-13).

## Data
Public dataset, already populated. Auth: `gcloud auth application-default login` plus `GOOGLE_CLOUD_PROJECT`.
