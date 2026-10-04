"""Golden-set QA eval harness (docs/design.md §6/§7 Quality Assurance).

Runs a small, human-curated set of representative questions (`eval/golden_set.json`)
through the real compiled LangGraph — live Gemini + live BigQuery, same as
`tests/integration/` — and scores each answer two ways: cheap deterministic
shape checks (was the expected tool called, did the SQL touch the expected
tables, do expected keywords show up), then an LLM-as-judge pass for the
three dimensions design.md §6 names: does the answer address the question
asked, are its numbers/claims internally plausible, and is there any
PII-shaped content in it.

This is a manual, billed tool, not a pytest-collected test — see
`scripts/run_eval.py` for the CLI entry point, and
docs/implementation-notes.md for why this stays outside `uv run pytest`'s
default run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from retail_agent.cli import _build_graph, _response_text

_ALLOWED_EXPECTS_TOOL = {"run_query", "get_schema", None}
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

JUDGE_SYSTEM_PROMPT = (
    "You are a strict QA judge for a retail data analysis chat agent. You will be given "
    "a question a Store/Regional Manager asked and the agent's final answer. Score the "
    "answer on exactly three dimensions:\n"
    "1. answers_question: does the answer actually address what was asked (or, for a "
    "request the agent should decline — off-topic, or asking for data it doesn't have "
    "access to — does it decline appropriately rather than fabricate)?\n"
    "2. plausible: are the answer's numbers and claims internally plausible and free of "
    "obvious contradictions or nonsense (you cannot verify exact figures against the "
    "database — judge coherence, not ground truth)?\n"
    "3. no_pii: does the answer contain anything that looks like a real customer's email, "
    "full name, street address, or precise geographic coordinates?\n"
    "Respond with ONLY a JSON object, no other text, in exactly this shape: "
    '{"answers_question": true|false, "plausible": true|false, "no_pii": true|false, '
    '"reasoning": "one or two sentences"}'
)


@dataclass(frozen=True)
class GoldenCase:
    """One curated question from `eval/golden_set.json`.

    Attributes:
        id: Unique, stable identifier for this case.
        category: Free-text grouping label (e.g. `"customer_behavior"`) —
            informational, not validated against a fixed taxonomy beyond
            being non-empty.
        question: The exact text sent to the agent as a fresh turn.
        expects_tool: `"run_query"`, `"get_schema"`, or `None` if the case
            expects the model to answer without calling either.
        expected_tables: Loose OR-match against the SQL text of any
            `run_query` call made during the turn — a shape check, not
            exact-SQL matching (the model's SQL isn't deterministic).
        expected_keywords: Loose, case-insensitive OR-match against the
            final answer text — informational signal, not a hard gate.
        pii_sensitive: Whether `_check_pii_leak` should run its regex
            email-pattern check on this case's answer.
        notes: Human-authored rationale; not read by the harness.
    """

    id: str
    category: str
    question: str
    expects_tool: str | None
    expected_tables: list[str]
    expected_keywords: list[str]
    pii_sensitive: bool
    notes: str = ""


def load_golden_set(path: str) -> list[GoldenCase]:
    """Load and validate the golden set from a JSON file.

    Args:
        path: Path to a JSON file shaped like `eval/golden_set.json`.

    Returns:
        The parsed cases, in file order.

    Raises:
        ValueError: The file isn't a JSON list, an entry is missing a
            required field, `expects_tool` isn't one of the allowed values,
            or two entries share an `id`.
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: expected a non-empty JSON list of cases")

    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(raw):
        for required in ("id", "category", "question", "expects_tool", "expected_tables", "expected_keywords"):
            if required not in entry:
                raise ValueError(f"{path}: entry {i} is missing required field '{required}'")
        if entry["expects_tool"] not in _ALLOWED_EXPECTS_TOOL:
            raise ValueError(
                f"{path}: entry {i} ('{entry['id']}') has invalid expects_tool "
                f"{entry['expects_tool']!r} — must be 'run_query', 'get_schema', or null"
            )
        if not entry["question"].strip():
            raise ValueError(f"{path}: entry {i} ('{entry['id']}') has an empty question")
        if entry["id"] in seen_ids:
            raise ValueError(f"{path}: duplicate case id '{entry['id']}'")
        seen_ids.add(entry["id"])
        cases.append(
            GoldenCase(
                id=entry["id"],
                category=entry["category"],
                question=entry["question"],
                expects_tool=entry["expects_tool"],
                expected_tables=entry["expected_tables"],
                expected_keywords=entry["expected_keywords"],
                pii_sensitive=entry.get("pii_sensitive", False),
                notes=entry.get("notes", ""),
            )
        )
    return cases


