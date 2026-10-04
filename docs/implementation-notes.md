# Implementation Notes & Known Gaps

Companion to `docs/design.md` (the HLD). This file holds implementation-level
detail, rejected alternatives, and gaps found during live testing — kept out
of the HLD so that document stays a fast read on a first pass. Nothing here
changes the architecture or the requirement-by-requirement scope described in
design.md §9; it's the "why exactly" and "what we found" behind decisions
summarized there in one or two sentences.

## Orchestrator: Rejected Alternatives

LangGraph (design.md §3) was weighed against two other categories, both
ruled out on the same axis: coordination machinery this agent doesn't
need, versus machinery it does.

**Role-based multi-agent frameworks (CrewAI, AutoGen).** These solve
coordination between multiple cooperating agents — role assignment, task
handoff, shared memory across agents. This system is one agent running a
tool-calling loop (`get_schema`, `run_query`, and future tools), not a
crew of specialized agents dividing a task. Adopting either framework
would mean carrying their multi-agent abstractions for a shape this agent
never needs.

**Provider-bundled agent runtimes (OpenAI's Agents SDK, Vertex AI Agent
Builder).** Both couple orchestration to one LLM vendor's SDK — the
Agents SDK is OpenAI-specific, Agent Builder is Vertex-specific. That
conflicts with keeping the orchestration loop itself vendor-neutral:
picking either runtime would mean rebuilding the tool-calling loop on top
of a vendor-specific agent loop instead of the vendor-neutral one
LangGraph provides — a property worth keeping even now that Gemini is the
only configured provider, since it's what makes swapping or adding one
later (see the next section) a change contained to `llm_provider.py`
rather than a rewrite of the orchestration loop.

## LLM Provider: Raw SDKs vs. LangChain Chat Model Wrappers

**This decision has been reversed.** `llm_provider.py` originally talked to
`google-genai` directly, with an `openrouter_provider.py` hand-translating
to/from OpenAI's wire format for a second provider (OpenRouter) behind a
hand-rolled `ProviderCircuitBreaker`. Both are gone; `llm_provider.py` now
wraps `langchain-google-genai`'s `ChatGoogleGenerativeAI`, and `graph.py`/
`tools.py`/`cli.py` all speak LangChain's message shape
(`HumanMessage`/`AIMessage`/`ToolMessage`/`SystemMessage`,
`AIMessage.tool_calls`) natively rather than `google-genai`'s
`types.Content`/`types.Part`. The account below is kept for the historical
reasoning — including why it stopped applying — not as a currently-accurate
recommendation.

**The original reasoning, and why each argument stopped holding once
OpenRouter was removed:**

- **"Exception classification is a wash — LangChain doesn't unify
  exceptions."** True at the time: the `langchain-google-genai` and
  `langchain-openai` versions available then let each SDK's native
  exceptions propagate largely unclassified, so a hand-written
  `_classify_*_error` was needed under LangChain just as much as over raw
  SDKs. This has since changed: the installed `langchain-google-genai`
  (4.x) classifies every `google.genai` `ClientError`/`ServerError` into a
  `langchain_core.exceptions.ModelError` subclass
  (`ModelAuthenticationError`, `ModelPermissionDeniedError`,
  `ModelRateLimitError`, `ModelAPIError`, `ModelInvalidRequestError`,
  `ModelNotFoundError`), each carrying an `is_retryable` flag — a real,
  first-party, provider-agnostic exception taxonomy that didn't exist when
  this decision was first made. `llm_provider.py`'s `_classify_error`/
  `_is_transient_error` now key off that taxonomy directly (plus a
  defensive `TimeoutError`/`ConnectionError` catch for failures before any
  HTTP response), which is *less* code than the old `.code`-attribute
  matching it replaced, not more.
- **"A first-party, self-pinned exception surface."** Still technically
  true in the strict sense — `langchain_google_genai`'s `ModelError`
  subclasses are one hop further from this code than `google.genai`'s own
  exceptions were. But this stopped being worth the cost it was paid for:
  it was weighed against paying a translation-module cost for a *second*
  provider (OpenRouter). With no second provider, there's nothing being
  avoided by staying on the raw SDK — the "self-pinned" property was never
  valuable on its own, only as an offset against OpenRouter's translation
  cost.
- **"Direct control over exact request shape (disabling automatic
  function-calling)."** LangChain's chat models don't auto-execute tool
  calls either — `.bind_tools()` + `AIMessage.tool_calls` already leaves
  the calling loop entirely to `graph.py`, the same property the old
  `automatic_function_calling=... disable=True` config was set to
  guarantee. Nothing was given up here; the mechanism just changed shape.
