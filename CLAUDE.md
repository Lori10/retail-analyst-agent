# Retail Data Analysis Agent

Specs and design live in OpenSpec (`openspec/`), not in standalone docs:
- `openspec/specs/<capability>/spec.md` — behaviour contracts (SHALL + WHEN/THEN scenarios). Source of truth for what the system must do; read the relevant spec before changing a capability.
- `openspec/changes/archive/2026-10-13-baseline-current-system/` — the "baseline design" referred to in code comments: `design.md` holds architecture, diagrams, component reasoning, rejected alternatives and known gaps (the former HLD and implementation notes); `proposal.md` holds how each of the eight requirements is handled and the scope matrix — read it before doing anything.
- `openspec/changes/<name>/` — open changes (designed-only requirements, production deployment). Decisions made by a change are recorded in its own `design.md`, which is archived with it.
- `openspec/config.yaml` — project context and per-artifact rules fed into every new change.

## Constraints
- Python 3.11, uv for deps
- LLM: Gemini only, via `langchain-google-genai`'s `ChatGoogleGenerativeAI`.
  No fallback provider.
- Orchestration: LangGraph (`langgraph` + `langchain-core`). Used for real —
  `interrupt()`/resume for the confirm-before-delete flow, checkpointing for
  conversation state — not added decoratively.
- Data: BigQuery public dataset `bigquery-public-data.thelook_ecommerce`
  (orders, order_items, products, users). Read-only. Never SELECT * from users.
- PII columns (email, first_name, last_name, street_address, latitude,
  longitude, postal_code, user_geom) must never appear in any output to the
  user. The last two were found by inspecting the live `users` schema —
  added because `user_geom`
  (GEOGRAPHY) is a direct point encoding of the same location `latitude`/
  `longitude` carry, and `postal_code` is a standard quasi-identifier.
- CLI chat interface. No web UI.
- Budget: up to ~1 week. Prefer simple and working over clever — the longer
  budget buys more coded scope and polish, not more architectural
  cleverness.

## Prototype scope (requirements implemented in code)
Coded: Safety & PII Masking, Resilience & Graceful Error Handling,
High-Stakes Oversight (Saved Reports Store + interrupt-based
confirm-then-delete), Observability (structured JSON tracing +
LangSmith), Quality Assurance (golden-set eval harness —
`eval/golden_set.json` + `eval.py` + `scripts/run_eval.py`, deterministic
checks + LLM-as-judge, run manually), Hybrid Intelligence/Golden Bucket
(`golden_bucket.py`: analyst-approved trios in pgvector, approved via
`scripts/approve_trio.py`, retrieved per turn by the `retrieve_examples`
node; change `add-golden-bucket`). Designed in docs only: Continuous
Improvement, Agility/Persona Management.

Also coded, beyond the eight requirements: an MCP server
(`mcp_server.py`, `uv run retail-mcp`, stdio, official `mcp` SDK v2
`MCPServer`) exposing the same tools to external MCP clients, with
deletion split into `preview_delete`/`confirm_delete` (single-use,
expiring, owner-bound token) since an MCP client never passes through
the graph's `interrupt()`.
The reverse direction is coded too: an MCP client (`mcp_client.py`,
`McpToolHub`) that lets the CLI agent load tools from external MCP
servers listed in `AGENT_MCP_SERVERS` (a `.mcp.json`-shaped file; see
`mcp_servers.example.json`), built on the `mcp` v2 SDK's own `Client`
because `langchain-mcp-adapters` pins `mcp<2`.

## Workflow
- Spec before code. Every change goes through OpenSpec: `/opsx:explore`
  (optional) → `/opsx:propose` → user reviews and approves the proposal,
  spec deltas, design and tasks → `/opsx:apply` → `/opsx:archive` on the
  feature branch, before merging (code, updated specs and archived change
  land in one merge). Don't write source until the change's artifacts are
  approved. Step-by-step guide with an example: `CONTRIBUTING.md`.
- Run the CLI pinned: `npx @fission-ai/openspec@1.14.1 <cmd>` (e.g.
  `validate --all --strict`, `list --specs`). The generated `/opsx:*`
  skills call a bare `openspec`; use the pinned npx form instead.
- Work in small vertical slices; each slice runnable end to end.
- Build order: (1) thin end-to-end skeleton — CLI → LangGraph tool-calling
  loop → Gemini → BQ wrapper with PII stripping and cost/timeout/row caps;
  (2) resilience depth — typed errors, bounded self-correct, backoff;
  (3) High-Stakes Oversight — Postgres-backed Saved Reports Store,
  `interrupt()`/`Command(resume=...)`-based confirm-then-delete flow;
  (4) Observability — structured JSON tracing (`tracing.py`, one line per
  LLM call/tool call/turn to stderr in Cloud-Logging-compatible shape) plus
  LangSmith auto-instrumentation (env vars only, off by default);
  (5) polish — docs, clean-machine setup test;
  (6) Quality Assurance — golden-set eval harness (`eval/golden_set.json`,
  `eval.py`, `scripts/run_eval.py`): deterministic shape checks + an
  LLM-as-judge rubric, run manually against live Gemini/BigQuery, never
  part of `uv run pytest`'s default run;
  (7) MCP server — the same tools over stdio for external MCP clients,
  every guarantee (PII strip, owner scope, delete confirmation) enforced
  server-side; the CLI agent keeps calling the tools in-process.
  (8) MCP client — the agent as an MCP host: external servers' tools
  bound next to the built-ins, namespaced `<server>__<tool>`, destructive
  tools skipped unless allowlisted, unreachable servers skipped.
  (9) Golden Bucket — saved reports record their source question/SQL;
  an analyst approves trios offline (`scripts/approve_trio.py`); each
  turn retrieves up to 3 similar trios (pgvector, `gemini-embedding-2`)
  as per-turn reference data; any retrieval failure degrades to "no
  examples".
- Tests first: write failing tests from the change's spec scenarios before
  implementation, and never weaken an existing test unless its spec changed.
- Ask before adding a dependency.

## Git workflow
- Never commit or push directly to `main`. All work happens on a feature
  branch created off up-to-date `main` (e.g. `feat/pii-masking`,
  `fix/query-timeout`).
- Merge back into `main` with a simple merge (`git merge --no-ff` or a
  fast-forward via PR) — no rebasing/squashing history unless asked.
- Always ask for explicit confirmation before running `git commit`,
  `git push`, or any merge into `main`. Show what will be committed/merged
  first (diff or file list); don't bundle the confirmation into a single
  "ok to do all of this?" for unrelated steps.
- No `Co-Authored-By: Claude` trailer on commits and no "Generated with
  Claude Code" line on PRs (user decision, 2026-10-13).

## Provided code
`src/provided/bq_runner.py` was supplied by the company as an example of how
to query BigQuery, not a required dependency. `BigQueryTool` owns its own
`bigquery.Client` directly rather than wrapping it; the file is left in the
repo unused. `BigQueryTool` adds: SQL read-only check, dry-run cost estimate
with a max-bytes cap, query timeout, row limit, and PII column stripping.

## Data
Public dataset, already populated. No ingestion needed. Auth via
`gcloud auth application-default login` plus GOOGLE_CLOUD_PROJECT env var.