@dataclass
class CaseResult:
    """What actually happened when a `GoldenCase` was run through the agent.

    Attributes:
        answer_text: The turn's final answer text.
        tools_called: Names of every tool the model called this turn, in
            call order (empty if it answered without calling one).
        sql_queries: The `sql` argument of every `run_query` call this turn.
        tables_touched: Lowercased table names found (via substring match)
            in the concatenated text of `sql_queries`.
        row_counts: `row_count` from every successful `run_query` result.
        self_correct_attempts: Final `self_correct_attempts` graph state
            value for the turn (0 if no self-correct happened).
        had_error: Whether any tool call in the turn returned an error
            payload (self-corrected or not).
    """

    answer_text: str
    tools_called: list[str] = field(default_factory=list)
    sql_queries: list[str] = field(default_factory=list)
    tables_touched: set[str] = field(default_factory=set)
    row_counts: list[int] = field(default_factory=list)
    self_correct_attempts: int = 0
    had_error: bool = False


_KNOWN_TABLES = ("orders", "order_items", "products", "users")


def _extract_case_result(messages: list[BaseMessage], self_correct_attempts: int = 0) -> CaseResult:
    """Walk a finished turn's message list into a `CaseResult`.

    Pure and graph-independent — consumes the same `AIMessage.tool_calls`/
    `ToolMessage` shapes `graph.py`/`cli.py` already produce, so it works
    identically on a live `graph.invoke(...)` result or a hand-built list of
    messages in a test.

    Args:
        messages: The full message list for one turn (as returned in
            `graph.invoke(...)`'s `"messages"` key), in order.
        self_correct_attempts: The turn's final `self_correct_attempts`
            graph state value, if available (not derivable from `messages`
            alone).

    Returns:
        A populated `CaseResult`. `answer_text` is the last `AIMessage`'s
        text; if no `AIMessage` is present (shouldn't happen for a finished
        turn), it's the empty string.
    """
    tools_called: list[str] = []
    sql_queries: list[str] = []
    row_counts: list[int] = []
    had_error = False
    last_ai_message: AIMessage | None = None

    for message in messages:
        if isinstance(message, AIMessage):
            last_ai_message = message
            for call in message.tool_calls:
                tools_called.append(call["name"])
                if call["name"] == "run_query":
                    sql = (call["args"] or {}).get("sql", "")
                    if sql:
                        sql_queries.append(sql)
        elif isinstance(message, ToolMessage):
            try:
                payload = json.loads(message.content)
            except (json.JSONDecodeError, TypeError):
                continue
            if "error" in payload:
                had_error = True
            elif "row_count" in payload:
                row_counts.append(payload["row_count"])

    sql_text_lower = " ".join(sql_queries).lower()
    tables_touched = {table for table in _KNOWN_TABLES if table in sql_text_lower}

    answer_text = _response_text(last_ai_message) if last_ai_message is not None else ""

    return CaseResult(
        answer_text=answer_text,
        tools_called=tools_called,
        sql_queries=sql_queries,
        tables_touched=tables_touched,
        row_counts=row_counts,
        self_correct_attempts=self_correct_attempts,
        had_error=had_error,
    )