- **"Matches the project's scope discipline — a `Provider` protocol in a
  few dozen lines beats an N-provider abstraction this prototype doesn't
  need."** Also reversed: once OpenRouter's translation module
  (`_to_openai_messages`/`_to_openai_tools`/`_to_genai_response`) is
  deleted, the *simpler* code path is the one with no translation module at
  all — using LangChain's native shapes throughout instead of a
  `google-genai`-shaped internal representation. Keeping the raw-SDK
  approach for Gemini alone, after removing the only reason a translation
  layer existed, would have meant keeping a `Provider` protocol duck-typed
  against a single implementation for no remaining benefit.

**What this buys going forward.** The property that motivated the original
translation-avoidance argument — adding a provider is cheap — now comes for
free instead of costing a hand-rolled module: every LangChain chat model
(`ChatOpenAI`, `ChatAnthropic`, `ChatOllama`, etc.) returns the same
`AIMessage`/`ToolMessage` shapes `graph.py`/`tools.py`/`cli.py` already
consume, so adding or swapping a provider is now contained to
`llm_provider.py` (constructor kwargs and the exception-classification
buckets) rather than touching the orchestration loop. This is the direct
opposite of the two-provider state this decision was first made in, where
adding OpenRouter required exactly that hand-rolled translation module.

## BigQuery PII Stripping: Name-Based Matching, Not a True Allow-List

The BQ wrapper strips PII columns post-execution by matching each returned
column's bare name (case-insensitive, table-qualifier stripped) against a
maintained PII field registry. This is name-based matching, not a true
schema-driven allow-list: it can't tell that `first_name AS x` is PII once
renamed, and it doesn't automatically catch a column added to the live
schema tomorrow that isn't in the registry yet.

