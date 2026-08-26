# Implementation Notes & Known Gaps

Companion to `docs/design.md` (the HLD). This file holds implementation-level
detail, rejected alternatives, and gaps found during live testing — kept out
of the HLD so that document stays a fast read on a first pass. Nothing here
changes the architecture or the requirement-by-requirement scope described in
design.md §9; it's the "why exactly" and "what we found" behind decisions
summarized there in one or two sentences.

## LLM Provider: Raw SDKs vs. LangChain Chat Model Wrappers

Each provider (`llm_provider.py`, `openrouter_provider.py`) talks to its SDK
directly (`google-genai`, `openai`) rather than through LangChain's chat
model wrappers (`ChatGoogleGenerativeAI`, `ChatOpenAI`). This was weighed
against what design.md §5/§7 actually need: precise, per-SDK exception
classification (`genai_errors.APIError.code`; `openai`'s distinct
per-category exception types) feeding a single classification point after
retries resolve, and an `error_class` on every log line precise enough to
reconstruct a failure from logs alone. A LangChain wrapper sits between this
code and those raw exceptions with its own retry/wrapping behavior, which
would have to be pinned and re-verified (the same live-testing work the
current classification already went through) for uncertain benefit — the two
providers already share one interface (`Provider`) with no branching
elsewhere in the codebase, so LangChain's unification wouldn't simplify
anything the resilience/observability requirements depend on today, only the
OpenRouter-side message/tool-schema translation, which is real but
secondary.

This is a two-provider decision, not a permanent one. If Hybrid Intelligence
(the Golden Bucket) moves from docs-only into coded scope, or a third LLM
provider is added, LangChain's chat model wrappers become worth revisiting:
`init_chat_model()`-style provider swapping and LangChain's
retriever/vector-store integrations reduce real per-provider code at that
point, in a way they don't for the current two-provider, resilience-first
scope.

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
used at all three retry sites (`bq_tool.py`, `llm_provider.py`,
`openrouter_provider.py`), entirely internal to the failing component — the
graph never sees a retry happen. The retry condition and the classification
step are deliberately separate: each site wraps a private
`_call_bq_raw`/`_generate_raw` helper that retries on the client's own raw
exception signal — a type tuple for `bq_tool.py` and `openrouter_provider.py`,
since `google.api_core.exceptions` and the `openai` SDK both raise a
distinct type per failure category, or a `tenacity.retry_if_exception`
predicate for `llm_provider.py`, since `google.genai.errors` lumps every 4xx
into one `ClientError` type and every 5xx into one `ServerError` type,
distinguishable only by a `.code` attribute. Classification into the typed
`AgentError` vocabulary happens exactly once, in the public
`_call_bq`/`generate` method, only after retries are resolved one way or the
other — not inside the retried call itself, which would either log a
failure that later succeeds on retry, or make the retry condition match a
synthetic type this codebase invented rather than the client's real one.

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

**Invalid Gemini API key is misclassified.** `ProviderCircuitBreaker`
(§3/§5) routes to OpenRouter after consecutive Gemini failures. Live
testing surfaced a real classification gap: an invalid Gemini API key
returns HTTP 400 (`INVALID_ARGUMENT`), not 401/403 —
`_classify_genai_error`'s `exc.code in (401, 403)` check doesn't match it,
so it falls through to the generic `ProviderError` instead of the more
specific `ProviderAuthError`. The actual reason (`API_KEY_INVALID`) is
present, but nested three levels into the error response
(`exc.details["error"]["details"][0]["reason"]`) — matching it reliably
would mean depending on that exact nested shape, which is fragile against a
Google-side response format change. Not fixed: both classes are
non-self-correctable and both produce a graceful message, so there's no
functional or safety difference — only the graceful message's wording and
the log's `error_class` are affected, an observability gap rather than a
behavioral one.