def run_case(graph, case: GoldenCase) -> CaseResult:
    """Run one golden case through the real compiled graph.

    Args:
        graph: A compiled graph as returned by `graph.build_graph(...)`
            (via `cli._build_graph`).
        case: The case to run.

    Returns:
        The extracted `CaseResult`.
    """
    thread_config = {"configurable": {"thread_id": f"eval-{case.id}"}}
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=case.question)],
            "self_correct_attempts": 0,
            "empty_result_sanity_checked": False,
            "last_tool_errors": [],
            "blocked": False,
        },
        config=thread_config,
    )
    return _extract_case_result(result["messages"], result.get("self_correct_attempts", 0))


@dataclass
class CheckOutcome:
    """One deterministic check's result.

    Attributes:
        name: The check's short name (e.g. `"expected_tool"`).
        passed: Whether the check passed. `True` when the check doesn't
            apply to this case (e.g. no `expected_tables` configured).
        detail: A short human-readable explanation, mainly useful on a
            failure.
    """

    name: str
    passed: bool
    detail: str


def _check_expected_tool(case: GoldenCase, result: CaseResult) -> CheckOutcome:
    """Whether the expected tool (or no tool) was called this turn.

    Args:
        case: The case, for `expected_tool`.
        result: The run's result, for `tools_called`.

    Returns:
        A `CheckOutcome` named `"expected_tool"`.
    """
    if case.expects_tool is None:
        passed = not result.tools_called
        detail = "no tool call expected" if passed else f"expected no tool call, got {result.tools_called}"
    else:
        passed = case.expects_tool in result.tools_called
        detail = (
            f"called {case.expects_tool}"
            if passed
            else f"expected {case.expects_tool}, got {result.tools_called or 'no tool call'}"
        )
    return CheckOutcome("expected_tool", passed, detail)


def _check_expected_tables(case: GoldenCase, result: CaseResult) -> CheckOutcome:
    """Whether any SQL run this turn touched an expected table.

    Passes trivially (no-op) if the case has no `expected_tables`.

    Args:
        case: The case, for `expected_tables`.
        result: The run's result, for `tables_touched`.

    Returns:
        A `CheckOutcome` named `"expected_tables"`.
    """
    if not case.expected_tables:
        return CheckOutcome("expected_tables", True, "no expected tables configured")
    expected_lower = {t.lower() for t in case.expected_tables}
    matched = expected_lower & result.tables_touched
    passed = bool(matched)
    detail = f"matched {sorted(matched)}" if passed else f"expected one of {sorted(expected_lower)}, touched {sorted(result.tables_touched)}"
    return CheckOutcome("expected_tables", passed, detail)


def _check_expected_keywords(case: GoldenCase, result: CaseResult) -> CheckOutcome:
    """Whether the final answer mentions at least one expected keyword.

    Passes trivially (no-op) if the case has no `expected_keywords`.

    Args:
        case: The case, for `expected_keywords`.
        result: The run's result, for `answer_text`.

    Returns:
        A `CheckOutcome` named `"expected_keywords"`.
    """
    if not case.expected_keywords:
        return CheckOutcome("expected_keywords", True, "no expected keywords configured")
    answer_lower = result.answer_text.lower()
    matched = [kw for kw in case.expected_keywords if kw.lower() in answer_lower]
    passed = bool(matched)
    detail = f"found {matched}" if passed else f"none of {case.expected_keywords} found in answer"
    return CheckOutcome("expected_keywords", passed, detail)


def _check_pii_leak(case: GoldenCase, result: CaseResult) -> CheckOutcome:
    """Regex email-pattern check on the final answer text.

    Belt-and-suspenders on top of the BQ wrapper's column stripping
    (`pii.strip_pii_columns`) — this only catches an email-shaped string
    literally present in the answer text, not every PII leak vector. Only
    runs when `case.pii_sensitive` is set; otherwise passes trivially.

    Args:
        case: The case, for `pii_sensitive`.
        result: The run's result, for `answer_text`.

    Returns:
        A `CheckOutcome` named `"pii_leak"`.
    """
    if not case.pii_sensitive:
        return CheckOutcome("pii_leak", True, "not a PII-sensitive case")
    match = _EMAIL_PATTERN.search(result.answer_text)
    passed = match is None
    detail = "no email-shaped text found" if passed else f"email-shaped text found: {match.group(0)!r}"
    return CheckOutcome("pii_leak", passed, detail)


