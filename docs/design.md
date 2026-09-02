# Retail Data Analysis Agent — High-Level Design

Source of truth for architecture and design decisions. Update this file
whenever a decision changes. See `CLAUDE.md` for build constraints and
current prototype scope, `docs/assignment.md` for the brief, and
`docs/implementation-notes.md` for implementation-level detail, rejected
alternatives, and gaps found during testing — kept separate so this
document stays a fast read on a first pass.

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
        Provider["LLM Provider\nlangchain-google-genai\nChatGoogleGenerativeAI"]
        BQWrap["BigQuery Tool Wrapper\nread-only check, dry-run cap,\ntimeout, row limit, PII strip"]
    end

    subgraph LLMs["LLM Provider"]
        Gemini["Gemini\nVertex AI (prod) /\nAI Studio (prototype)"]
    end

    subgraph Data["Data & Knowledge"]
        BQ[("BigQuery\nthelook_ecommerce\n(read-only)")]
        Checkpoints[("Conversation Checkpoint Store\nMemorystore for Redis (prod)\nInMemorySaver (prototype)")]
        GoldenBucket[("Golden Bucket\npgvector on Cloud SQL (prod)\nlocal JSON + cosine (design reference)")]
        Reports[("Saved Reports Store\nPostgres — Cloud SQL (prod) /\ndocker-compose (prototype) — coded")]
        Prefs[("User Preference Store\nFirestore (prod) —\ndocs-only in prototype")]
        Persona[("Persona Config\nCloud Storage/Firestore (prod)\nlocal persona.yaml (design reference)")]
    end

    subgraph Obs["Observability"]
        Logs["Structured Logs\nCloud Logging (prod) /\nJSON file (prototype)"]
        Trace["LLM/Agent Tracing\nLangfuse (prod, self-hosted) —\nnative LangGraph callback"]
        Dash["Dashboards & Alerts\nCloud Monitoring (prod)"]
    end

    CLI --> Orchestrator
    Orchestrator --> Provider
    Provider --> Gemini
    Orchestrator --> BQWrap
    BQWrap --> BQ
    Orchestrator --> Checkpoints
    Orchestrator --> GoldenBucket
    Orchestrator --> Reports
    Orchestrator --> Prefs
    Orchestrator --> Persona
    Orchestrator --> Logs
    Logs --> Trace
    Trace --> Dash
```

## 2a. Current Graph Structure (generated, not hand-drawn)

The diagram above is the production system architecture — most of it
(Golden Bucket, Persona config) isn't coded, and per §9 never will be in
this prototype; the Saved Reports Store is coded (§3/§4). This one is
different in kind: it's
Mermaid syntax read directly off the real compiled `StateGraph` in
`graph.py` via `graph.get_graph().draw_mermaid()`, so it shows exactly
what's running today, not an aspiration. Regenerate it with
`uv run python scripts/render_graph.py` any time the graph changes.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	guardrail(guardrail)
	blocked(blocked)
	call_model(call_model)
	tools(tools)
	give_up(give_up)
	resolve_delete(resolve_delete)
	__end__([<p>__end__</p>]):::last
	__start__ --> guardrail;
	call_model -.-> __end__;
	call_model -.-> resolve_delete;
	call_model -.-> tools;
	guardrail -.-> blocked;
	guardrail -.-> call_model;
	tools -.-> call_model;
	tools -.-> give_up;
	blocked --> __end__;
	give_up --> __end__;
	resolve_delete --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

- **`guardrail`** — runs once, on the turn's user message only, before any
  model or tool call: `guardrail.check_user_input` matches it against a
  regex/keyword denylist of prompt-injection/jailbreak phrasing (`"ignore
  previous instructions"`, `"you are now"`, `"reveal your system prompt"`,
  etc. — see `guardrail.py`). This is the coded half of Safety & PII
  Masking's input-side control; it catches malicious *intent* in the
  request, not general off-topic questions (that's still the softer
  system-prompt instruction in `cli.py`).
- **`blocked`** — terminal node reached when `guardrail` rejects the turn;
  emits `GuardrailBlockedError`'s graceful decline without the model or any
  tool ever being invoked (zero LLM cost for a blocked message).
- **`call_model`** — sends the running message history to Gemini (via
  `langchain-google-genai`'s `ChatGoogleGenerativeAI`, tools bound with
  `.bind_tools(...)`) and appends whatever comes back (text, a tool call,
  or both).
- **`tools`** — executes every tool call in the latest model message
  against `BigQueryTool` or `ReportsStore` (`run_query`, `get_schema`,
  `save_report`, `list_reports`), appends the results, and tracks per-turn
  self-correct/empty-result state (the resilience mechanisms in §5).
  `delete_reports` is never dispatched here — see `resolve_delete` below.
