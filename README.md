# Retail Data Analysis Agent

A production-grade High-Level Design plus a working prototype for an
internal chat agent that lets non-technical Store/Regional Managers ask
natural-language questions over `bigquery-public-data.thelook_ecommerce`,
discuss the results conversationally, and turn them into saved reports with
action items. Built the way a real production agent would be — with typed
error handling,
human-in-the-loop oversight on destructive actions, structured tracing, and
a regression-gate eval harness, not just a happy-path demo.

## What this demonstrates

- **Design → build, not just build.** A full production design
  ([baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md)) covering all 8 requirements, with
  the prototype implementing 6 of them — every coded piece traces back to
  a documented design decision and to a behaviour spec in
  [openspec/specs/](openspec/specs/).
- **Safety & PII masking** — an input-side guardrail against prompt
  injection, an instruction to treat all tool output as untrusted data, and
  schema-verified PII column stripping in the BigQuery wrapper: the
  registry is checked against the *live* `users` schema rather than a
  hand-typed list, and covers two less obvious columns (`postal_code`,
  `user_geom`) such a list would miss.
- **Resilience** — typed, self-correcting error handling: syntax errors get
  fed back to the model to retry with a different query (bounded, never
  unbounded), permission/transient errors go straight to a graceful
  message, and a separate backoff layer handles transient BigQuery/Gemini
  failures beneath the graph entirely.
- **High-stakes oversight** — a Postgres-backed Saved Reports Store with a
  LangGraph `interrupt()`-based confirm-then-delete flow: destructive
  requests ("delete all reports mentioning Client X") pause, show the
  exact candidates, and require an explicit yes before anything is removed.