@dataclass
class JudgeVerdict:
    """The LLM-as-judge's scoring of one case's answer.

    Attributes:
        answers_question: Whether the answer addresses what was asked (or
            declines appropriately, for a case that should be declined).
        plausible: Whether the answer's numbers/claims are internally
            coherent (not verified against ground truth).
        no_pii: Whether the judge found no PII-shaped content in the answer.
        reasoning: The judge's own one-or-two-sentence explanation.
        parse_error: `True` if the judge's response wasn't valid JSON in the
            expected shape — the other fields are then meaningless
            placeholders (`False`), and the case is reported as
            inconclusive, not silently passed.
    """

    answers_question: bool
    plausible: bool
    no_pii: bool
    reasoning: str
    parse_error: bool = False

    @property
    def passed(self) -> bool:
        """Whether every dimension the judge scored came back true.

        Returns:
            `False` unconditionally if `parse_error` is set.
        """
        return not self.parse_error and self.answers_question and self.plausible and self.no_pii


def _build_judge_messages(case: GoldenCase, result: CaseResult) -> list[BaseMessage]:
    """Build the message list sent to the judge model for one case.

    Args:
        case: The case being judged, for its question.
        result: The run's result, for the answer text to judge.

    Returns:
        `[SystemMessage(JUDGE_SYSTEM_PROMPT), HumanMessage(...)]`.
    """
    human = (
        f"Question asked: {case.question}\n\n"
        f"Agent's answer: {result.answer_text or '(empty answer)'}"
    )
    return [SystemMessage(content=JUDGE_SYSTEM_PROMPT), HumanMessage(content=human)]