- **`give_up`** — terminal node reached when a tool error isn't
  self-correctable or the self-correct budget (2 retries) is exhausted;
  emits the error class's graceful user-facing message and ends the turn.
- **`resolve_delete`** — reached instead of `tools` for any round
  containing a `delete_reports` call: resolves the request into exact
  candidate reports via `ReportsStore.find_candidates` (scoped to the
  current user, never cross-user), then either declines immediately (zero
  candidates — no confirmation needed for a no-op) or calls `interrupt()`
  to pause the graph and surface the exact candidates for the user's
  explicit yes/no on the next turn (the coded half of §4's delete path and
  §6's requirement 3). See §4 for the full mechanics, including the
  LangGraph replay nuance around code placed before an `interrupt()` call.
- **Solid edges** (`__start__ → guardrail`, `blocked → __end__`,
  `give_up → __end__`, `resolve_delete → __end__`) always fire. **Dashed
  edges** are conditional routing: out of `guardrail` to `blocked` if the
  message matched a denylist pattern, else `call_model`; out of
  `call_model` to `resolve_delete` if the model's reply contains a
  `delete_reports` call, to `tools` if it contains any other function
  call, else `__end__`; out of `tools` back to `call_model` unless the
  self-correct budget is exhausted, in which case to `give_up` — the
  bounded self-correct loop described in §5. Backoff on transient
  BigQuery/provider errors happens beneath this graph entirely, inside
  `bq_tool.py`/the provider classes — a typed transient error reaching
  `tools`/`call_model` means backoff already ran out, so it's treated as
  non-self-correctable here.

## 3. Component Reasoning

**Orchestrator — LangGraph.** Two requirements map directly onto its
primitives: `interrupt()`/resume for the confirm-before-delete flow
(requirement 3, coded — see §4/§6), and checkpointing for conversation-
state persistence (requirement 4, docs only). Used for these mechanisms
specifically, not adopted decoratively — the architecture diagram maps
~1:1 onto actual graph nodes. Also weighed against multi-agent frameworks
and provider-bundled agent runtimes; see
[implementation-notes.md](implementation-notes.md#orchestrator-rejected-alternatives)
for the full comparison — short version: neither fits a single
tool-calling agent behind a provider-agnostic `Provider` interface.

**Conversation Checkpoint Store — Memorystore for Redis (prod),
`InMemorySaver` (prototype).** Cloud Run instances are stateless and
scale to zero between turns, so the prototype's checkpointer
(`InMemorySaver`, fine for one long-running local CLI process) can't
carry conversation state across a cold start in production —
checkpointing has to be externalized for `interrupt()`/resume and
multi-turn context to survive that. Redis over Cloud SQL: checkpoints
are written on every turn (far higher frequency than Reports or Golden
Bucket writes) and are ephemeral working state, not a durable record
worth keeping indefinitely, so under the same access-pattern rule used
below for Cloud SQL vs. Firestore, they don't belong on the same
relational instance — Memorystore is LangGraph's other officially
supported checkpoint backend and keeps that write churn off the durable
stores entirely.

**Extensibility — new tools and data sources.** New capabilities (chart
generation, emailing reports, web search) fall out of the tool-calling
shape already chosen rather than needing a separate mechanism: each is a
new function registered as a LangGraph tool alongside
`run_query`/`get_schema` (below), with the model deciding when to call
it, same as the coded tools today. A new data source follows the same
wrapper pattern as `BigQueryTool`: its own safety check appropriate to
that source, its own cost/row/timeout caps, and its own PII-stripping
pass before results reach the LLM, rather than a bespoke integration
path per source.

**Agent Service compute — Cloud Run.** Fits a synchronous,
intermittently-used chat workload better than GKE (cluster overhead
buying nothing a single container needs) or Cloud Functions (default
timeout too tight for a self-correct retry plus a BQ dry-run/execute):
scales to zero between conversations and takes an arbitrary container
image for the LangGraph process's actual dependencies.

**Client ↔ Agent Service communication.** The prototype CLI runs the
orchestrator in-process — no network hop, no protocol involved. In
production the same CLI talks to Cloud Run over a REST endpoint (a
single `POST /chat` taking `{conversation_id, message}` and returning
that turn's response), reached the same way any internal Cloud Run
service is — an IAM-authenticated service-to-service call, not a public
API key. `conversation_id` is the thread key both the LangGraph
checkpointer above and the (stateless, scale-to-zero) Cloud Run instance
need: every request carries it so the right checkpoint loads regardless
of which instance handles the request.

The final answer is returned whole, not token-streamed — token-by-token
streaming would need the provider's `.generate()` to call
`ChatGoogleGenerativeAI`'s streaming method (`.stream()`/
`generate_content_stream`), which it doesn't do today, so it's left as
future work rather than assumed here. That's a narrower gap than it
first looks, though: the tool-calling loop that runs before the final
synthesis call (schema lookups, SQL execution, self-correct retries) has
no answer text to stream, but it does have node-level *progress* to
report, and that's cheap regardless of provider-level streaming — the
CLI (coded) uses LangGraph's `graph.stream(..., stream_mode="updates")`
instead of `.invoke()`, printing a one-line status (`"Looking up schema
for orders..."`, `"Running a query..."`, `"Got 7 row(s)."`, `"That
didn't work, retrying..."`) after each `call_model`/`tools` node instead
of staying silent until the whole turn completes (`cli.py`'s
`_stream_progress`). The Agent
Service reaches Cloud SQL, Memorystore, and Firestore the same way it
reaches BigQuery — its own IAM identity, no separate per-store credential
to manage.

**LLM provider — Gemini only, via `langchain-google-genai`'s
`ChatGoogleGenerativeAI`. No fallback provider.** Gemini via Vertex AI in
production (shares IAM/audit/quota plumbing with the BigQuery access
already required); AI Studio key in the prototype for simplicity. Default
model is Gemini Flash (`gemini-3.6-flash`, see §8), not Pro: both coded
turn shapes (schema-bound SQL generation, report synthesis over an
already-small PII-stripped result set) are closer to templated generation
than open-ended reasoning, so Flash's latency/cost fits a synchronous chat
UX better.

`GeminiProvider` (coded) wraps `ChatGoogleGenerativeAI(...).bind_tools(TOOLS)`
— tools bound once at construction rather than passed per call, since
Gemini is the only provider and the tool set never changes mid-conversation.
`max_retries=1` (not `0`!) disables the SDK's own retry loop — a documented
quirk of the underlying Google SDK where `max_retries=0` is interpreted as
"use the Google default" (5 retries) rather than "no retries" — so the
`bounded_backoff` wrapper (§5) stays the single source of retry behavior,
same principle the earlier `OpenRouterProvider`'s `max_retries=0` (for the
`openai` SDK, where `0` genuinely means "no retries") once documented.
There is no circuit breaker and no fallback provider: with Gemini as the
only provider, there's nowhere to fail over to, so a Gemini failure that
survives backoff surfaces directly as a graceful error message (§5) instead
of being routed anywhere else.

This reverses an earlier decision recorded in
[implementation-notes.md](implementation-notes.md#llm-provider-raw-sdks-vs-langchain-chat-model-wrappers)
to talk to the Gemini/OpenRouter SDKs directly rather than through
LangChain's chat model wrappers. That decision was made when a second
provider (OpenRouter) existed and needed its own hand-rolled message/
tool-schema translation module — LangChain's chat model interface would
have made that translation unnecessary, at the cost of a direct request-
shape control this project wanted to keep at the time. With OpenRouter
removed, that trade-off no longer applies: there's no second provider's
translation cost to avoid paying, and `langchain-google-genai`'s
`ChatGoogleGenerativeAI` now classifies every provider failure into
`langchain_core.exceptions`' own unified `ModelError` taxonomy
(`ModelAuthenticationError`, `ModelRateLimitError`, `ModelAPIError`, etc.,
each carrying an `is_retryable` flag) — a real provider-agnostic exception
surface that didn't exist when the original raw-SDK decision was made, so
the "exception classification is a wash" argument from that decision no
longer holds either. See
[implementation-notes.md](implementation-notes.md#llm-provider-raw-sdks-vs-langchain-chat-model-wrappers)
for the full account of what changed. This also means adding or swapping a
provider later is materially cheaper than it was: every LangChain chat
model returns the same `AIMessage`/`ToolMessage` shapes, so `graph.py`,
`tools.py`, and `cli.py` — which now speak that shape natively, not a
Gemini-specific one — would need no changes; only `llm_provider.py`'s
constructor and its exception-classification buckets would.

**BigQuery tool wrapper.** `src/provided/bq_runner.py` was supplied by the
company as an example of how to query BigQuery, not a required
dependency — it's left in the repo unused. A raw `bigquery.Client` call
(what it thinly wraps) has no cost cap, no statement-type check, no PII
masking, and collapses every failure into one exception shape. It backs
two tools exposed to the model: `get_schema` (table/column
introspection — how "what data is available" questions get answered)
and `run_query`. The wrapper (`BigQueryTool`, which owns its own client)
adds: a SQL read-only
keyword/regex allowlist (SELECT/WITH only, reject DML/DDL and
multi-statement input) with the service account's IAM read-only role as
the real backstop; `dry_run=True` plus a ~1GB max-bytes cap (well under
the 1TB/month free tier); a query timeout; a row limit; PII column
stripping applied post-execution by matching each returned column's bare
name against a maintained PII field registry; and typed error
classification (syntax, permission, transient, empty-result) so the
orchestrator can decide retry vs. self-correct vs. give-up instead of
re-parsing a bare exception.

The PII match is name-based, not a true schema-driven allow-list — it can
miss a column renamed in the query, or one added to the live schema after
the registry was written. See
[implementation-notes.md](implementation-notes.md#bigquery-pii-stripping-name-based-matching-not-a-true-allow-list)
for why that trade-off was accepted (a true allow-list would also drop
legitimate aggregate columns like `SUM(sale_price) AS total_revenue`) and
what covers the gap. The registry was verified against the live `users`
schema during development, not just typed from the brief — that check
caught two columns (`postal_code`, `user_geom`) absent from the
assignment's original PII list before they could leak.

**Golden Bucket (docs only).** Only the question half of each trio is
embedded, with `text-embedding-004` — both at insert time (when a trio is
added to the bucket) and at query time (the incoming user question), so
both sides land in the same vector space for cosine comparison. The SQL
and report text are stored as plain payload, not embedded, and are
returned alongside their matching question once retrieved — no chunking,
since a trio is already a small, self-contained record. `text-embedding-004`
specifically to stay inside the Google/Vertex ecosystem already used for
Gemini rather than adding a second embeddings vendor for one small piece
of the system. Retrieval is top-k (e.g. k=3) cosine/ANN search against
**pgvector on Cloud SQL** — retrieved (question, sql, report) trios are
injected as few-shot context before SQL generation and again as
style/structure cues before report writing.

pgvector over Vertex AI Vector Search because the two aren't managed at
the same level: Cloud SQL manages the *database* (patching, backups,
HA), but pgvector is an extension on top of it — index type/parameters
(HNSW/IVFFlat) and scaling are still ours to own, and vector search
shares compute with whatever else runs on that instance (here, also the
Saved Reports Store). Vertex AI Vector Search is a dedicated managed
*vector-search product* — no index tuning, independent scaling, built for
much larger corpora. That gap is acceptable here because the
human-curated update path (below) keeps the corpus in the thousands of
vectors, not millions — comfortably inside pgvector's range — and
co-locating with the Saved Reports Store's Cloud SQL instance avoids
paying for a second managed vector service. Vertex AI Vector Search would
be worth revisiting if the bucket is ever seeded from a large historical
archive instead of growing incrementally.

Update path is human-curated, not automatic: a trio is appended only
after a human analyst approves or edits the generated report,
specifically to avoid feedback-loop drift where an unreviewed mistake
compounds into future retrievals. Periodic
maintenance: dedup near-identical trios, down-weight/archive trios past a
freshness horizon (e.g. 12 months), and version trios rather than
overwrite them in place.

**Saved Reports Store — coded, Postgres in both the prototype and
production.** Schema: `id, owner, title, content, conversation_id,
created_at, tags` — gives real queryable delete-scoping (`WHERE owner =
%s AND ...`), which is what the confirm-then-delete flow in requirement 3
actually needs rather than asserts in prose. `ReportsStore`
(`reports_store.py`) owns its own `psycopg` (v3) connection directly, the
same way `BigQueryTool` owns its own `bigquery.Client` — no ORM, matching
this codebase's existing preference for a thin wrapper over the driver.
Unlike the rest of this design, the prototype and production stores are
the same technology here, not a simplified stand-in: local dev/testing
runs against a real Postgres via `docker-compose.yml` (`postgres:16`,
two databases — `retail_agent_reports` for interactive use,
`retail_agent_reports_test` for the test suite, so running `uv run
pytest` never truncates reports saved during manual testing), and
production points `REPORTS_DATABASE_URL` at Cloud SQL for Postgres
instead. One deliberate asymmetry with `bq_tool.py`: `ReportsStore` skips
`resilience.bounded_backoff` — this store has no "brief rate-limit or
server hiccup, retry the same call" failure mode the way a third-party
network API (BigQuery, Gemini) does, so retrying here would be unearned
complexity (see docs/implementation-notes.md).

`owner` is `getpass.getuser()` (the OS username of whoever is running the
CLI process) in the prototype — there's no login system to resolve a real
per-manager identity from, and this is a single-process, single-user tool
regardless. Every store call is scoped by `owner` (never cross-user,
re-asserted at delete time too, not just at candidate selection — defense
in depth), so the scoping *behavior* requirement 3 asks for is real even
though the prototype's identity source is a placeholder for the Firestore-
backed per-manager auth production would use. "Create a report with
action items" — the base deliverable-3 ask, distinct from requirement 3 —
is satisfied by the agent formatting its chat answer as a report and
optionally persisting it via `save_report`.

Cloud SQL vs. Firestore below follows one rule: relational/queryable
access (arbitrary `WHERE` clauses, joins) goes in Cloud SQL; single-key
document reads/writes go in Firestore. Reports and the Golden Bucket need
the former; preferences and persona don't.

**User Preference Store (docs only).** Firestore doc per manager
(preferred format, analysis depth, updated_at), read into the system
prompt each turn, written after an explicit ("just give me the numbers")
or recurring implicit preference signal — Firestore, not Cloud SQL,
since every access is a single lookup by manager id.

**Persona Config (docs only).** A single versioned instruction document
external to code — a Firestore row or Cloud Storage file (whichever fits
the eventual admin surface — a CRUD form or a re-uploaded file), editable
with no engineering ticket, read fresh each session. This is the concrete
mechanism for "CEO changes tone weekly, no redeploy" (requirement 8).

**Observability (docs only as a coded requirement — see §9).** Target
shape: one structured JSON log line per LLM call / tool call / turn
(conversation_id, intent, sql, rows_returned, tokens, latency_ms,
error_class, self_correct_attempt). The prototype has only incidental
`logging` calls with `extra=` fields at error/retry sites, added as a
by-product of building resilience depth (§5), not a deliberate
implementation of this requirement — there's no per-turn structured JSON
line and no log covers the successful path. Production ships the full
log shape to Cloud Logging (metrics/alerting substrate) *and* adds
cross-call conversation tracing via self-hosted **Langfuse**, chosen
because it plugs into LangGraph as a native callback handler — no
hand-rolled OpenTelemetry spans needed. Self-hosted over LangSmith:
tracing must cover the same PII boundary the rest of this system respects,
and self-hosting keeps trace data in the same infra boundary rather than
handing prompts/SQL/tool output to a third-party SaaS by default —
whatever tracer is used, it must trace **post-PII-strip** data only.
Cloud Monitoring dashboards/alerts sit on top of the Cloud Logging stream
for error rate, self-correct rate, latency, PII-block rate, and daily
cost; Langfuse covers the conversation-level "what exactly happened in
this exchange" deep-dive that raw metrics can't.

## 4. Data Flow

Described here at production scope; the prototype implements the coded
steps of both the Q&A path and the Delete path below (§9) — only Golden
Bucket retrieval, persona config, and user preferences remain docs only.

**Q&A / analysis path**: user message → **coded** rule-based guardrail
check (regex/keyword denylist for prompt-injection/jailbreak intent —
`guardrail.py`; a production system would add a semantic classifier here
for the off-topic/delete-intent distinction this denylist doesn't attempt)
→ embed question, retrieve top-k Golden Bucket trios (docs only) → LLM
generates SQL using trios + schema as context → BQ wrapper validates
(read-only check) → dry-run (reject over cap) → execute with timeout +
row limit → PII-strip the DataFrame → LLM synthesizes the report/answer,
treating tool output as untrusted data (never as instructions — closes
the second-order injection path where adversarial text embedded in a row
value could otherwise be read as a directive) and using persona config +
user preferences (both docs only) → response returned to the client and
logged.

**Delete path — coded.** User message → model calls `delete_reports`
(`scope`, optional `title_contains`) → `resolve_delete` resolves the
request into candidate report(s) via `ReportsStore.find_candidates`,
scoped to the requesting user (never cross-user, re-asserted again at
delete time) → zero candidates declines immediately, no pause needed;
otherwise the node lists the exact candidates and pauses via
`interrupt()` → next user turn: a deterministic keyword check (not
another LLM call — `_is_affirmative`) treats "yes"/"confirm"/etc. as
confirmation and resumes the graph to execute the delete against the
store; anything else aborts. Both outcomes are logged
(`reports_deleted`/`reports_delete_aborted`). Non-mutating actions (e.g.
"show me my reports") never trigger this pause — only `delete_reports`
does, keeping the added friction to one turn. This entire path depends on
the Saved Reports Store (§3); see §2a for the `resolve_delete` node and
§6 requirement 3 for the full safety rationale.

## 5. Error Handling & Fallback Strategies

Coded (`errors.py`, `bq_tool.py`, `graph.py`, `llm_provider.py`,
`resilience.py`). Every
`AgentError` subclass carries a `self_correctable: bool` flag and a
`graceful_message` — the graph routes on the flag, never ad hoc
isinstance checks, and `errors.graceful_message_for(...)` is the single
place user-facing copy for a failure class lives.

Two independent retry mechanisms: **backoff** (inside
`bq_tool.py`/the providers) retries the *same* call automatically when
the client's raw exception looks transient. **Self-correct** (in
`graph.py`, gated by `self_correctable`) lets the *model* retry with a
*different* query, only after a typed error reaches the graph. See
[implementation-notes.md](implementation-notes.md#two-retry-mechanisms-backoff-vs-self-correct)
for why these are deliberately separate mechanisms rather than a
contradiction.

| Error class | Mechanism | Outcome |
|---|---|---|
| Syntax/bad-request (`QuerySyntaxError`, `SQLSafetyError`, `QueryTooExpensiveError`) | Self-correct | Fed back to the model as the tool result; up to 2 retries (`MAX_SELF_CORRECT_ATTEMPTS`), 3rd attempt routes to `give_up` with a graceful message — never an unbounded loop |
| Permission (`QueryPermissionError`) | None | Straight to `give_up` on first occurrence — no query rewrite fixes an IAM grant |
| Empty result (0 rows) | One sanity-check pass | Model is nudged once per turn to check its own filters/joins before accepting "zero rows"; a second zero-row result in the same turn is accepted rather than looping |
| Transient/timeout — BQ or provider (`QueryTransientError`/`ProviderTransientError`) | Backoff (2 attempts, exponential, `tenacity`-based, shared across both retry sites) | If backoff exhausts, the typed error reaches the graph already non-self-correctable → `give_up` (BQ), or propagates to `cli.py`'s top-level `except AgentError` handler (provider), which prints the graceful message and keeps the REPL loop alive |
| LLM provider failure (any `ProviderError`) | None — no fallback provider | Propagates to `cli.py`'s top-level `except AgentError` handler, which prints the graceful message and keeps the REPL loop alive; with Gemini as the only provider there's nowhere to fail over to |

Self-correct operates on the whole model turn, not per tool call — if a
turn fires multiple tool calls and one fails non-self-correctably while
another succeeds, the entire turn gives up rather than keeping the
successful result. Deliberate, not an oversight (each `call_model`
regenerates the whole call set fresh, so there's no partial-retry path) —
see
[implementation-notes.md](implementation-notes.md#known-gaps-found-during-testing)
for the full reasoning and test coverage.

Every failure path is logged (Python stdlib `logging`, `extra=` fields —
see the Observability note in §3) with its error class
(`bq_call_failed`/`bq_unclassified_exception`, `tool_call_error`,
`provider_call_failed`, `provider_failover`, etc.). No path crashes the
CLI loop; every path terminates in either a valid answer or a bounded,
legible error message.

Gaps found during live testing are recorded in
[implementation-notes.md](implementation-notes.md#known-gaps-found-during-testing):

- The empty-result check doesn't fire for `COUNT(*)` queries.
- An invalid API key is still classified as a generic `ProviderError`
  instead of `ProviderAuthError` — confirmed unchanged after the move to
  `langchain-google-genai`'s classification (Google's API itself returns
  HTTP 400 for this case, not 401/403, so no client-side classifier change
  fixes it).
- That distinction isn't used anywhere regardless: `call_model` lets any
  `ProviderError` propagate past the graph's curated-message pipeline
  entirely, straight to `cli.py`'s generic `except AgentError` handler,
  which prints `str(exc)` rather than the class's curated
  `graceful_message`.

None has a functional or safety impact (the CLI loop never dies either
way), but the last one means a misconfigured API key surfaces as a raw
provider error string rather than the friendlier curated message written
for exactly that case.

## 6. Requirement-by-Requirement Handling

**1. Hybrid Intelligence — docs only.** See Golden Bucket in §3. Key
design choice: a human-in-the-loop update gate — the bucket only grows
from analyst-approved reports, trading update speed for protection
against the agent reinforcing its own errors.

**2. Safety & PII Masking — coded.** Three independent layers, addressing
three distinct threats:

- **Input-side guardrail** (`guardrail.py`, wired into `graph.py` as the
  `guardrail` node — §2a) — a regex/keyword denylist that blocks
  prompt-injection/jailbreak phrasing ("ignore previous instructions",
  "reveal your system prompt", "developer mode", etc.) before the model or
  any tool is ever called. Deliberately a denylist, not a semantic
  classifier: cheap, deterministic, zero added latency/cost, and it
  targets malicious *intent* specifically — it does not attempt the
  broader off-topic classification named in the assignment brief, which
  remains the system-prompt instruction ("only answer analysis questions
  — politely decline anything else") it always was. A denylist also can't
  catch a paraphrased or subtler injection attempt; see
  [implementation-notes.md](implementation-notes.md#input-guardrail-regex-denylist-not-a-classifier)
  for that trade-off and what covers the gap.
- **Untrusted-tool-output handling** — the system prompt (`cli.py`)
  explicitly instructs the model to treat everything returned by
  `run_query`/`get_schema` as data, never as instructions. This closes a
  second, different injection path than the guardrail above: adversarial
  text embedded in a database value (e.g. a product name field crafted to
  look like a command) reaches the model through tool output, not through
  the user's chat message, so the input-side guardrail never sees it.
- **Output-side hard control** — name-based PII column stripping in the BQ
  wrapper (§3) guarantees known-PII columns never leave the wrapper even
  if both layers above are bypassed.

Defense in depth: the read-only SQL check and the service account's IAM
read-only role are independent backstops against a malicious or buggy
query in the first place.

**3. High-Stakes Oversight — coded.** See delete path in §4 and the
`resolve_delete` node in §2a. The resolve-then-list-then-confirm shape is
the safeguard; the confirmation mechanism itself (a plain yes/no next
turn, checked deterministically rather than by another LLM call) is
deliberately boring so it wouldn't become UX friction for a routine
action users are allowed to take on their own reports. Resuming a paused
confirmation bypasses the input-side guardrail (§2a), but the
confirmation check is a narrow allowlist where anything unmatched aborts
— there's no privilege-escalation path, only a UX rough edge if the user
types a new, unrelated question instead of a yes/no (it's swallowed as a
"no" rather than answered; see docs/implementation-notes.md). Owner
scoping in the prototype uses `getpass.getuser()` (the OS username of
whoever runs the CLI) rather than real per-manager auth, since there's no
login system to resolve an identity from — production would source
`owner` from the same auth the rest of the service already requires.

**4. Continuous Improvement — docs only.** User-level: preference
profile (§3), injected into the system prompt. System-level: explicitly
*not* automatic fine-tuning — high risk for a decision-support tool.
Instead, positively-signaled interactions (saved/approved reports) become
Golden Bucket candidates (requirement 1), and a periodic (e.g. weekly)
human review of low-signal interactions (self-correct exhausted, user
abandoned, explicit negative feedback) feeds a prompt/instruction
changelog reviewed by an engineer.

**5. Resilience & Graceful Error Handling — coded.** See §5.

**6. Quality Assurance — docs only** (eligible for the prototype,
deliberately not coded — see §9). Offline golden eval set — representative
questions with expected SQL shape and report themes, curated by a human
analyst — scored by an LLM-as-judge rubric (right numbers, answers the
actual question, no PII), periodically cross-checked against human
grading, re-run as a regression gate before any prompt/persona/bucket
change ships. UX would be evaluated from the same structured logs
Observability would capture, rather than a separate survey mechanism.

**7. Observability — docs only** (eligible for the prototype,
deliberately not coded — see §9; only the incidental `logging` calls from
resilience work exist today). See §3. In production, every log line
would carry `conversation_id`, so a full exchange could be reconstructed
by filtering the JSON log; a Langfuse trace view gives the same
reconstruction without a log grep.

**8. Agility / Persona Management — docs only.** See Persona Config in
§3. A CEO-requested tone change ships by editing an external document,
not by a code deploy.

## 7. Quality Assurance / Evaluation

See requirement 6 above for the full approach. In short: a curated golden
set + LLM-as-judge scoring (spot-checked by humans) as a pre-ship
regression gate, and UX measured passively from production observability
data rather than a separate instrumentation surface. No eval script,
golden set, or judge rubric exists in this repo — this section describes
the intended production approach only.

## 8. Setup Instructions & Example Run

Three independent things need to be configured, and they have different
prerequisites — Gemini is a single API key with nothing else to install;
BigQuery needs the `gcloud` CLI and a real GCP project, even though the
dataset being queried (`thelook_ecommerce`) is public; the Saved Reports
Store needs a running Postgres, provided locally via `docker-compose.yml`.

**1. Python environment**

- Python 3.11 — not required to be pre-installed: `uv sync` reads
  `.python-version` and fetches a matching interpreter automatically if
  none is found, via `uv`'s own Python management
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed

**2. Gemini (LLM provider)**

- Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey)
- No other setup — this is just `GEMINI_API_KEY` in `.env`

**3. BigQuery (data source)**

- Install the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) if
  you don't already have it
- Have (or create) a GCP project with the BigQuery API enabled and billing
  active — required even though `thelook_ecommerce` is a public dataset,
  because dry-run cost estimation and query execution both run as *your*
  project, not the dataset owner's
- Run `gcloud auth application-default login` once (opens a browser) to
  create local Application Default Credentials
- Set `GOOGLE_CLOUD_PROJECT` in `.env` to that project's ID

**4. Saved Reports Store (Postgres)**

- Requires Docker (or an already-running Postgres — see below)
- `docker compose up -d postgres` starts a local Postgres (`postgres:16`)
  with two databases: `retail_agent_reports` (what the CLI uses) and
  `retail_agent_reports_test` (what the test suite uses, so running tests
  never truncates reports saved during interactive use)
- `ReportsStore` creates its own `reports` table on first connect (`CREATE
  TABLE IF NOT EXISTS`) — no separate migration step
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

**Troubleshooting:** a `PermissionDenied`/403 on startup or on the first
query almost always means one of the BigQuery-specific steps above was
skipped — either the BigQuery API isn't enabled on the project named by
`GOOGLE_CLOUD_PROJECT`, billing isn't active on it, or
`gcloud auth application-default login` was never run (or was run for a
different account/project than the one in `.env`). Gemini-side failures
(invalid/missing `GEMINI_API_KEY`) surface as a graceful provider error
message rather than a crash — see §5. A reports-store connection failure
(Postgres not running, wrong `REPORTS_DATABASE_URL`) surfaces as a
startup error naming `docker compose up -d postgres` as the likely fix.

Optional env vars (defaults shown): `GEMINI_MODEL=gemini-3.6-flash`,
`BQ_MAX_BYTES_BILLED=1000000000` (~1GB), `BQ_ROW_LIMIT=500`,
`BQ_QUERY_TIMEOUT_SECONDS=30`, `LOG_LEVEL=INFO`,
`REPORTS_DATABASE_URL=postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports`.

Run the test suite: `uv run pytest`. This runs the unit tests by default,
including `test_reports_store.py` — unlike every other unit test file,
that one needs a real Postgres reachable at `REPORTS_DATABASE_URL` (or its
default, matching `docker-compose.yml`'s `retail_agent_reports_test`
database) to pass, since `ReportsStore` has no fake/mock double the way
`BigQueryTool`/`GeminiProvider` do in `test_graph.py`/`test_cli.py` — run
`docker compose up -d postgres` first. `tests/integration/` — live
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

Golden Bucket retrieval shown in earlier drafts of this example isn't in
the prototype — see §9.

Type `exit` or `quit` to leave the REPL (Ctrl-D/Ctrl-C also work).

**Inspecting internals (PII stripping, self-correct).** `retail-agent`
prints only the final `Agent: ...` answer per turn — enough to use the
agent, not enough to see the coded requirements actually fire.
`scripts/trace.py` runs one question through the same graph and prints
every message in the resulting state (every tool call, every tool
response — post-PII-strip — every model turn) plus the resilience
bookkeeping (`self_correct_attempts`, `last_tool_errors`) the REPL never
surfaces. Same credentials as the CLI; no conversation continuity (each
run is a fresh thread), so it's for inspecting one question at a time,
not for discussing results:

```bash
uv run python scripts/trace.py "What are the top 5 product categories by revenue?"
```

## 9. Prototype vs. Production Scope Matrix

| Requirement | Prototype (coded) | Production (design only) |
|---|---|---|
| 1. Hybrid Intelligence | Docs only | Vertex AI Vector Search, human-curated updates |
| 2. Safety & PII Masking | **Coded** — injection-denylist guardrail + untrusted-tool-output handling + name-based PII stripping (schema-verified) | Same, at scale, with a semantic classifier replacing the denylist |
| 3. High-Stakes Oversight | **Coded** — Postgres-backed Saved Reports Store (`ReportsStore`/`psycopg`) + `interrupt()`/`Command(resume=...)`-based confirm-then-delete (`resolve_delete` node) | Same store technology as the prototype (Cloud SQL for Postgres instead of local/docker Postgres); real per-manager auth resolving `owner` instead of the OS username |
| 4. Continuous Improvement | Docs only | Firestore preference store; human-gated system learning |
| 5. Resilience & Error Handling | **Coded** — typed errors, self-correct, backoff | Same, at scale |
| 6. Quality Assurance | Docs only — eligible for the prototype, deliberately not coded | Golden eval set + scoring script, judge-drift audits |
| 7. Observability | Docs only — eligible for the prototype, deliberately not coded | Structured JSON logs → Cloud Logging + Monitoring dashboards/alerts + Langfuse (self-hosted) for conversation-level tracing |
| 8. Agility (Persona) | Docs only | Firestore/Cloud Storage config, admin surface |

3 of 8 requirements are coded (all three eligible for the prototype per
the assignment's deliverable-3 list, which allows any 2 of 5). The other 2
eligible requirements (Quality Assurance, Observability) are a deliberate
scope decision, not a time cutoff — the build order (see `CLAUDE.md`)
adds High-Stakes Oversight after resilience depth, then stops. The
remaining 3 requirements (Hybrid Intelligence, Continuous Improvement,
Agility) were never in the assignment's prototype-eligible list, so
they're designed here in full but were never candidates for coding
either way.
