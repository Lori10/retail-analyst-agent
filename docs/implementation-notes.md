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