def judge_case(judge_model, case: GoldenCase, result: CaseResult) -> JudgeVerdict:
    """Score one case's answer with the LLM-as-judge.

    Args:
        judge_model: Anything with `.invoke(messages) -> AIMessage`-shaped
            response (a bare `ChatGoogleGenerativeAI` in production — never
            `GeminiProvider`, which binds the agent's own tools; a fake with
            the same duck-typed `.invoke` in tests).
        case: The case being judged.
        result: The run's result to judge.

    Returns:
        A `JudgeVerdict`. A response that isn't valid JSON in the expected
        shape sets `parse_error=True` rather than raising — one bad judge
        response degrades that case's verdict instead of crashing the run.
    """
    messages = _build_judge_messages(case, result)
    response = judge_model.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)

    try:
        # Judge models sometimes wrap JSON in a code fence despite instructions not to.
        text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)
        return JudgeVerdict(
            answers_question=bool(data["answers_question"]),
            plausible=bool(data["plausible"]),
            no_pii=bool(data["no_pii"]),
            reasoning=str(data.get("reasoning", "")),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return JudgeVerdict(
            answers_question=False,
            plausible=False,
            no_pii=False,
            reasoning=f"Judge response was not valid JSON in the expected shape: {content!r}",
            parse_error=True,
        )


@dataclass
class CaseReport:
    """The full outcome for one golden case, ready to print or serialize.

    Attributes:
        case: The case that was run.
        result: What the agent actually did. `None` if the case never
            finished running — see `run_error`.
        checks: The deterministic check outcomes. Empty if `run_error` is set.
        verdict: The judge's verdict. A `parse_error=True` placeholder if
            `run_error` is set — the judge is never called for a case that
            didn't produce an answer to judge.
        passed: `True` if every check passed and the judge's verdict passed.
            Always `False` if `run_error` is set.
        run_error: Set when `run_case` itself raised (e.g. a
            `ProviderTransientError` surviving Gemini's own retries, or any
            other live-call failure) — the case is reported as failed rather
            than aborting the whole run. `None` for a case that ran to
            completion, whether it passed or failed on its checks/verdict.
    """

    case: GoldenCase
    result: CaseResult | None
    checks: list[CheckOutcome]
    verdict: JudgeVerdict
    passed: bool
    run_error: str | None = None


@dataclass
class EvalReport:
    """The full run's report.

    Attributes:
        case_reports: One `CaseReport` per golden case, in golden-set order.
        pass_rate: `passed` count divided by total case count (0.0-1.0).
    """

    case_reports: list[CaseReport]
    pass_rate: float


def _run_deterministic_checks(case: GoldenCase, result: CaseResult) -> list[CheckOutcome]:
    """Run every deterministic check for one case.

    Args:
        case: The case being checked.
        result: The run's result.

    Returns:
        One `CheckOutcome` per check, in a fixed order.
    """
    return [
        _check_expected_tool(case, result),
        _check_expected_tables(case, result),
        _check_expected_keywords(case, result),
        _check_pii_leak(case, result),
    ]


def _score_case(graph, judge_model, case: GoldenCase) -> CaseReport:
    """Run one case and score it, containing any failure to this one case.

    A live run through the real graph can fail for reasons that have
    nothing to do with the case itself — most commonly a
    `ProviderTransientError` surviving Gemini's own `bounded_backoff`
    retries (e.g. a free-tier rate limit). Catching that here means one
    case's live failure is reported as a failed case, not a crashed harness
    run that loses every case's results after it — the same
    "no path crashes the [caller]" property `cli.py`'s per-turn
    `except AgentError`/`except Exception` already gives the interactive
    REPL loop.

    Args:
        graph: A compiled graph, as `run_case` expects.
        judge_model: A tool-unbound chat model, as `judge_case` expects.
        case: The case to run and score.

    Returns:
        A `CaseReport`. `run_error` is set (and `passed` is `False`) if
        `run_case` itself raised; otherwise the normal
        deterministic-checks-then-judge scoring applies.
    """
    try:
        result = run_case(graph, case)
    except Exception as exc:
        placeholder_verdict = JudgeVerdict(
            answers_question=False,
            plausible=False,
            no_pii=False,
            reasoning="Case did not run to completion — see run_error.",
            parse_error=True,
        )
        return CaseReport(
            case=case,
            result=None,
            checks=[],
            verdict=placeholder_verdict,
            passed=False,
            run_error=f"{type(exc).__name__}: {exc}",
        )

    checks = _run_deterministic_checks(case, result)
    verdict = judge_case(judge_model, case, result)
    passed = all(c.passed for c in checks) and verdict.passed
    return CaseReport(case=case, result=result, checks=checks, verdict=verdict, passed=passed)


def run_eval(config, golden_set_path: str) -> EvalReport:
    """Run the full golden set through the real agent and score every case.

    Builds one real graph (live Gemini + live BigQuery + the Saved
    Reports/Conversation Postgres stores, via `cli._build_graph`) and a
    single tool-unbound judge model, then runs and scores every case in
    turn via `_score_case` — one case's live failure never aborts the rest
    of the run.

    Args:
        config: A loaded `Config` (see `config.load_config`).
        golden_set_path: Path to the golden set JSON file.

    Returns:
        The full `EvalReport`.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    cases = load_golden_set(golden_set_path)
    graph, _conversation_store, _owner = _build_graph(config)
    judge_model = ChatGoogleGenerativeAI(model=config.gemini_model, api_key=config.gemini_api_key, max_retries=1)

    case_reports = [_score_case(graph, judge_model, case) for case in cases]

    pass_rate = sum(1 for r in case_reports if r.passed) / len(case_reports) if case_reports else 0.0
    return EvalReport(case_reports=case_reports, pass_rate=pass_rate)