Both gaps are accepted trade-offs, not oversights. A real allow-list (only
pass through columns from a pre-approved safe list) would also silently drop
legitimate computed/aggregated columns (`SUM(sale_price) AS total_revenue`),
which an analysis agent needs to return constantly — so it isn't viable
without full SQL parsing, which was itself rejected (an AST parser was
considered and rejected as over-building against a threat model the
service account's IAM read-only role already covers). The renaming gap is
covered by defense-in-depth (the input-side guardrail and the IAM role), not
by this function.

The registry itself was verified against the live `users` schema during
prototype development, not just typed from the assignment brief — that check
caught two columns absent from the brief's original PII list
(`postal_code`, and `user_geom`, a GEOGRAPHY point encoding the same
location `latitude`/`longitude` carry) before they could leak. That's
exactly the schema-drift scenario this control needs to survive; in
production this check should be a scheduled job diffing the live schema
against the registry, not a one-time manual pass.

## Input Guardrail: Regex Denylist, Not a Classifier

`guardrail.check_user_input` (wired into `graph.py` as the `guardrail`
node, run before `call_model` on every turn) blocks a fixed set of
prompt-injection/jailbreak phrasings — "ignore previous instructions",
"you are now", "reveal your system prompt", "developer mode", "jailbreak",
"bypass your rules", and a few close variants. This exists specifically to
close a gap found during a design review: design.md had described an
"input-side guardrail" as coded since the Safety & PII Masking requirement
was first scoped in, but no such control actually existed in code — the
only enforcement was the system prompt's own "politely decline anything
else" instruction, which a model can simply choose not to follow. This
module is the fix for that gap, not a pre-existing control.

It's a regex/keyword denylist, deliberately, for the same reason
`sql_safety.py`'s read-only check is a regex allowlist rather than a full
parser: cheap, deterministic, and zero added latency or LLM cost ahead of
the first real model call — important because this check runs on *every*
turn, including all the ordinary ones. The trade-off that comes with a
denylist is real: it catches the common, mechanical forms of "override
your instructions" phrasing, but it will not catch a semantically
equivalent request phrased differently enough to dodge every pattern (a
production system would replace or supplement it with a small
classification pass — "analysis | off-topic | malicious" — at the cost of
one extra cheap LLM call per turn). It also does not attempt the broader
off-topic classification named in the assignment brief ("politely decline
anything else") — that remains the system prompt's job, unenforced beyond
the model's own instruction-following, exactly as it was before this
module existed. The two are different problems: malicious intent is
worth a hard, testable control because a jailbreak attempt is adversarial
by construction; "is this on-topic" is a much fuzzier classification where
a regex denylist would produce more false positives (blocking legitimate
questions) than it's worth for a prototype.

This module only covers requests arriving through the user's own chat
message. A related but distinct path — adversarial text embedded in *data*
that comes back from `run_query`/`get_schema` (e.g. a crafted product name
field read back into the model's context) — can't be caught by inspecting
the user's message at all, since the attacker's text never appears there.
That path is closed separately, in the system prompt (`cli.py`), which
instructs the model to treat all tool output as data, never as
instructions. See `test_guardrail.py` for the denylist's test coverage and
`test_cli.py::test_system_instruction_treats_tool_output_as_untrusted_data`
for the untrusted-tool-output check.

## Two Retry Mechanisms: Backoff vs. Self-Correct

Design.md §5 names two retry mechanisms that share vocabulary ("transient,"
"retry") on purpose, since both describe the same real-world condition —
worth spelling out so they don't read as contradicting each other:
**backoff** (inside `bq_tool.py`/the providers, via
`resilience.bounded_backoff`) retries the *same* call automatically when the
client's raw exception looks transient. **Self-correct** (in `graph.py`,
gated by `self_correctable`) lets the *model* retry with a *different*
query, only after a typed error reaches the graph. A
`QueryTransientError`/`ProviderTransientError` is a case where the first
already ran, twice, and failed both times — which is exactly why the second
is `self_correctable = False` for it: not a contradiction, two different
questions ("is retrying this call worth attempting at all" vs. "would a
*different* query fix it") answered differently for the same failure, at two
different points in the pipeline.

**Backoff implementation detail.** Exponential backoff, 2 attempts total,
via a shared `resilience.bounded_backoff(...)` (`tenacity`-based) policy
used at both retry sites (`bq_tool.py`, `llm_provider.py`), entirely
internal to the failing component — the graph never sees a retry happen.
The retry condition and the classification step are deliberately
separate: each site wraps a private `_call_bq_raw`/`_generate_raw` helper
that retries on the client's own raw exception signal — a type tuple for
`bq_tool.py`, since `google.api_core.exceptions` raises a distinct type per
failure category, or a `tenacity.retry_if_exception` predicate for
`llm_provider.py`, since not every failure `ChatGoogleGenerativeAI.invoke`
can raise is a classified `langchain_core.exceptions.ModelError` (a bare
`TimeoutError`/`ConnectionError` from a failure before any HTTP response
isn't), so the predicate checks `isinstance(exc, ModelError) and
exc.is_retryable` first and falls back to a raw `TimeoutError`/
`ConnectionError` check. Classification into the typed `AgentError`
vocabulary happens exactly once, in the public `_call_bq`/`generate`
method, only after retries are resolved one way or the other — not inside
the retried call itself, which would either log a failure that later
succeeds on retry, or make the retry condition match a synthetic type this
codebase invented rather than the client's real one.

The BQ wrapper's typed errors: `QuerySyntaxError` (BigQuery
`BadRequest`/`NotFound`/`Conflict`), `QueryPermissionError`
(`Forbidden`/`Unauthorized`), `QueryTransientError`
(`ServerError`/`TooManyRequests`/`RetryError`/timeouts), and
`QueryExecutionError` itself as a defensive fallback for any exception type
not yet classified — logged as `bq_unclassified_exception` so a recurring
one gets a new typed subclass added, and treated as terminal (not retried)
until it does.

## Known Gaps Found During Testing

**Self-correct operates on the whole round, not per tool call.** A single
model turn can fire multiple function calls at once (e.g. `get_schema` on
two tables before writing SQL). If that round has a mix of outcomes — say
one call succeeds or fails self-correctably, but another fails with a
non-self-correctable error — `route_after_tools` gives up immediately for
the *entire* turn, discarding the other calls' results even though they
were independently fine. This is a deliberate limitation, not an oversight:
each `call_model` regenerates the *entire* set of function calls fresh from
the full history, so there's no mechanism to retry only the fixable call
while preserving a prior success from the same round — and retrying anyway
would be wasted cost, since the blocked call would just fail identically
again. A more granular per-call retry design (tracking retry state per tool
call, accepting the blocked one as a permanent gap, returning a partial
answer) is a valid alternative this architecture doesn't support today.
Covered by `test_mixed_round_non_self_correctable_error_wins_over_self_correctable_one`
and `test_mixed_round_non_self_correctable_error_wins_even_over_a_success`
in `test_graph.py`.

**Empty-result sanity check doesn't fire for `COUNT(*)` queries.** The
empty-result mechanism (one extra pass, tracked by a turn-scoped
`empty_result_sanity_checked` flag, nudging the model to sanity-check its
own filters/joins before accepting "zero rows" as the real answer) checks
`row_count == 0`, which only describes an *unaggregated* empty result (a raw
`SELECT` matching no rows). A `COUNT(*)` query — almost certainly the single
most natural way an LLM answers any "how many" question — always returns
exactly one row (the count itself, however small), so the note never fires
for the most common shape of the exact question class it exists to catch.
Not fixed; recorded because it means the mechanism's real-world trigger rate
is likely far lower than the design assumed. A related, smaller gap: the
flag is turn-scoped, not query-scoped, so if a turn asks a compound question
needing two unrelated `run_query` calls and *both* unexpectedly return zero
rows, only the first gets nudged. A per-query-identity fix (keyed by SQL
text, plus a small total cap) would close this but isn't implemented — a
boolean is the simplest thing that catches the common case without risking
runaway nudging on a genuinely-zero answer.

**Invalid Gemini API key is still misclassified, even after the move to
`langchain-google-genai`.** This gap predates the LangChain migration and
was re-verified live afterward rather than assumed fixed by it: an invalid
Gemini API key returns HTTP 400 (`INVALID_ARGUMENT`), not 401/403. That's a
Google API response-shape fact, not a client-library quirk — confirmed by
running `GeminiProvider.generate(...)` against a real invalid key and
observing `langchain-google-genai`'s own `_CLIENT_ERROR_TYPES` mapping (400
→ `GoogleInvalidRequestError`/`ModelInvalidRequestError`, not
`GoogleAuthenticationError`/`ModelAuthenticationError`) reach
`llm_provider.py`'s `_classify_error` unchanged, which correctly falls
through to the generic `ProviderError` given what it was handed. The new
unified `ModelError` taxonomy (see the LLM Provider section above) makes
the classification code itself simpler and more robust than the old
`.code`-attribute matching, but it can't fix a case where the upstream API
itself doesn't report the status code the taxonomy is keyed on. Not fixed:
both classes are non-self-correctable, so there's no functional or safety
difference from misclassification alone — only the log's `error_class` is
affected (the curated `graceful_message` text isn't reachable from this
path regardless of which class is raised — see the gap below), an
observability gap rather than a behavioral one.

**Provider error classification isn't wired into behavior the way it's
typed.** Three typed provider error classes exist (`ProviderError`,
`ProviderTransientError`, `ProviderAuthError`), all `self_correctable =
False` — correctly, no query-style self-correct move exists for a model API
failure — but the distinction isn't threaded any further than that:
`call_model` in `graph.py` has no try/except around `provider.generate(...)`,
so a raised `ProviderError` propagates straight out of `graph.invoke()` and
is caught only by `cli.py`'s top-level `except AgentError as exc:
print(f"...{exc}")`, which prints `str(exc)` — the three curated
`graceful_message` strings on the provider error classes are consequently
never invoked by any live path, since `graceful_message_for` is only ever
called from the graph's `give_up` node, which a provider error never
reaches. (The earlier version of this gap also noted that
`ProviderCircuitBreaker` treated `ProviderAuthError` and
`ProviderTransientError` identically when counting toward opening the
breaker, cycling open forever against an unrecoverable bad key — that's
moot now that the circuit breaker is gone entirely; see design.md §3.) Not
fixed: no crash risk (the CLI loop still never dies, per §5's guarantee),
but a misconfigured API key surfaces as a raw provider error string rather
than the friendlier curated message written for exactly that case.

## High-Stakes Oversight: Delete-Confirmation Implementation Notes

Companion detail to design.md §3/§4/§6 for the Saved Reports Store and
`interrupt()`-based confirm-then-delete flow, added when that requirement
moved from docs-only to coded.

**LangGraph replay semantics before `interrupt()` are a non-issue here by
construction.** `graph.py`'s `resolve_delete` node calls `interrupt(...)`
partway through its body; everything before that call (the
`reports_store.find_candidates(...)` query) re-executes from scratch when
the graph resumes via `Command(resume=...)`, a documented LangGraph
behavior. This turns out not to matter here: the node's only `return`
statement is reached exclusively on the pass that completes (zero
candidates, a store error, or the resumed pass after confirmation) — the
pausing pass unwinds internally before ever reaching a `return`, so no
`ToolMessage`/`AIMessage` is ever double-appended. A side effect worth
naming rather than treating as accidental: because `find_candidates` runs
again on resume, the delete always operates against a *freshly re-queried*
candidate list, not a snapshot frozen at the moment the confirmation
prompt was shown. In the time between the prompt and the "yes," another
process could in principle have added or removed a report matching the
same criteria. This is judged acceptable for the prototype's single-user,
mostly-single-session usage pattern; a production system serving
concurrent managers might instead pass the exact candidate ids through the
`interrupt()` payload and delete by id rather than by re-running the
filter, trading a (very) small staleness risk for guaranteed
re-derivation of "what exactly matches this filter right now."

**Owner identity is `getpass.getuser()`, not real authentication.** There
is no login system in the CLI prototype — one OS process, one user for its
whole lifetime. `owner` is threaded into `build_graph(...)` once at
startup and closed over by every node that touches `ReportsStore`. Every
store method still enforces owner scoping (`find_candidates`,
`delete_reports` both filter `WHERE owner = %s`, and `delete_reports`
re-asserts it even though `find_candidates` already filtered — belt and
suspenders against a bug ever passing an id from a different owner) — so
the *scoping mechanism* requirement 3 asks for ("never cross-user") is
real and tested (`test_delete_reports_scoped_to_wrong_owner_deletes_nothing`
in both `test_reports_store.py` and, via `FakeReportsStore`,
`test_graph.py`), even though the *identity source* feeding it is a
placeholder for real per-manager auth.

**Confirmation parsing is a deterministic keyword allowlist
(`_is_affirmative` in `graph.py`), not an LLM call.** Matches this
codebase's established pattern (`guardrail.py`'s injection denylist,
`sql_safety.py`'s keyword allowlist): cheap, zero added latency/cost,
adequate for the narrow "yes or anything else" shape of a reply to a
plain confirmation prompt. A resumed turn bypasses the `guardrail` node
entirely (resuming re-enters `resolve_delete` directly, not `START`), but
this isn't a safety gap: the allowlist is narrow and everything unmatched
aborts, so there's no way for adversarial phrasing on the confirmation
turn to talk its way into a delete it wouldn't otherwise get. The real
cost is UX, not safety: if the user types a brand-new, unrelated question
instead of a yes/no while a delete is pending, `_is_affirmative` treats it
as a "no" — the delete is correctly aborted, but the new question is
silently discarded rather than answered, and the user has to re-ask it
next turn. Solving this would mean classifying "is this a yes/no reply or
a new topic," reintroducing the LLM-call cost this design deliberately
avoids for a confirmation step described in design.md §6 as "deliberately
boring." Not fixed; documented as an accepted trade-off, same spirit as
the `COUNT(*)` empty-result gap above.

**A round mixing `delete_reports` with another tool call drops the other
call.** `route_after_model` routes the *entire* round to `resolve_delete`
if any tool call in it is named `delete_reports`, discarding any other
tool call the same model turn also requested. This mirrors the existing,
already-documented precedent above ("Self-correct operates on the whole
round, not per tool call") for exactly the same underlying reason: nothing
in this architecture supports partially executing a round and retrying
only the rest, and `call_model` will regenerate the full call set fresh
next turn if the dropped call is still needed. Covered by
`test_mixed_round_delete_reports_and_another_tool_call_takes_over_whole_round`
in `test_graph.py`.

**`ReportsStore` deliberately has no `resilience.bounded_backoff` retry,
unlike `bq_tool.py`/`llm_provider.py`.** Both of those wrap calls to a
genuinely remote, rate-limited third-party API where "the exact same
request, retried a moment later, might succeed" is a real, common
condition. A Postgres connection — whether the local `docker-compose`
instance or Cloud SQL in production — doesn't have that failure mode in
the same way for this workload; a `psycopg.Error` here is far more likely
to mean a real, non-transient problem (bad connection string, database
down, a schema/permissions issue) that a bare retry wouldn't fix. Adding
backoff here would be unearned complexity copied from a different failure
model. If `REPORTS_DATABASE_URL` is ever pointed at something with a
meaningfully different failure profile (a connection-pooled proxy under
heavy load, say), this is worth revisiting — but it's out of scope for
the current deployment shape.

**Postgres from the start, not SQLite-then-swap.** This requirement was
initially implemented against a local SQLite file (zero new dependency,
matching this codebase's general bias toward minimal dependencies for a
take-home prototype) and then deliberately migrated to Postgres
(`psycopg`, v3, no ORM) mid-implementation once real deployment plans
came up — Postgres is what design.md's Saved Reports Store section always
named as the production store technology (Cloud SQL for Postgres), so
this removes a "swap it before shipping" step rather than adding
architectural complexity the assignment didn't need. `docker-compose.yml`
provides one-command local Postgres (two databases — one for interactive
use, one for the test suite, so `uv run pytest` can never truncate
manually-saved reports); `REPORTS_DATABASE_URL` repoints at any other
reachable Postgres (a local install, Cloud SQL, etc.) with no code change.
The practical cost of this choice: unlike every other unit test file in
this repo, `test_reports_store.py` is not hermetic — it requires a real,
reachable Postgres to pass at all, since `ReportsStore` (unlike
`BigQueryTool`/`GeminiProvider`) has no fake/mock double standing in for
it at that layer. `test_graph.py`/`test_cli.py`'s `FakeReportsStore`
doubles keep the *graph-level* delete/save/list tests hermetic regardless
— only the store's own CRUD/scoping tests need live Postgres.

## Observability: LangSmith Instead of Self-Hosted Langfuse

design.md's Observability section (§3) originally specified self-hosted
**Langfuse** for conversation-level tracing, explicitly rejecting
**LangSmith** by name — the stated reason was that LangSmith is
third-party SaaS by default, and tracing should respect the same PII
boundary the rest of this system does by staying inside this project's
own infra rather than handing prompts/SQL/tool output to a vendor.

That decision was reversed when this requirement moved from docs-only to
actually coded. Two things changed the calculus, in order:

1. **A hard constraint appeared that neither original option satisfied
   for free**: no service requiring billing or a credit card. Self-hosted
   Langfuse itself has no such requirement (it's just a docker-compose
   service, like the existing Postgres setup), but *self-hosting it
   doesn't remove the third-party-SaaS objection from the table* — it was
   never Langfuse-the-vendor being avoided, it was the "trace data leaves
   this project's infra" property, and self-hosting is exactly how you
   avoid that. So self-hosted Langfuse was still the right shape under the
   original PII-boundary reasoning.
2. **The user explicitly said third-party data sharing is acceptable for
   this prototype.** That single statement removes the entire premise the
   original Langfuse-over-LangSmith argument was built on — once "must not
   leave this project's infra" is off the table, there's no remaining
   reason to prefer Langfuse, and a real reason to prefer LangSmith: it
   auto-instruments every LangChain/LangGraph run via three environment
   variables (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
   `LANGSMITH_PROJECT`) with **zero application code** — no
   `CallbackHandler` to construct, no per-call `config={"callbacks": [...]}`
   wiring, no explicit flush-on-exit. Langfuse's LangChain integration
   needs all three of those. LangSmith's free Developer tier also matches
   the no-credit-card constraint (verified directly against
   smith.langchain.com's docs: "no credit card required," 5k traces/month,
   14-day retention, 1 seat) — so the constraint that triggered this
   whole reconsideration is satisfied either way; LangSmith won on being
   less code once the PII-boundary tiebreaker was removed.

This is recorded as a reversal, not a silent contradiction, because the
original reasoning wasn't wrong — it was correct for a system where
"never hand data to a third-party SaaS" is a real constraint. It stopped
applying here specifically because the user relaxed that constraint for
this prototype. A production deployment run at a scale or audit posture
where that constraint matters again should re-read design.md §3's
Observability section, which keeps a line for exactly this: self-hosting
a tracer instead of LangSmith remains a valid choice at that point, using
the same env-var-free-vs-explicit-integration trade described above,
just decided the other way.

One functional consequence worth noting: because LangSmith needs no code
in this repo, there's also no branching logic to unit test — the "is
tracing enabled" question is answered entirely by environment variables
that `langchain-core` reads directly, never touched by
`retail_agent`'s own code. Verified manually (not via an automated test,
since there's no code path to exercise) that an invalid/missing
`LANGSMITH_API_KEY` degrades gracefully: `langsmith.client` logs its own
warning to stderr and the turn completes normally — consistent with the
"no third-party failure crashes the CLI" property every other external
dependency in this system already has (§5).

## Quality Assurance: Golden-Set Eval Harness Implementation Notes

Companion detail to design.md §6/§7/§9 for the eval harness
(`eval/golden_set.json`, `src/retail_agent/eval.py`,
`scripts/run_eval.py`), added when this requirement moved from docs-only
to coded.

**Why this is a manual script, not a pytest-collected test.** Every golden
case runs a real question through the real compiled graph — live Gemini
for the agent's own generation, live Gemini again for the judge, live
BigQuery for every `run_query`/`get_schema` call. That's the identical
justification `tests/integration/conftest.py` already documents for why
those tests require real *exported* environment variables rather than
loading `.env`: `uv run pytest` must never silently make billed, live
calls just because a developer's `.env` happens to be configured. Rather
than fold the harness into that suite as a skipped-by-default test file,
it's a standalone script with its own CLI (`scripts/run_eval.py`) that
prints a human-readable report and writes a JSON file to disk — closer in
spirit to `scripts/render_graph.py` (a tool you run and read the output
of) than to a pass/fail assertion `pytest` collects. `--fail-under` exists
specifically so a CI pipeline could still treat a run of this script as a
gate later, without needing it to be a pytest test to do so.

**Why the judge is a bare `ChatGoogleGenerativeAI`, not `GeminiProvider`.**
`GeminiProvider.__init__` (`llm_provider.py`) unconditionally calls
`.bind_tools(TOOLS)` — the same five tools (`run_query`, `get_schema`,
`save_report`, `list_reports`, `delete_reports`) the agent itself uses.
Reusing `GeminiProvider` for the judge would let a judge call actually
invoke one of those tools (or at minimum return a spurious `tool_calls`
list the JSON-parsing logic in `judge_case` never expects), which is not
a capability a scoring pass over already-produced text should have.
`eval.run_eval` constructs a second, separate `ChatGoogleGenerativeAI`
directly — no tools bound, no `bounded_backoff`/retry wrapper the way
`GeminiProvider._generate_raw` has, and no typed-error classification the
way `llm_provider._classify_error` does. That asymmetry is deliberate, not
an oversight: a judge-call failure degrading one case's verdict to
`parse_error=True` (via `judge_case`'s defensive `try`/`except` around the
JSON parse — a raised provider exception would need its own catch, added
if this becomes a real gap in practice) is an acceptable outcome for a
manually-run report; it doesn't need the same resilience machinery a
live, user-facing conversation turn does.

**Deterministic checks before the judge, same layering principle as
elsewhere in this codebase.** `_check_expected_tool`/`_check_expected_tables`/
`_check_expected_keywords`/`_check_pii_leak` all run before `judge_case` is
ever called, mirroring the existing "cheap deterministic check first"
shape (`guardrail.py`'s denylist, `sql_safety.py`'s keyword allowlist,
`graph._is_affirmative`) rather than asking a single LLM-as-judge call to
do everything. `_check_expected_tables`/`_check_expected_keywords` are
loose OR-matches against a `list[str]`, not exact-SQL or exact-answer
matching — an LLM's generated SQL and prose phrasing are not deterministic
between runs even for the same question, so a stricter match would produce
false failures unrelated to actual answer quality. `_check_pii_leak` is a
single email-regex check, gated on `case.pii_sensitive`: it is
belt-and-suspenders on top of the BQ wrapper's column stripping
(`pii.strip_pii_columns`, already the hard control — see design.md §6
requirement 2), not a new PII-detection mechanism in its own right; it
would not catch a full name or street address leaking in prose, only an
email-shaped string literally present in the final answer text.

**Found and fixed during live testing: one case's provider failure was
crashing the whole run.** The first live run of `scripts/run_eval.py` hit a
real Gemini free-tier rate limit (`ProviderTransientError`, HTTP 429) on a
single case — and because `run_eval`'s loop had no per-case error handling,
that one exception propagated straight out of `run_eval`, aborting the
script before any of the later cases ran or any report was written. Fixed
by extracting `_score_case(graph, judge_model, case) -> CaseReport`, which
wraps `run_case(...)` in a `try`/`except Exception` and reports a case-level
`run_error` instead of letting the exception escape — the same
"no path crashes the caller" property `cli.py`'s per-turn
`except AgentError`/`except Exception` already gives the interactive REPL
loop, applied here to a batch harness instead of a single turn. A case with
`run_error` set is reported as failed (never silently skipped or counted as
a pass) and the judge is never called for it, since there's no answer to
judge. Covered by `test_score_case_contains_a_run_case_exception` in
`tests/test_eval.py`.

**Accepted gaps, not fixed:**

- **9 cases is not a statistically meaningful sample.** It's sized to be
  "a small eval set" per the assignment brief and this task's own scope,
  covering each capability the brief names once, not to support a
  confident pass-rate number. A production version (design.md §6/§9) would
  grow this from real analyst-curated Golden Bucket trios over time, the
  same human-in-the-loop growth path already described for the Golden
  Bucket itself.
- **No ground-truth query/result diffing.** The harness has no reference
  "correct" SQL or expected row values to diff against — `plausible` in
  `JudgeVerdict` is the judge's coherence read on the final answer, not a
  verification against a known-correct number. This is the same
  "expected SQL shape and report themes," not exact SQL, framing
  design.md's Hybrid Intelligence/Golden Bucket section already uses for
  why trios store shape, not a canonical answer to byte-match.
- **Single run, not averaged.** Each case runs through the agent and the
  judge exactly once per `scripts/run_eval.py` invocation. Gemini's output
  isn't fully deterministic, so a case that fails once might pass on a
  re-run and vice versa — there's no majority-vote-over-N-runs smoothing.
  Diffing `--out`'s JSON across multiple manual runs is the closest this
  harness gets to noticing that variance today.
- **No historical trend tracking beyond the `--out` file itself.** Each
  run overwrites (or is written to a differently-named) JSON report; there
  is no accumulation of pass-rate-over-time the way `design.md`'s
  production framing ("re-run as a regression gate before any
  prompt/persona/bucket change ships") implies a mature version would
  have — an engineer running this manually is expected to keep or diff
  past `--out` files themselves.
- **The judge can itself be wrong or inconsistent** — a known, general
  limitation of LLM-as-judge approaches, which is exactly why design.md
  §6 always specified "periodically cross-checked against human grading"
  as part of the production design. That human cross-check is a process
  description in this design, not something this harness automates.

## MCP Server Implementation Notes

Companion detail to design.md §3's MCP Server section.

**Why a two-step token, not MCP elicitation.** MCP has a native way for
a server to ask the user something mid-tool-call (elicitation), and a
`delete_reports` tool that elicits a yes/no would look closest to the
CLI's `interrupt()`. It was rejected because it only works in clients
that implement elicitation; against one that doesn't, the server either
can't delete at all or has to fall back to deleting unconfirmed. The
`preview_delete`/`confirm_delete` split works with every client, and its
guarantee — nothing is deleted that wasn't previewed — is enforced
entirely server-side and covered by hermetic tests
(`tests/test_mcp_server.py`). What it can't enforce is that a *human*
saw the preview: a client model could call both tools back to back. The
tool descriptions and server `instructions` forbid that, but it's
advisory, the same way the CLI system prompt is advisory about
off-topic questions. Elicitation, where supported, would close that gap
and could be layered on later without removing the token check.

**Why the token map is in process memory.** A stdio MCP server is one
process per connected client, so preview and confirm always reach the
same process; a dict with a TTL is enough. Expired tokens are swept on
each `preview_delete`, so the map can't grow without bound. This stops
being true the moment the server runs as more than one instance behind
HTTP — see design.md §3's production note.

**Why stdio only.** stdio is what local clients (Claude Code, Claude
Desktop, MCP Inspector) launch, needs no auth or port, and matches the
prototype's single-user, `getpass.getuser()` identity model. Streamable
HTTP is a one-line `.run(...)` change in the SDK, but serving it
honestly means real per-caller identity, which the prototype doesn't
have anywhere.

**Why the CLI agent doesn't consume its own MCP server.** Switching
`graph.py` to load tools through `langchain-mcp-adapters` would teach the
client side of MCP, but it adds a process boundary and a serialization
hop under every tool call, and it would move `delete_reports` behind the
server's token flow — breaking the graph's `resolve_delete`/`interrupt()`
path, which is a coded requirement. In-process calls keep the CLI's
behavior and tests unchanged; the MCP server is additive.

**Errors leak a little BigQuery detail, deliberately.** `AgentError`
messages are passed through verbatim so the client model can
self-correct, and a BigQuery syntax error's message includes the job URL
and GCP project number. That's the same text the CLI agent already
hands Gemini, and a project number isn't a credential, so it's accepted.
Messages from unexpected (non-`AgentError`) exceptions are never passed
through.

**`load_config(require_gemini=False)`.** The server never calls Gemini,
so it shouldn't refuse to start without `GEMINI_API_KEY`. Setup shared
with the CLI (`build_bq_tool`, `build_reports_store`) lives in `cli.py`
and is imported, not copied.
