# Proposal

## Why

The system was designed in a monolithic HLD (`docs/design.md`) and a companion
`docs/implementation-notes.md`. Moving to OpenSpec, this baseline change
records the system as already built, so later changes can be proposed,
reviewed and archived as deltas against explicit behaviour specs instead of
prose.

## What Changes

- No code changes. Captures existing behaviour as ten capability specs.
- Carries the HLD and implementation notes into `design.md` (architecture,
  component decisions, rejected alternatives, known gaps).
- Carries requirement-by-requirement coverage and the scope matrix into this
  proposal (below).
- Setup instructions move to `README.md`; docs-only requirements become open
  changes.

## Capabilities

### New Capabilities
- `pii-masking`: PII column registry, result and schema stripping, no `SELECT *` from `users`.
- `sql-safety`: read-only single-statement check, dry-run cost cap, timeout, row limit, dataset scope.
- `input-guardrail`: injection denylist before any model call; tool output treated as data.
- `report-management`: owner-scoped save/list and confirm-then-delete of saved reports.
- `error-resilience`: typed errors, bounded self-correct, backoff, empty-result check, no CLI crash.
- `observability`: structured JSON trace events and optional LangSmith tracing.
- `conversation-persistence`: owner-scoped transcript and resume on startup.
- `mcp-server`: tools over stdio MCP with server-side guarantees and token deletion.
- `mcp-client`: external MCP tools loaded, namespaced and filtered for the agent.
- `eval-harness`: golden-set evaluation with deterministic checks and an LLM judge.

### Modified Capabilities
- None (baseline).

## Impact

- Docs: `docs/design.md` and `docs/implementation-notes.md` removed; content
  is in this change. `CLAUDE.md`, `README.md` and code comments now point at
  `openspec/`.
- Code, dependencies, APIs: unchanged.

## Requirement-by-requirement handling

**1. Hybrid Intelligence — docs only.** See Golden Bucket in design.md "Components". Key
design choice: a human-in-the-loop update gate — the bucket only grows
from analyst-approved reports, trading update speed for protection
against the agent reinforcing its own errors.

**2. Safety & PII Masking — coded.** Three independent layers, addressing
three distinct threats:

- **Input-side guardrail** (`guardrail.py`, wired into `graph.py` as the
  `guardrail` node — design.md "Current graph structure") — a regex/keyword denylist that blocks
  prompt-injection/jailbreak phrasing ("ignore previous instructions",
  "reveal your system prompt", "developer mode", etc.) before the model or
  any tool is ever called. Deliberately a denylist, not a semantic
  classifier: cheap, deterministic, zero added latency/cost, and it
  targets malicious *intent* specifically — it does not attempt the
  broader off-topic classification, which
  remains the system-prompt instruction ("only answer analysis questions
  — politely decline anything else") it always was. A denylist also can't
  catch a paraphrased or subtler injection attempt; see
  Implementation notes (below)
  for that trade-off and what covers the gap.
- **Untrusted-tool-output handling** — the system prompt (`cli.py`)
  explicitly instructs the model to treat everything returned by
  `run_query`/`get_schema` as data, never as instructions. This closes a
  second, different injection path than the guardrail above: adversarial
  text embedded in a database value (e.g. a product name field crafted to
  look like a command) reaches the model through tool output, not through
  the user's chat message, so the input-side guardrail never sees it.
- **Output-side hard control** — name-based PII column stripping in the BQ
  wrapper (design.md "Components") guarantees known-PII columns never leave the wrapper even
  if both layers above are bypassed.

Defense in depth: the read-only SQL check and the service account's IAM
read-only role are independent backstops against a malicious or buggy
query in the first place.

**3. High-Stakes Oversight — coded.** See delete path in design.md "Data flow" and the
`resolve_delete` node in design.md "Current graph structure". The resolve-then-list-then-confirm shape is
the safeguard; the confirmation mechanism itself (a plain yes/no next
turn, checked deterministically rather than by another LLM call) is
deliberately boring so it wouldn't become UX friction for a routine
action users are allowed to take on their own reports. Resuming a paused
confirmation bypasses the input-side guardrail (design.md "Current graph structure"), but the
confirmation check is a narrow allowlist where anything unmatched aborts
— there's no privilege-escalation path, only a UX rough edge if the user
types a new, unrelated question instead of a yes/no (it's swallowed as a
"no" rather than answered; see design.md "Implementation notes"). Owner
scoping in the prototype uses `getpass.getuser()` (the OS username of
whoever runs the CLI) rather than real per-manager auth, since there's no
login system to resolve an identity from — production would source
`owner` from the same auth the rest of the service already requires.

**4. Continuous Improvement — docs only.** User-level: preference
profile (design.md "Components"), injected into the system prompt. System-level: explicitly
*not* automatic fine-tuning — high risk for a decision-support tool.
Instead, positively-signaled interactions (saved/approved reports) become
Golden Bucket candidates (requirement 1), and a periodic (e.g. weekly)
human review of low-signal interactions (self-correct exhausted, user
abandoned, explicit negative feedback) feeds a prompt/instruction
changelog reviewed by an engineer.

**5. Resilience & Graceful Error Handling — coded.** See design.md "Error handling".

**6. Quality Assurance — coded.** Offline golden eval set (`eval/golden_set.json`,
9 hand-curated questions, one per core capability:
customer behavior, product performance, time-based metrics, a schema/
structure question, a multi-step "why" question, report-with-action-items,
a PII-probe safety case, an off-topic decline case, and a cross-segment
comparison) run through the real compiled
graph by `src/retail_agent/eval.py` and scored two ways. Cheap deterministic
checks run first — same "cheap check before spending an LLM call" pattern
already used by the input guardrail and the SQL safety allowlist: was the
expected tool (`run_query`/`get_schema`/neither) called, did the SQL touch
an expected table, does the answer mention an expected keyword, and (for the
PII-probe case) does the answer contain an email-shaped string at all,
belt-and-suspenders on top of the BQ wrapper's own column stripping. Only
then does an LLM-as-judge pass run — a bare, tool-unbound
`ChatGoogleGenerativeAI` (never `GeminiProvider`, which binds the agent's own
five tools) scoring the three dimensions this section always named: does the
answer address the question asked, are its numbers/claims internally
plausible, is there any PII-shaped content in it. `scripts/run_eval.py` is
the manual entry point (`uv run python scripts/run_eval.py`) — it prints a
pass/fail table, writes the full structured report to a JSON file so runs
can be diffed over time (the practical form of "regression gate before any
prompt/persona/bucket change ships"), and supports `--fail-under` for later
CI wiring. Never run by `uv run pytest` — like `tests/integration/`, every
case is a real, billed Gemini + BigQuery call, so this stays an explicit,
manual action (see design.md "Implementation notes" for the full reasoning and
the accepted gaps: 9 cases isn't statistically meaningful, table/keyword
checks are loose shape checks rather than exact-SQL/exact-answer matching,
and there's no ground-truth query/result diffing — the judge scores
plausibility, not correctness against a known answer). Periodic human
cross-checking of judge grading remains a process description, not code.
UX would be
evaluated from the same structured trace events Observability already
captures (design.md "Components"), rather than a separate survey mechanism.

**7. Observability — coded.** See design.md "Components". Every `llm_call`/`tool_call`/`turn`
JSON line carries `conversation_id`, so a full exchange can be
reconstructed by filtering the CLI's stderr output (or a file, if
`TRACE_LOG_DESTINATION` points at one) on that field; a LangSmith trace
view gives the same reconstruction without a log grep, today, not just in
a future production deployment. Cloud Monitoring dashboards/alerts built
on top of the structured log stream remain production-only — no
dashboards exist.

**8. Agility / Persona Management — docs only.** See Persona Config in
design.md "Components". A CEO-requested tone change ships by editing an external document,
not by a code deploy.

## Quality assurance / evaluation

See requirement 6 above for the full approach and the coded harness
(`eval/golden_set.json`, `src/retail_agent/eval.py`, `scripts/run_eval.py`).
In short: a curated golden set + LLM-as-judge scoring as a pre-ship
regression gate, and UX measured passively from production observability
data rather than a separate instrumentation surface. What's coded here is
the prototype's version of that production approach — the periodic
human-cross-check-of-judge-grading half stays a process description, not
code, and there's no CI wiring or golden-SQL/result ground-truth diffing
(design.md "Implementation notes" has the full list of accepted gaps).

## Prototype vs. production scope matrix

| Requirement | Prototype (coded) | Production (design only) |
|---|---|---|
| 1. Hybrid Intelligence | Docs only | Vertex AI Vector Search, human-curated updates |
| 2. Safety & PII Masking | **Coded** — injection-denylist guardrail + untrusted-tool-output handling + name-based PII stripping (schema-verified) | Same, at scale, with a semantic classifier replacing the denylist |
| 3. High-Stakes Oversight | **Coded** — Postgres-backed Saved Reports Store (`ReportsStore`/`psycopg`) + `interrupt()`/`Command(resume=...)`-based confirm-then-delete (`resolve_delete` node) | Same store technology as the prototype (Cloud SQL for Postgres instead of local/docker Postgres); real per-manager auth resolving `owner` instead of the OS username |
| 4. Continuous Improvement | Docs only | Firestore preference store; human-gated system learning |
| 5. Resilience & Error Handling | **Coded** — typed errors, self-correct, backoff | Same, at scale |
| 6. Quality Assurance | **Coded** — `eval/golden_set.json` (9 cases) + deterministic checks + LLM-as-judge harness (`eval.py`, `scripts/run_eval.py`), run manually as a regression gate | Same approach at scale, wired into CI, with judge-drift audits and ground-truth query/result diffing |
| 7. Observability | **Coded** — `tracing.py`'s `llm_call`/`tool_call`/`turn` JSON events (Cloud-Logging-shaped, stderr) + LangSmith auto-instrumentation (free Developer tier, env vars only) | Same structured logs to real Cloud Logging + Monitoring dashboards/alerts on top; LangSmith or a self-hosted tracer, depending on whether the prototype's no-third-party-SaaS trade-off still applies at that scale |
| 8. Agility (Persona) | Docs only | Firestore/Cloud Storage config, admin surface |

5 of 8 requirements are coded, following the build order in `CLAUDE.md`,
which adds Quality Assurance as its final slice after Observability. The
remaining 3 requirements (Hybrid Intelligence, Continuous Improvement,
Agility) are designed here in full but left out of the prototype.

This matrix tracks the eight numbered requirements only. Architecture
components that don't map onto one of them are argued in design.md "Components" rather than
given rows here, so the absence of a row is not itself a claim about
whether something is coded. The Conversation Checkpoint Store and Agent
Service compute are production design throughout, coded nowhere in this
prototype (the prototype's `InMemorySaver` and local-process "compute"
are incidental side effects of using LangGraph/a CLI, not deliberate
implementations of either). The Conversation Store is the exception: it
*is* coded (`conversation_store.py`, wired into `cli.py` — design.md "Components", design.md "Data flow" Resume
path) even though it still isn't one of the eight numbered requirements
and so still gets no row above. The MCP Server is the same kind of
exception — coded (`mcp_server.py`, design.md "Components"), no row — and reinforces three
rows that do exist: Safety (PII stripped server-side for any client),
High-Stakes Oversight (the token delete flow), and Observability
(`tool_call` events with `transport: "mcp"`). The MCP Client (`mcp_client.py`,
design.md "Components") is the third coded component with no row of its own: the agent
loading tools from external MCP servers.
