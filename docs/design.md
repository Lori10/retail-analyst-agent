# Retail Data Analysis Agent — High-Level Design

Source of truth for architecture and design decisions. Update this file
whenever a decision changes; see `src/provided/CLAUDE.md` for build
constraints and current prototype scope, `docs/assignment.md` for the brief.

## 1. Overview

An internal chat agent for non-technical Store/Regional Managers to ask
natural-language questions over `bigquery-public-data.thelook_ecommerce`,
discuss results conversationally, and save reports with action items. The
design below is a production HLD; the prototype implements a subset (see
§9) chosen to demonstrate the requirements that are actually gradeable as
working code, while every requirement is designed in full here regardless
of whether it's coded.

## 2. Architecture Diagram

```mermaid
flowchart TB
    subgraph Client["Client"]
        CLI["CLI Chat Interface\n(prototype)"]
    end

    subgraph Agent["Agent Service\n(Cloud Run in prod / local process in prototype)"]
        Orchestrator["Orchestrator\nLangGraph: tool-calling loop +\ninterrupt()-based confirm-before-delete"]
        Provider["LLM Provider Interface"]
        BQWrap["BigQuery Tool Wrapper\nread-only check, dry-run cap,\ntimeout, row limit, PII strip"]
    end

    subgraph LLMs["LLM Providers"]
        Gemini["Gemini\nVertex AI (prod) /\nAI Studio (prototype)"]
        OpenRouter["OpenRouter\n(fallback)"]
    end

    subgraph Data["Data & Knowledge"]
        BQ[("BigQuery\nthelook_ecommerce\n(read-only)")]
        GoldenBucket[("Golden Bucket\nVertex AI Vector Search / pgvector (prod)\nlocal JSON + cosine (design reference)")]
        Reports[("Saved Reports Store\nCloud SQL (prod) /\nSQLite (prototype)")]
        Prefs[("User Preference Store\nFirestore (prod) —\ndocs-only in prototype")]
        Persona[("Persona Config\nCloud Storage/Firestore (prod) /\nlocal persona.yaml (prototype)")]
    end

    subgraph Obs["Observability"]
        Logs["Structured Logs\nCloud Logging (prod) /\nJSON file (prototype)"]
        Trace["LLM/Agent Tracing\nLangfuse (prod, self-hosted) —\nnative LangGraph callback"]
        Dash["Dashboards & Alerts\nCloud Monitoring (prod)"]
    end

    CLI --> Orchestrator
    Orchestrator --> Provider
    Provider --> Gemini
    Provider -. "circuit breaker on failure" .-> OpenRouter
    Orchestrator --> BQWrap
    BQWrap --> BQ
    Orchestrator --> GoldenBucket
    Orchestrator --> Reports
    Orchestrator --> Prefs
    Orchestrator --> Persona
    Orchestrator --> Logs
    Logs --> Trace
    Trace --> Dash
```

## 3. Component Reasoning

**Orchestrator — LangGraph.** Chosen over a hand-rolled loop because two
requirements map directly onto its primitives rather than needing bespoke
state machines: `interrupt()`/resume implements the confirm-before-delete
flow (requirement 3) as a pause-and-resume in the graph, and checkpointing
gives real conversation-state persistence (relevant to requirement 4's
memory story). It's used for these mechanisms specifically, not adopted
decoratively — the architecture diagram maps ~1:1 onto actual graph nodes.

**LLM provider — Gemini primary, OpenRouter fallback, behind a common
interface.** Gemini via Vertex AI in production (shares IAM/audit/quota
plumbing with the BigQuery access already required); AI Studio key in the
prototype for simplicity. OpenRouter sits behind the same interface as a
fallback; a circuit breaker routes to it after N consecutive Gemini
failures/timeouts, with a cooldown before retrying Gemini — this is the
concrete mechanism for "resilient to 3rd-party service downtime"
(requirement 5).

