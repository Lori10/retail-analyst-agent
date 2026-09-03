# Retail Data Analysis Agent

Take-home assignment. Full brief: @docs/assignment.md — read it before doing anything.
Design decisions and HLD live in @docs/design.md (source of truth; update it when a decision changes). Implementation-level detail, rejected alternatives, and gaps found during testing live in @docs/implementation-notes.md — kept separate so design.md stays a fast read.

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
  user. The last two were found by inspecting the live `users` schema — not
  in the assignment brief's original list, added because `user_geom`
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
LangSmith) — four of the assignment's five prototype-eligible
requirements, exceeding the "at least 2 of 5" minimum for deliverable 3.
Docs only: Quality Assurance (golden-set eval harness) — eligible for the
prototype but deliberately left as design only. Also docs only, and never
eligible for the prototype in the first place: Hybrid Intelligence/Golden
Bucket, Continuous Improvement, Agility/Persona Management.

## Workflow
- Design before code. Don't write source until docs/design.md is agreed.
- Work in small vertical slices; each slice runnable end to end.
- Build order: (1) thin end-to-end skeleton — CLI → LangGraph tool-calling
  loop → Gemini → BQ wrapper with PII stripping and cost/timeout/row caps;
  (2) resilience depth — typed errors, bounded self-correct, backoff;
  (3) High-Stakes Oversight — Postgres-backed Saved Reports Store,
  `interrupt()`/`Command(resume=...)`-based confirm-then-delete flow;
  (4) Observability — structured JSON tracing (`tracing.py`, one line per
  LLM call/tool call/turn to stderr in Cloud-Logging-compatible shape) plus
  LangSmith auto-instrumentation (env vars only, off by default);
  (5) polish — docs, clean-machine setup test.
  A QA/eval harness is designed in docs/design.md but deliberately not
  coded.
- Write tests for PII filtering and delete confirmation first.
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

## Provided code
`src/provided/bq_runner.py` was supplied by the company as an example of how
to query BigQuery, not a required dependency. `BigQueryTool` owns its own
`bigquery.Client` directly rather than wrapping it; the file is left in the
repo unused. `BigQueryTool` adds: SQL read-only check, dry-run cost estimate
with a max-bytes cap, query timeout, row limit, and PII column stripping.

## Data
Public dataset, already populated. No ingestion needed. Auth via
`gcloud auth application-default login` plus GOOGLE_CLOUD_PROJECT env var.