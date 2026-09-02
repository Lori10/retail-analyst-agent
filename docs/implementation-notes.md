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
conflicts directly with the provider-agnostic `Provider` interface
(design.md §3) the Gemini/OpenRouter circuit breaker depends on: picking
either runtime would mean rebuilding the fallback mechanism on top of a
vendor-specific agent loop instead of the vendor-neutral one LangGraph
provides.

## LLM Provider: Raw SDKs vs. LangChain Chat Model Wrappers

Each provider (`llm_provider.py`, `openrouter_provider.py`) talks to its SDK
directly (`google-genai`, `openai`) rather than through LangChain's chat
model wrappers (`ChatGoogleGenerativeAI`, `ChatOpenAI`).

**Exception classification is a wash, not an argument for raw SDKs.**
design.md §5/§7 need precise, per-SDK exception classification
(`genai_errors.APIError.code`; `openai`'s distinct per-category exception
types) feeding a single classification point after retries resolve, and an
`error_class` on every log line precise enough to reconstruct a failure from
logs alone. LangChain has no unified exception taxonomy across chat
models — a wrapper still lets each SDK's native exceptions propagate (or
re-wraps them per-integration), so a `_classify_*_error` function of the
same shape as today's would still need writing underneath a LangChain layer.
The work doesn't disappear either way; it just moves one call frame up, to
sit around `.invoke()` instead of around `generate_content()`/
`chat.completions.create()`.

**What raw SDKs actually buy, once that's stripped out:**

- **A first-party, self-pinned exception surface.** Catching
  `genai_errors.APIError` from the `google-genai` package this code already
  imports directly is one hop. Catching whatever
  `ChatGoogleGenerativeAI.invoke()` raises is a second-hand surface —
  contingent on which client library `langchain-google-genai` wraps
  internally, a choice this codebase doesn't control and that could change
  on a routine dependency bump, silently breaking the classifier without a
  corresponding change on this side.
- **Direct control over exact request shape.** `llm_provider.py`'s
  `_generate_raw` explicitly sets
  `automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)`
  — a deliberate choice that the graph, not the SDK, drives the tool-calling
  loop. `.bind_tools()` and LangChain's message/tool-call shaping carry their
  own opinions here that would need verifying against this design rather
  than being set directly.
- **Matches the project's own scope discipline.** CLAUDE.md's build
  philosophy is "prefer simple and working over clever." At two providers,
  one of which needs no translation, the `Provider` protocol already does
  the one job needed (`generate(...)`) in a few dozen lines with nothing
  extra to pin or debug through; LangChain's `Runnable`/LCEL layer solves an
  N-provider problem this prototype doesn't have yet.

**What genuinely does favor LangChain, and scales with provider count:**
message/tool-schema translation. LangChain's chat models normalize message
history (`BaseMessage`) and function-calling (`.bind_tools`,
`AIMessage.tool_calls`, `ToolMessage`) into one shape across providers. This
codebase doesn't have that — the canonical shape is `google-genai`'s
`types.Content`/`types.Tool`, so any provider that isn't Gemini needs a
hand-rolled translation module. `openrouter_provider.py`'s
`_to_openai_messages`, `_to_openai_tools`, and `_to_genai_response` are
exactly that, written by hand because OpenRouter speaks OpenAI's format, not
Gemini's. Real cost, accepted at two providers since one of the two (Gemini)
needs no translation in the first place — it would not stay small at three
or more.

This is a two-provider decision, not a permanent one. The trigger to
revisit is specifically provider *count*: the exception-surface-stability
and request-control arguments don't weaken as providers are added, but the
translation savings compound, and past some point they outweigh the value of
keeping direct control over the request shape. If a third LLM provider is
added, or Hybrid Intelligence (the Golden Bucket) moves from docs-only into
coded scope and pulls in LangChain's retriever/vector-store integrations
anyway, that's the point to re-run this trade-off.

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
non-self-correctable, so there's no functional or safety difference from
misclassification alone — only the log's `error_class` is affected (the
curated `graceful_message` text isn't reachable from this path regardless
of which class is raised — see the gap below), an observability gap rather
than a behavioral one.

**Provider error classification isn't wired into behavior the way it's
typed.** Three typed provider error classes exist (`ProviderError`,
`ProviderTransientError`, `ProviderAuthError`), all `self_correctable =
False` — correctly, no query-style self-correct move exists for a model API
failure — but the distinction isn't threaded any further than that. Two
concrete effects: (1) `call_model` in `graph.py` has no try/except around
`provider.generate(...)`, so a raised `ProviderError` propagates straight
out of `graph.invoke()` and is caught only by `cli.py`'s top-level `except
AgentError as exc: print(f"...{exc}")`, which prints `str(exc)` — the three
curated `graceful_message` strings on the provider error classes are
consequently never invoked by any live path, since `graceful_message_for`
is only ever called from the graph's `give_up` node, which a provider error
never reaches. (2) `ProviderCircuitBreaker.generate()` catches the generic
`except ProviderError`, treating `ProviderAuthError` and
`ProviderTransientError` identically when counting toward opening the
breaker. A `ProviderTransientError` (rate limit, 5xx) plausibly clears
after `PROVIDER_COOLDOWN_SECONDS`; a `ProviderAuthError` (bad/revoked key)
never will, so today the breaker cycles open indefinitely against a Gemini
key that will never recover, quietly routing every subsequent call through
OpenRouter instead of surfacing a distinct "your Gemini credentials are
broken" message. Not fixed: no crash risk (the CLI loop still never dies,
per §5's guarantee) and OpenRouter still answers the question — but it
silently masks a configuration problem behind the fallback provider and
leaves the auth-specific curated message unused for exactly the case it was
written for.