**BigQuery tool wrapper.** `src/provided/bq_runner.py` was supplied by the
company as an example of how to query BigQuery, not a required dependency —
it's left in the repo unused. `BigQueryTool` owns a `bigquery.Client`
directly instead of wrapping it. The raw client (and `bq_runner.py` alike,
since it's a thin pass-through over the same client) has no cost, safety,
PII, or resilience behavior:

- *Cost*: no dry-run/bytes estimate, no max-bytes cap, no row limit
  (an unbounded `.to_dataframe()` pulls the full result set), no timeout on
  `query_job.result()` — a runaway query has no ceiling and can hang.
- *Safety*: SQL is passed straight to `client.query()` with no
  statement-type check — nothing stops DML/DDL or multi-statement input at
  the application layer.
- *PII*: query results return whatever columns are selected, verbatim,
  with no masking; schema lookups expose PII column names/types with
  no sensitivity flag.
- *Resilience*: a bare client call raises whatever the underlying library
  raises, collapsing syntax errors, permission errors, quota errors, and
  transient network failures into one shape — a caller can't tell "retry"
  from "fix the SQL" from "give up" without re-parsing the raw exception.

The wrapper therefore adds: a SQL read-only keyword/regex allowlist
(SELECT/WITH only, reject DML/DDL keywords and multi-statement input) with
an IAM read-only role on the service account as the real backstop — an AST
parser was considered and rejected as over-building against a threat model
IAM already covers; `dry_run=True` plus a ~1GB max-bytes cap (generous
against thelook_ecommerce's actual table sizes, well under the 1TB/month
free tier); a query timeout; a row limit; PII column stripping applied
post-execution, matching each returned column's bare name (case-insensitive,
table-qualifier stripped) against a maintained PII field registry — and typed
error classification (syntax/bad-request, permission, transient/timeout,
empty-result) so the orchestrator can decide retry vs. self-correct vs.
give-up instead of re-parsing a bare exception.

This is name-based matching, not a true schema-driven allow-list: it can't
tell that `first_name AS x` is PII once renamed, and it doesn't
automatically catch a column added to the live schema tomorrow that isn't
in the registry yet. Both gaps are accepted trade-offs, not oversights — a
real allow-list (only pass through columns from a pre-approved safe list)
would also silently drop legitimate computed/aggregated columns
(`SUM(sale_price) AS total_revenue`), which an analysis agent needs to
return constantly, so it isn't viable here without full SQL parsing (which
§3 already rejects as over-building). The renaming gap is covered by
defense-in-depth (the input-side guardrail and the IAM read-only role), not
by this function. The registry itself was verified against the live
`users` schema during prototype development, not just typed from the brief
— that check caught two columns absent from the assignment's original PII
list (`postal_code`, `user_geom`, a GEOGRAPHY point encoding the same
location `latitude`/`longitude` carry) before they could leak, which is
exactly the schema-drift scenario this control needs to survive; in
production this check should be a scheduled job diffing the live schema
against the registry, not a one-time manual pass.

**Golden Bucket.** Question embedded at query time (`text-embedding-004`),
top-k (e.g. k=3) cosine/ANN retrieval against Vertex AI Vector Search
(pgvector on Cloud SQL is an acceptable cheaper alternative) — retrieved
(question, sql, report) trios are injected as few-shot context before SQL
generation and again as style/structure cues before report writing. Update
path is human-curated, not automatic: a trio is appended only after a
human analyst approves or edits the generated report, specifically to
avoid feedback-loop drift where an unreviewed mistake compounds into
future retrievals. Periodic maintenance: dedup near-identical trios,
down-weight/archive trios past a freshness horizon (e.g. 12 months, since
"why did revenue rise" from a year ago may not reflect current dynamics),
and version trios rather than overwrite them in place.

**Saved Reports Store.** SQLite in the prototype, Cloud SQL/Postgres in
production, identical schema: `id, owner, title, content, conversation_id,
created_at, tags`. This gives real queryable delete-scoping (`WHERE owner =
? AND content LIKE ?`), which is what the confirm-then-delete flow in
requirement 3 actually needs rather than asserts in prose.

**User Preference Store.** Firestore doc per manager (preferred format,
analysis depth, updated_at), read into the system prompt each turn, written
after an explicit ("just give me the numbers") or recurring implicit
preference signal. Docs-only in the prototype — not in the assignment's
prototype-eligible requirement list.

**Persona Config.** A single versioned instruction document external to
code — a Firestore row or Cloud Storage file, editable through a small
internal admin surface with no engineering ticket, read fresh each session.
This is the concrete mechanism for "CEO changes tone weekly, no redeploy"
(requirement 8).