- **Observability** — structured JSON tracing plus LangSmith
  auto-instrumentation, so every LLM call, tool call, and turn is
  reconstructable end to end. See [Observability](#observability) below.
- **Hybrid intelligence** — a Golden Bucket of analyst-approved
  (question, SQL, report) examples in pgvector: each new question pulls
  in the most similar approved ones as reference for that turn, and a
  report only enters the bucket when an analyst approves it. See
  [Golden Bucket](#golden-bucket) below.
- **Quality Assurance** — a golden-set eval harness with deterministic
  checks and an LLM-as-judge rubric, run as a manual regression gate. See
  [Quality Assurance](#quality-assurance) below.

## Architecture

```mermaid
flowchart TB
    subgraph Client["Client"]
        CLI["CLI Chat Interface\n(prototype)"]
    end

    subgraph Agent["Agent Service\n(Cloud Run in prod / local process in prototype)"]
        Orchestrator["Orchestrator\nLangGraph: tool-calling loop +\ninterrupt()-based confirm-before-delete"]
        Provider["LLM Provider\nlangchain-google-genai\nChatGoogleGenerativeAI"]
        BQWrap["BigQuery Tool Wrapper\nread-only check, dry-run cap,\ntimeout, row limit, PII strip"]
    end

    subgraph LLMs["LLM Provider"]
        Gemini["Gemini\nVertex AI (prod) /\nAI Studio (prototype)"]
    end

    subgraph Data["Data & Knowledge"]
        BQ[("BigQuery\nthelook_ecommerce\n(read-only)")]
        Checkpoints[("Conversation Checkpoint Store\nMemorystore for Redis (prod)\nInMemorySaver (prototype)")]
        Conversations[("Conversation Store\nPostgres — Cloud SQL (prod) /\nsame database as Reports (prototype) — coded")]
        GoldenBucket[("Golden Bucket\npgvector on Cloud SQL (prod) /\nsame database as Reports (prototype) — coded")]
        Reports[("Saved Reports Store\nPostgres — Cloud SQL (prod) /\ndocker-compose (prototype) — coded")]
        Prefs[("User Preference Store\nFirestore (prod) —\ndocs-only in prototype")]
        Persona[("Persona Config\nCloud Storage/Firestore (prod)\nlocal persona.yaml (design reference)")]
    end

    subgraph Obs["Observability — coded"]
        Logs["Structured Logs\nCloud-Logging-shaped JSON on stderr —\nsame code, prototype and prod"]
        Trace["LLM/Agent Tracing\nLangSmith (free Developer tier) —\nenv-var auto-instrumentation, no code"]
        Dash["Dashboards & Alerts\nCloud Monitoring (prod only)"]
    end

    CLI --> Orchestrator
    Orchestrator --> Provider
    Provider --> Gemini
    Orchestrator --> BQWrap
    BQWrap --> BQ
    Orchestrator --> Checkpoints
    Orchestrator --> Conversations
    Orchestrator --> GoldenBucket
    Orchestrator --> Reports
    Orchestrator --> Prefs
    Orchestrator --> Persona
    Orchestrator --> Logs
    Logs --> Trace
    Trace --> Dash
```

*The production architecture — everything with "coded" in its label is
running in this prototype today; the rest (Persona Config, Preference
Store) is designed in full in
the [baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md) and tracked as open changes in
[openspec/changes/](openspec/changes/), but out of scope for the prototype.*

There's a second diagram worth knowing about:
[the graph structure in the Golden Bucket change's design](openspec/changes/add-golden-bucket/design.md#graph-structure-after-this-change-generated)
(which supersedes the [baseline's](openspec/changes/archive/2026-10-13-baseline-current-system/design.md#current-graph-structure-generated-not-hand-drawn))
is generated directly from the compiled LangGraph `StateGraph`
(`graph.get_graph().draw_mermaid()`, regenerated with
`uv run python scripts/render_graph.py`), not hand-drawn — it shows exactly
what's running today and can't silently drift from the code the way a
hand-maintained diagram can.

## Coded vs. designed

| # | Requirement | Status |
|---|---|---|
| 1 | Hybrid Intelligence (Golden Bucket) | **Coded** |
| 2 | Safety & PII Masking | **Coded** |
| 3 | High-Stakes Oversight | **Coded** |
| 4 | Continuous Improvement | Designed only |
| 5 | Resilience & Error Handling | **Coded** |
| 6 | Quality Assurance | **Coded** |
| 7 | Observability | **Coded** |
| 8 | Agility (Persona Management) | Designed only |

6 of 8 requirements are coded. The other 2 are designed in full but left
out of the prototype. Hybrid Intelligence was added after the baseline by
the [add-golden-bucket](openspec/changes/add-golden-bucket/) change.
Full reasoning and trade-offs for every row:
[baseline proposal](openspec/changes/archive/2026-10-13-baseline-current-system/proposal.md#prototype-vs-production-scope-matrix).

## Setup & example run

Three independent things need to be configured, and they have different
prerequisites — Gemini is a single API key with nothing else to install;
BigQuery needs the `gcloud` CLI and a real GCP project, even though the
dataset being queried (`thelook_ecommerce`) is public; the Saved Reports
Store and the Conversation Store both need a running Postgres, provided
locally via `docker-compose.yml` — the same instance and database, so
this is still one thing to set up, not two.

### 1. Python environment

- Python 3.11 — not required to be pre-installed: `uv sync` reads
  `.python-version` and fetches a matching interpreter automatically if
  none is found, via `uv`'s own Python management
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed

### 2. Gemini (LLM provider)

- Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey)
- No other setup — this is just `GEMINI_API_KEY` in `.env`

### 3. BigQuery (data source)

- Install the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) if
  you don't already have it
- Have (or create) a GCP project with the BigQuery API enabled and billing
  active — required even though `thelook_ecommerce` is a public dataset,
  because dry-run cost estimation and query execution both run as *your*
  project, not the dataset owner's
- Run `gcloud auth application-default login` once (opens a browser) to
  create local Application Default Credentials
- Set `GOOGLE_CLOUD_PROJECT` in `.env` to that project's ID

### 4. Saved Reports Store & Conversation Store (Postgres)

- Requires Docker (or an already-running Postgres — see below)
- `docker compose up -d postgres` starts a local Postgres
  (`pgvector/pgvector:pg16` — Postgres 16 with the pgvector extension the
  Golden Bucket needs) with two databases: `retail_agent_reports` (what
  the CLI uses) and `retail_agent_reports_test` (what the test suite uses,
  so running tests never truncates reports/conversations saved during
  interactive use)
- Upgrading from the earlier `postgres:16-alpine` image: the same `pgdata`
  volume works, but the collation changes from musl to glibc, so rebuild
  the text indexes once —
  `docker compose exec postgres psql -U retail_agent -d retail_agent_reports -c 'REINDEX DATABASE retail_agent_reports'`
  (and the same for `retail_agent_reports_test`). If you don't need the
  local reports/conversations, `docker compose down -v` and starting
  fresh does the same.
- `ReportsStore` and `ConversationStore` each create their own tables on
  first connect (`CREATE TABLE IF NOT EXISTS`) — no separate migration
  step, and no separate connection string: both point at
  `REPORTS_DATABASE_URL`
- No Docker? Point `REPORTS_DATABASE_URL` at any reachable Postgres
  instance instead (a local install, Cloud SQL, etc.) — the default in
  `config.py` matches the `docker-compose.yml` credentials
  (`postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports`)

```bash
gcloud auth application-default login
cp .env.example .env   # fill in GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY
# or export them directly — .env is picked up automatically (python-dotenv),
# but real environment variables always take precedence over it.

docker compose up -d postgres
uv sync
uv run retail-agent
```

### Troubleshooting

 a `PermissionDenied`/403 on startup or on the first
query almost always means one of the BigQuery-specific steps above was
skipped — either the BigQuery API isn't enabled on the project named by
`GOOGLE_CLOUD_PROJECT`, billing isn't active on it, or
`gcloud auth application-default login` was never run (or was run for a
different account/project than the one in `.env`). Gemini-side failures
(invalid/missing `GEMINI_API_KEY`) surface as a graceful provider error
message rather than a crash — see [openspec/specs/error-resilience](openspec/specs/error-resilience/spec.md). A reports-store or conversation-store
connection failure (Postgres not running, wrong `REPORTS_DATABASE_URL`)
surfaces as a startup error naming `docker compose up -d postgres` as the
likely fix — both stores share that same connection string, so they fail
together.

Optional env vars (defaults shown): `GEMINI_MODEL=gemini-3.6-flash`,
`BQ_MAX_BYTES_BILLED=1000000000` (~1GB), `BQ_ROW_LIMIT=500`,
`BQ_QUERY_TIMEOUT_SECONDS=30`, `LOG_LEVEL=INFO`,
`REPORTS_DATABASE_URL=postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports`,
`TRACE_LOG_DESTINATION=stderr` ([Observability](#observability)'s structured JSON events;
also accepts `stdout` or a file path), `AGENT_MCP_SERVERS` (unset; a path to
a `.mcp.json`-shaped file of external MCP servers — [baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md), "MCP Client"),
`GOLDEN_EMBEDDING_MODEL=gemini-embedding-2`, `GOLDEN_MIN_SIMILARITY=0.75`,
`GOLDEN_BUCKET_TABLE=golden_trios` ([Golden Bucket](#golden-bucket)).

LangSmith conversation-level tracing (see [Observability](#observability)) is optional and
off by default — no signup needed to run the CLI at all. To turn it on,
sign up at [smith.langchain.com](https://smith.langchain.com) (free
Developer tier, no credit card) and set `LANGSMITH_TRACING=true`,
`LANGSMITH_API_KEY=...`, `LANGSMITH_PROJECT=retail-agent` — LangChain's
own tracing hooks pick these up automatically; nothing else changes.

Run the test suite: `uv run pytest`. This runs the unit tests by default,
including `test_reports_store.py` and `test_conversation_store.py` —
unlike every other unit test file, those two need a real Postgres
reachable at `REPORTS_DATABASE_URL` (or its default, matching
`docker-compose.yml`'s `retail_agent_reports_test` database) to pass,
since neither `ReportsStore` nor `ConversationStore` has a fake/mock
double the way `BigQueryTool`/`GeminiProvider` do in
`test_graph.py`/`test_cli.py` — run `docker compose up -d postgres`
first. `tests/integration/` — live
regression checks against real BigQuery/Gemini (PII stripping holds even
when explicitly selected, and one end-to-end smoke question) —
deliberately requires `GOOGLE_CLOUD_PROJECT`/`GEMINI_API_KEY` as real
*exported* environment variables, not just values in `.env`, so `uv run
pytest` never silently runs live, billed calls just because `.env`
happens to be configured for the CLI. To run them explicitly:
`set -a && source .env && set +a && uv run pytest tests/integration`.

Example session:

```
> Why did our churn rate spike last month?
[agent generates SQL, queries BigQuery read-only, strips PII, returns an
 analysis]

> Turn that into a report with action items for next quarter, and save it
[agent formats the analysis as a report — summary, key findings, action
 items — and calls save_report to persist it to the Saved Reports Store]

> Delete that report
This would delete the following report(s):
  - [1] Churn Spike — Q_ Action Items (saved 2026-...)
Type 'yes' to confirm deletion, or anything else to cancel.
> yes
Agent: Deleted 1 report(s).
```

Type `exit` or `quit` to leave the REPL (Ctrl-D/Ctrl-C also work).
Running `uv run retail-agent` again afterward resumes this same
conversation rather than starting blank: `main()` loads it from the
Conversation Store and prints `Resuming previous conversation (N
message(s) loaded).` before the prompt — see the [baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md) Conversation Store
and Resume path.

### Using the MCP server

 Needs the same BigQuery and Postgres setup as
above, but not `GEMINI_API_KEY` — the connecting client's own model does
the reasoning. The repo's `.mcp.json` registers it for Claude Code
automatically (open the repo in Claude Code and approve the
`retail-analyst` server); any other client runs the same command:

```bash
uv run retail-mcp                                         # stdio server
npx @modelcontextprotocol/inspector uv run retail-mcp     # browse/call tools in a web UI
```

### Giving the agent external MCP tools

 Point `AGENT_MCP_SERVERS` at a
server list. The bundled example runs the official time server through
`uvx` (shipped with `uv`, so there's nothing extra to install):

```bash
AGENT_MCP_SERVERS=mcp_servers.example.json uv run retail-agent
# Loaded 1 external tool(s) from 1 of 1 MCP server(s).
> What was total revenue last month?
Calling time__get_current_time...
Running a query...
```

An external server's own stderr is hidden unless `LOG_LEVEL=DEBUG`.

### Golden Bucket

When the agent saves a report, it records the question and the
successful SQL that produced it (captured by the graph, never written by
the model). An analyst then promotes good reports into the Golden Bucket:

```bash
uv run python scripts/approve_trio.py --list            # recent reports, all owners
uv run python scripts/approve_trio.py --report-id 42    # review, confirm, store
uv run python scripts/approve_trio.py --report-id 42 \
    --question "Revenue by category last month?" \
    --sql-file edited.sql --report-file edited.md       # approve with edits
```

Edited SQL must pass the same read-only check as agent queries, and the
question and report are rejected if they contain an email-shaped string.
Re-approving a report stores a new version and retires the old one.
Reports saved over MCP (or saved before this feature) have no recorded
SQL and need `--sql-file`.

On each question, the agent embeds it, takes up to 3 approved trios at or
above `GOLDEN_MIN_SIMILARITY`, and gives them to the model for that turn
only — never stored in the conversation. If the bucket or the embeddings
call is unavailable, the turn simply runs without examples. Each `turn`
trace event carries `golden_examples` (count), `golden_top_similarity` and
`golden_error_class`, but never trio text. Design and rejected
alternatives: [add-golden-bucket design](openspec/changes/add-golden-bucket/design.md).

### Inspecting internals (PII stripping, self-correct)

 `retail-agent`
prints only the final `Agent: ...` answer per turn on stdout — enough to
use the agent, not enough to see the coded requirements actually fire.
The CLI's own stderr output (see [Observability](#observability)) is the machine-readable
window into that: one JSON line per LLM call/tool call/turn, with
`latency_ms`, `tokens`, `rows_returned`, `sql`, `error_class`,
`self_correct_attempt`, etc., emitted live during any normal
`retail-agent` session (`TRACE_LOG_DESTINATION` redirects it to a file
instead, if preferred over reading stderr directly):

```bash
uv run retail-agent 2>trace.jsonl
# ... use the CLI normally in this terminal; tail -f trace.jsonl elsewhere ...
```

For inspecting full message *content* rather than metrics (why a
self-correct retry happened, exactly what a tool call returned
post-PII-strip), a LangSmith trace (if configured) gives the richest
view; short of that, `logging.basicConfig`'s existing console output
(`LOG_LEVEL=DEBUG`) and the ad hoc `logger.warning(...)` call sites
(`bq_call_failed`, `tool_call_error`, etc.) are what's left to read.

## Observability

Every LLM call, tool call, and turn emits one structured JSON line
(`tracing.py`), in the shape Cloud Logging auto-parses from stdout/stderr —
the same code path runs in the prototype and would in production:

```json
{"severity": "INFO", "message": "tool_call", "conversation_id": "cli-session-lori28", "turn_id": "…", "tool": "run_query", "sql": "SELECT …", "rows_returned": 7, "latency_ms": 412.3, "error_class": null, "self_correct_attempt": 0}
```

Every line carries `conversation_id`, so a full exchange — including any
self-correct retries — can be reconstructed by filtering on that field:

```bash
uv run retail-agent 2>trace.jsonl
# use the CLI normally in this terminal; tail -f trace.jsonl elsewhere
```

For a conversation-level view without grepping JSON, set
`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` (free Developer tier, no
credit card — see `.env.example`) and every run auto-instruments through
LangSmith with zero code changes. Full reasoning, including why LangSmith
was chosen over a self-hosted tracer for this prototype (and when that
choice should flip):
[baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md) ("Observability" and "Observability: LangSmith
Instead of Self-Hosted Langfuse"); behaviour contract in
[openspec/specs/observability](openspec/specs/observability/spec.md).

## Quality Assurance

A golden-set eval harness (`eval/golden_set.json`) runs 10 hand-curated
questions — one per capability the agent is meant to cover — through the
real compiled graph and scores each answer two ways: cheap deterministic
checks first (was the expected tool called, did the SQL touch an expected
table, does the answer mention an expected keyword, no PII-shaped text,
and for the Golden Bucket case, was a seeded example retrieved),
then an LLM-as-judge pass scoring whether the answer addresses the question
asked, whether its claims are internally plausible, and whether it leaks
PII.

```bash
uv run python scripts/run_eval.py [--golden-set eval/golden_set.json] [--out eval_results.json] [--fail-under 0.8]
```

This makes real, billed Gemini + BigQuery calls, so — like
`tests/integration/` — it's a deliberate manual action, never run by
`uv run pytest`. It uses its own Golden Bucket table (`golden_trios_eval`),
reset before every case and seeded only with that case's `seed_trios`, so
trios approved for real use never influence eval results.
`--fail-under` exits non-zero below a pass-rate threshold,
for wiring into CI later without further code changes. Full reasoning and
accepted gaps (sample size, no ground-truth SQL diffing, single-run
variance):
[baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md) ("Quality Assurance: Golden-Set Eval Harness
Implementation Notes"); behaviour contract in
[openspec/specs/eval-harness](openspec/specs/eval-harness/spec.md).

## MCP server

The same data and reports tools are also available to any MCP client —
Claude Code, Claude Desktop, MCP Inspector — through a stdio MCP server:

```bash
uv run retail-mcp                                         # what a client launches
npx @modelcontextprotocol/inspector uv run retail-mcp     # try the tools in a browser
```

Opening this repo in Claude Code picks it up from `.mcp.json`. It needs
BigQuery and Postgres as above, but no Gemini key — the client's model
does the reasoning. Every guarantee is enforced under the tool, not left
to the client: PII columns are stripped server-side, reports are
owner-scoped, and deletion is two calls — `preview_delete` returns the
exact candidates plus a short-lived token, and `confirm_delete(token)`
deletes exactly those, once. Design and trade-offs:
[baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md) ("MCP Server" and "MCP Server Implementation
Notes"); behaviour contract in
[openspec/specs/mcp-server](openspec/specs/mcp-server/spec.md).

## Testing

```bash
uv run pytest
```

Needs a local Postgres (`docker compose up -d postgres`) for
`test_reports_store.py`/`test_conversation_store.py`. `tests/integration/`
(live BigQuery/Gemini regression checks) and the eval harness above are
both opt-in only — they require real *exported* `GOOGLE_CLOUD_PROJECT`/
`GEMINI_API_KEY` environment variables, not just a configured `.env`, so
`uv run pytest` never makes a live, billed call by accident. See
[Setup](#setup--example-run) for how to run them explicitly.

## Specs & design (OpenSpec)

This project uses [OpenSpec](https://github.com/Fission-AI/OpenSpec)
(pinned `@fission-ai/openspec@1.14.1`, needs Node ≥ 20.19) for spec-driven
changes:

- [openspec/specs/](openspec/specs/) — what the system must do: one
  behaviour spec per capability (SHALL requirements + WHEN/THEN scenarios).
  Source of truth for behaviour.
- [Baseline design](openspec/changes/archive/2026-10-13-baseline-current-system/design.md) —
  architecture, diagrams, component reasoning, data flow, error handling,
  rejected alternatives and known gaps for the system as built (the former
  HLD and implementation notes).
- [Baseline proposal](openspec/changes/archive/2026-10-13-baseline-current-system/proposal.md) —
  requirement-by-requirement coverage and the prototype vs. production
  scope matrix.
- [openspec/changes/](openspec/changes/) — open changes: the Golden Bucket
  (implemented, awaiting archive), the designed-only requirements (user
  preferences, persona config) and the production deployment.
- [openspec/config.yaml](openspec/config.yaml) — project constraints and
  per-artifact rules applied to every new change.
- [CLAUDE.md](CLAUDE.md) — build constraints and workflow.

Every change goes through `/opsx:propose` → human review → `/opsx:apply`
→ `/opsx:archive` in Claude Code. CLI checks:

```bash
npx @fission-ai/openspec@1.14.1 list --specs
npx @fission-ai/openspec@1.14.1 validate --all --strict
```