**Observability.** One structured JSON log line per LLM call / tool call /
turn (conversation_id, user, intent, sql, rows_returned, tokens,
latency_ms, error_class, self_correct_attempt). Prototype writes this shape
to stdout/a local file — this is the coded, zero-setup mechanism a reviewer
can see just by running the CLI, and it's what satisfies requirement 7 in
the prototype. Production ships the identical log shape to Cloud Logging
(metrics/alerting substrate) *and* adds real cross-call conversation
tracing via **Langfuse**, chosen specifically because it plugs into
LangGraph as a native callback handler — no hand-rolled OpenTelemetry spans
needed, since the orchestration framework already emits everything a
tracer needs. Self-hosted Langfuse over LangSmith: this system already
treats PII as something that must never leave its boundary, and a trace of
prompts/SQL/tool output is exactly the kind of payload you don't want
handed to a third-party SaaS by default; self-hosting keeps trace data in
the same infra boundary as everything else. (LangSmith remains a valid
alternative if the org already has LangChain-ecosystem buy-in.) Whatever
tracer is used, it must trace **post-PII-strip** data only — tracing raw
BigQuery output would reopen the exact leak the wrapper's PII stripping
closes. Cloud Monitoring dashboards/alerts sit on top of the Cloud Logging
stream for error rate, self-correct rate, latency, PII-block rate,
delete-confirmation rate, and daily cost; Langfuse covers the
conversation-level "what exactly happened in this exchange" deep-dive that
raw metrics can't.

## 4. Data Flow

Two paths through the same orchestrator:

**Q&A / analysis path**: user message → lightweight guardrail check
(analysis vs. off-topic/malicious vs. delete-intent) → embed question,
retrieve top-k Golden Bucket trios → LLM generates SQL using trios + schema
as context → BQ wrapper validates (read-only check) → dry-run (reject over
cap) → execute with timeout + row limit → PII-strip the DataFrame → LLM
synthesizes the report/answer using persona config + (prod: user
preferences) → response returned to the client and logged.

**Delete path**: user message → LLM resolves the request into candidate
report(s) via a store query scoped to the requesting user (never
cross-user) → orchestrator lists the exact candidates and pauses via
`interrupt()` → next user turn: "yes" resumes the graph and executes the
delete against the store; anything else aborts. Both outcomes are logged.
Non-mutating actions (e.g. "show me my reports") never trigger this pause —
only the delete itself does, keeping the added friction to one turn.

## 5. Error Handling & Fallback Strategies

- The BQ wrapper raises typed errors instead of bare exceptions, fixing the
  raw-client gap described in §3.
- **Syntax/bad-request** → the error text is fed back to the LLM, bounded
  to 2 self-correct retries, then a graceful "couldn't produce a valid
  query" message — never a raw stack trace, never an unbounded retry loop
  that inflates cost.
- **Empty result** → one extra pass asking the model to sanity-check its
  own query logic before "zero rows" is accepted as the real answer.
- **Transient/timeout** → exponential backoff, 2 attempts, before
  surfacing a "try again shortly" message.
- **LLM provider failure** → circuit breaker switches to OpenRouter for a
  cooldown window; logged as a provider-failover event.
- Every failure path is logged with its error class. No path crashes the
  CLI loop; every path terminates in either a valid answer or a bounded,
  legible error message.

## 6. Requirement-by-Requirement Handling

**1. Hybrid Intelligence (docs-only in prototype).** See Golden Bucket in
§3 for retrieval and update mechanics. The key design choice is the
human-in-the-loop update gate: the bucket only grows from
analyst-approved reports, trading update speed for protection against
the agent reinforcing its own errors.

**2. Safety & PII Masking (primary, coded).** Two independent layers: an
input-side guardrail rejects off-topic/malicious requests before any tool
call happens, and an output-side hard control — name-based PII column
stripping in the BQ wrapper (§3) — guarantees known-PII columns never leave
the wrapper even if the guardrail is bypassed and the LLM generates a query
touching them. This caught a real gap during prototype development: the
live `users` schema has two sensitive columns (`postal_code`, `user_geom`)
absent from the assignment's original PII list, found by inspecting the
schema directly rather than trusting the brief's list as complete — see §3
for the full reasoning on why this is name-based matching, not a true
allow-list, and what that trade-off does and doesn't cover. Defense in
depth: the read-only SQL check and the service account's IAM read-only role
are independent backstops against a malicious or buggy query in the first
place.

**3. High-Stakes Oversight (secondary, coded).** See delete path in §4.
The resolve-then-list-then-confirm shape is the actual safeguard;
the confirmation mechanism itself (a plain yes/no next turn) is
deliberately boring so it doesn't become UX friction for a routine action
users are allowed to take on their own reports.

**4. Continuous Improvement (docs-only).** User-level: preference profile
described in §3, injected into the system prompt. System-level: explicitly
*not* automatic fine-tuning — high risk for a decision-support tool.
Instead, positively-signaled interactions (saved/approved reports) become
Golden Bucket candidates (requirement 1), and a periodic (e.g. weekly)
human review of low-signal interactions (self-correct exhausted, user
abandoned, explicit negative feedback) feeds a prompt/instruction
changelog reviewed by an engineer. Improvement happens at the prompt and
bucket layer with a human gate, not via silent model retraining.

**5. Resilience & Graceful Error Handling (primary, coded).** See §5 in
full.

**6. Quality Assurance (secondary, coded).** Offline golden eval set —
representative questions with expected SQL shape and expected report
themes/facts, curated by a human analyst, ideally later sourced from real
analyst-approved Golden Bucket entries. Correctness is scored by an
LLM-as-judge rubric (right numbers, answers the actual question, no PII),
periodically cross-checked against human grading to catch judge drift, and
re-run as a regression gate before any prompt/persona/bucket change ships.
UX is evaluated from the same structured logs Observability already
captures — turns-to-answer, clarification-request rate, self-correct
rate, delete-confirmation abandonment rate — rather than a separate survey
mechanism.

**7. Observability (secondary, coded).** See §3. In the prototype: because
every log line carries `conversation_id`, a full exchange (every LLM call,
tool call, and outcome) can be reconstructed by filtering the JSON log —
the concrete mechanism for "understand what went wrong in this exact
exchange," and it requires nothing beyond running the CLI to inspect. In
production, the same reconstruction is a Langfuse trace view rather than a
log grep, since Langfuse attaches to LangGraph's own execution graph and
needs no extra instrumentation code per call site.

**8. Agility / Persona Management (docs-only, small prototype add if time
allows).** See Persona Config in §3. A CEO-requested tone change ships by
editing an external document, not by a code deploy.

## 7. Quality Assurance / Evaluation

See requirement 6 above for the full approach. In short: a curated golden
set + LLM-as-judge scoring (spot-checked by humans) as a pre-ship
regression gate, and UX measured passively from production observability
data rather than a separate instrumentation surface.

## 8. Setup Instructions & Example Run

```bash
# Prerequisites: Python 3.11, uv, a GCP project with BigQuery API enabled
gcloud auth application-default login
cp .env.example .env   # fill in GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY
# or export them directly — .env is picked up automatically (python-dotenv),
# but real environment variables always take precedence over it.

uv sync
uv run retail-agent
```

Optional env vars (defaults shown): `GEMINI_MODEL=gemini-3.6-flash`,
`BQ_MAX_BYTES_BILLED=1000000000` (~1GB), `BQ_ROW_LIMIT=500`,
`BQ_QUERY_TIMEOUT_SECONDS=30`.

`OPENROUTER_API_KEY` / fallback wiring lands in the resilience-depth slice
(build order step 2), not the current skeleton.

Run the test suite: `uv run pytest`.

Example session:

```
> Why did our churn rate spike last month?
[agent retrieves relevant Golden Bucket trios, generates SQL, queries
 BigQuery read-only, strips PII, returns an analysis]

> Save that as a report
[report persisted to the local Saved Reports store]

> Delete all the reports we made in this conversation
Agent: This will delete 1 report: "Churn spike analysis — <date>". Confirm? (y/n)
> y
Agent: Deleted.
```

## 9. Prototype vs. Production Scope Matrix

| Requirement | Prototype (coded) | Production (design only) |
|---|---|---|
| 1. Hybrid Intelligence | Docs only | Vertex AI Vector Search, human-curated updates |
| 2. Safety & PII Masking | **Coded** — guardrail + name-based PII stripping (schema-verified) | Same, at scale |
| 3. High-Stakes Oversight | **Coded** — SQLite store + interrupt-based confirm | Cloud SQL, same flow |
| 4. Continuous Improvement | Docs only | Firestore preference store; human-gated system learning |
| 5. Resilience & Error Handling | **Coded** — typed errors, self-correct, backoff, circuit breaker | Same, at scale |
| 6. Quality Assurance | **Coded** — golden eval set + scoring script | Same + judge-drift audits |
| 7. Observability | **Coded** — structured JSON logs (stdout/file); optional Langfuse callback as a stretch add | Cloud Logging + Monitoring dashboards/alerts + Langfuse (self-hosted) for conversation-level tracing |
| 8. Agility (Persona) | Docs only (or `persona.yaml` if time allows) | Firestore/Cloud Storage config, admin surface |

5 of 8 requirements are coded (all 5 eligible for the prototype per the
assignment's deliverable-3 list); the remaining 3 aren't in that list, so
they're designed here in full but not implemented in code.
