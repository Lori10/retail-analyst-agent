import json
import logging
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from retail_agent.bq_tool import BigQueryTool
from retail_agent.errors import AgentError, GuardrailBlockedError, graceful_message_for
from retail_agent.guardrail import check_user_input
from retail_agent.llm_provider import GeminiProvider
from retail_agent.reports_store import ReportsStore

logger = logging.getLogger(__name__)

MAX_SELF_CORRECT_ATTEMPTS = 2

_AFFIRMATIVE_REPLIES = {"y", "yes", "yeah", "yep", "confirm", "confirmed", "sure", "go ahead", "do it"}


def _is_affirmative(text: str) -> bool:
    """Whether a delete-confirmation reply counts as "yes".

    A small deterministic keyword check, not an LLM call — same philosophy
    as `guardrail.py`'s denylist and `sql_safety.py`'s keyword allowlist:
    cheap, zero added latency/cost, and this only needs to catch the
    ordinary "yes"/"no" shape of a reply to a plain confirmation prompt, not
    interpret arbitrary phrasing. Anything that doesn't match — including a
    brand-new unrelated question typed instead of a yes/no — aborts the
    delete rather than proceeding; see docs/implementation-notes.md for the
    known UX gap this leaves (the unrelated question itself is swallowed,
    not answered).

    Args:
        text: The raw text of the user's next message after a delete
            confirmation prompt (the `interrupt()` resume value).

    Returns:
        Whether `text` should be treated as an affirmative confirmation.
    """
    return text.strip().lower() in _AFFIRMATIVE_REPLIES


class AgentState(TypedDict):
    """LangGraph state threaded through every node.

    Attributes:
        messages: Conversation history; accumulates via `operator.add` as
            each node appends its output.
        self_correct_attempts: Count of self-correctable tool errors seen
            so far this turn, reset to 0 by the caller at the start of each
            `graph.invoke()` call.
        empty_result_sanity_checked: Whether a zero-row `run_query` result
            has already had a sanity-check note injected this turn.
        last_tool_errors: Errors from the most recent `call_tools` pass
            (replaced, not accumulated, each time it runs).
        blocked: Whether `guardrail_check` rejected this turn's user
            message; set fresh every turn before anything else reads it.
    """

    messages: Annotated[list[BaseMessage], operator.add]
    self_correct_attempts: int
    empty_result_sanity_checked: bool
    last_tool_errors: list[dict]
    blocked: bool


def _dataframe_to_records(df) -> list[dict]:
    """Convert a query result DataFrame into JSON-safe records.

    Routes through JSON (not `.to_dict()`) so numpy/Timestamp values become
    plain JSON-safe types before they're embedded in a `ToolMessage`.

    Args:
        df: The (already PII-stripped) query result DataFrame.

    Returns:
        A list of plain dicts, one per row.
    """
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _run_tool(
    bq_tool: BigQueryTool, reports_store: ReportsStore, owner: str, conversation_id: str, name: str, args: dict
) -> dict:
    """Dispatch one model-requested tool call to `BigQueryTool` or `ReportsStore`.

    `delete_reports` is deliberately not handled here — it's unreachable by
    construction, since `route_after_model` diverts any round containing it
    to the `resolve_delete` node before this function is ever called.

    Args:
        bq_tool: The BigQuery wrapper to call for `run_query`/`get_schema`.
        reports_store: The reports store to call for `save_report`/`list_reports`.
        owner: The current user's identity, scoping all reports store calls.
        conversation_id: The LangGraph thread id, recorded on saved reports.
        name: Tool name.
        args: The model-supplied arguments for that tool.

    Returns:
        A JSON-safe payload suitable for a `ToolMessage`.

    Raises:
        AgentError: Propagated unchanged from the underlying wrapper call —
            `call_tools` is what actually catches this.
    """
    if name == "run_query":
        result = bq_tool.run_query(args["sql"])
        return {
            "row_count": result["row_count"],
            "redacted_columns": result["redacted_columns"],
            "rows": _dataframe_to_records(result["dataframe"]),
        }
    if name == "get_schema":
        return {"columns": bq_tool.get_schema(args["table_name"])}
    if name == "save_report":
        report = reports_store.save_report(
            owner=owner,
            title=args["title"],
            content=args["content"],
            conversation_id=conversation_id,
            tags=args.get("tags"),
        )
        return {"saved": True, "id": report["id"], "title": report["title"]}
    if name == "list_reports":
        return {"reports": reports_store.list_reports(owner)}
    return {"error": f"Unknown tool: {name}"}


def build_graph(
    provider: GeminiProvider,
    bq_tool: BigQueryTool,
    reports_store: ReportsStore,
    owner: str,
    system_instruction: str,
):
    """Compile the agent's LangGraph tool-calling loop.

    Args:
        provider: The LLM provider used by the `call_model` node.
        bq_tool: The BigQuery wrapper used by the `tools` node.
        reports_store: The Saved Reports store used by the `tools` and
            `resolve_delete` nodes.
        owner: The current user's identity, scoping every reports store
            call (never cross-user).
        system_instruction: The system prompt sent on every `call_model`
            invocation.

    Returns:
        A compiled `StateGraph` with in-memory checkpointing, ready for
        `.invoke(...)`.
    """

    def guardrail_check(state: AgentState) -> dict:
        """Check the turn's user message against the input-side guardrail
        before any model or tool call happens.

        Runs once per turn on the newest message only — the graph state
        accumulates the whole conversation, but only the message that
        started this turn needs checking.

        Args:
            state: Current graph state; reads the last message's text.

        Returns:
            `{"blocked": True/False}` — `route_after_guardrail` decides
            where to go next; a pass-through turn appends no message here,
            so it looks exactly as it did before this node existed.
        """
        text = state["messages"][-1].content
        try:
            check_user_input(text)
        except GuardrailBlockedError as exc:
            logger.warning("guardrail_blocked", extra={"reason": str(exc)})
            return {"blocked": True}
        return {"blocked": False}

    def blocked(state: AgentState) -> dict:
        """Terminal node reached when `guardrail_check` rejects the turn.

        Returns:
            `{"messages": [...]}` — a single `AIMessage` carrying the
            guardrail's graceful decline, without the model or any tool
            ever being called for this turn.
        """
        return {"messages": [AIMessage(content=GuardrailBlockedError.graceful_message)]}

    def call_model(state: AgentState) -> dict:
        """Send the message history to the provider and append its reply.

        Args:
            state: Current graph state; only `messages` is read.

        Returns:
            `{"messages": [...]}` — the model's reply, appended via the
            state's `operator.add` reducer.

        Raises:
            ProviderError: Propagated unchanged from `provider.generate`;
                not caught here — see `cli.py`'s `except AgentError` for
                where it's ultimately handled.
        """
        response = provider.generate(messages=state["messages"], system_instruction=system_instruction)
        return {"messages": [response]}

    def call_tools(state: AgentState, config: RunnableConfig) -> dict:
        """Execute every tool call in the latest model message.

        Dispatches each to `_run_tool`, turns the result (or a caught
        `AgentError`) into a `ToolMessage`, injects a one-time sanity-check
        note on the first zero-row `run_query` result, and tracks
        `self_correct_attempts`/`last_tool_errors` for `route_after_tools`
        to act on.

        Args:
            state: Current graph state; reads `messages` (for the model's
                tool calls) and the prior `self_correct_attempts`/
                `empty_result_sanity_checked` to continue counting within
                the same turn.
            config: LangGraph-injected run config; `configurable.thread_id`
                is passed to `_run_tool` as the `conversation_id` recorded
                on any report saved this turn.

        Returns:
            A dict updating `messages` (one `ToolMessage` per tool call),
            plus the (possibly incremented) `self_correct_attempts`,
            `empty_result_sanity_checked`, and this pass's `last_tool_errors`.
        """
        last: AIMessage = state["messages"][-1]
        thread_id = config["configurable"]["thread_id"]
        tool_messages: list[ToolMessage] = []
        self_correct_attempts = state.get("self_correct_attempts", 0)
        empty_result_checked = state.get("empty_result_sanity_checked", False)
        errors: list[dict] = []

        for call in last.tool_calls:
            try:
                payload = _run_tool(bq_tool, reports_store, owner, thread_id, call["name"], call["args"] or {})
            except AgentError as exc:
                payload = {"error": str(exc), "error_class": type(exc).__name__}
                errors.append(
                    {
                        "error_class": type(exc).__name__,
                        "message": str(exc),
                        "self_correctable": exc.self_correctable,
                    }
                )
                logger.warning("tool_call_error", extra={"tool": call["name"], "error_class": type(exc).__name__})
                if exc.self_correctable:
                    self_correct_attempts += 1
            else:
                if call["name"] == "run_query" and payload.get("row_count") == 0 and not empty_result_checked:
                    payload = {
                        **payload,
                        "note": (
                            "Zero rows returned — double-check filters/joins/date range "
                            "before treating this as the final answer."
                        ),
                    }
                    empty_result_checked = True

            tool_messages.append(
                ToolMessage(content=json.dumps(payload), tool_call_id=call["id"], name=call["name"])
            )

        return {
            "messages": tool_messages,
            "self_correct_attempts": self_correct_attempts,
            "empty_result_sanity_checked": empty_result_checked,
            "last_tool_errors": errors,
        }

    def give_up(state: AgentState) -> dict:
        """Terminal node: emit a graceful message for the turn's failure.

        Picks the first non-self-correctable error if any (it's the one
        that actually explains why the turn is ending), otherwise the
        first error recorded.

        Args:
            state: Current graph state; reads `last_tool_errors` (always
                non-empty when this node runs, per `route_after_tools`).

        Returns:
            `{"messages": [...]}` — a single `AIMessage` carrying the
            chosen error's graceful message.
        """
        errors = state["last_tool_errors"]
        chosen = next((e for e in errors if not e["self_correctable"]), errors[0])
        message = graceful_message_for(chosen["error_class"])
        logger.warning("agent_terminal_error", extra={"error_class": chosen["error_class"]})
        return {"messages": [AIMessage(content=message)]}

    def resolve_delete(state: AgentState, config: RunnableConfig) -> dict:
        """Resolve a `delete_reports` call into exact candidates, pause for
        the user's explicit confirmation, then execute or abort.

        Reached instead of `tools` for any round containing a
        `delete_reports` call (see `route_after_model`) — deletion is
        destructive, so it gets its own confirm-then-execute node rather
        than running through the ordinary tool dispatch path.

        Everything before the `interrupt()` call re-runs from scratch on
        resume (LangGraph replay semantics) — this function's only `return`
        is reached exclusively on the pass that completes (zero candidates,
        a store error, or post-resume), never on the pass that pauses, so no
        message is ever double-appended, and `candidates` is always freshly
        re-queried, never a stale pre-pause snapshot.

        Args:
            state: Current graph state; reads the last message's
                `delete_reports` tool call.
            config: LangGraph-injected run config; `configurable.thread_id`
                scopes candidate resolution when `scope == "conversation"`.

        Returns:
            `{"messages": [tool_message, ai_message]}` — a `ToolMessage`
            answering the `delete_reports` call (so the model always has a
            function response, per Gemini's function-calling protocol) plus
            a terminal `AIMessage` for the user.
        """
        last: AIMessage = state["messages"][-1]
        call = next(c for c in last.tool_calls if c["name"] == "delete_reports")
        args = call["args"] or {}
        thread_id = config["configurable"]["thread_id"]

        try:
            candidates = reports_store.find_candidates(
                owner,
                scope=args.get("scope", "conversation"),
                conversation_id=thread_id,
                title_contains=args.get("title_contains"),
            )
        except AgentError as exc:
            logger.warning("reports_store_error", extra={"error_class": type(exc).__name__})
            tool_message = ToolMessage(
                content=json.dumps({"error": str(exc), "error_class": type(exc).__name__}),
                tool_call_id=call["id"],
                name="delete_reports",
            )
            message = AIMessage(content=graceful_message_for(type(exc).__name__))
            return {"messages": [tool_message, message]}

        if not candidates:
            tool_message = ToolMessage(
                content=json.dumps({"candidates": [], "deleted": 0}),
                tool_call_id=call["id"],
                name="delete_reports",
            )
            message = AIMessage(
                content="I couldn't find any saved reports matching that — nothing was deleted."
            )
            return {"messages": [tool_message, message]}

        decision = interrupt({"type": "confirm_delete", "candidates": candidates})

        if _is_affirmative(decision):
            ids = [c["id"] for c in candidates]
            deleted = reports_store.delete_reports(owner, ids)
            logger.info("reports_deleted", extra={"owner": owner, "count": deleted, "ids": ids})
            message = AIMessage(content=f"Deleted {deleted} report(s).")
            payload = {"candidates": candidates, "deleted": deleted}
        else:
            logger.info("reports_delete_aborted", extra={"owner": owner, "candidate_count": len(candidates)})
            message = AIMessage(content="Okay, I didn't delete anything.")
            payload = {"candidates": candidates, "deleted": 0}

        tool_message = ToolMessage(content=json.dumps(payload), tool_call_id=call["id"], name="delete_reports")
        return {"messages": [tool_message, message]}

    def route_after_guardrail(state: AgentState) -> str:
        """Route to `blocked` if `guardrail_check` rejected this turn, else
        into the normal `call_model` loop.

        Args:
            state: Current graph state; reads `blocked`.

        Returns:
            `"blocked"` or `"call_model"`.
        """
        return "blocked" if state["blocked"] else "call_model"

    def route_after_model(state: AgentState) -> str:
        """Route to `resolve_delete` if the model called `delete_reports`,
        to `tools` if it made any other tool call, else end the turn.

        A round mixing `delete_reports` with another tool call routes
        entirely to `resolve_delete`, dropping the other call for this
        round — same precedent and justification as the existing
        non-self-correctable-error "mixed round" behavior in
        `route_after_tools`/`call_tools`: `call_model` regenerates the
        whole call set fresh next turn if the other call is still needed.

        Args:
            state: Current graph state; only `messages` is read.

        Returns:
            `"resolve_delete"`, `"tools"`, or `END`.
        """
        last: AIMessage = state["messages"][-1]
        if any(c["name"] == "delete_reports" for c in last.tool_calls):
            return "resolve_delete"
        if last.tool_calls:
            return "tools"
        return END

    def route_after_tools(state: AgentState) -> str:
        """Route back to `call_model` to retry, or to `give_up` if this
        turn's errors aren't self-correctable or the retry budget
        (`MAX_SELF_CORRECT_ATTEMPTS`) is exhausted.

        Args:
            state: Current graph state; reads `last_tool_errors` and
                `self_correct_attempts`.

        Returns:
            `"call_model"` or `"give_up"`.
        """
        errors = state["last_tool_errors"]
        if not errors:
            return "call_model"
        if any(not e["self_correctable"] for e in errors):
            return "give_up"
        if state["self_correct_attempts"] > MAX_SELF_CORRECT_ATTEMPTS:
            return "give_up"
        return "call_model"

    graph = StateGraph(AgentState)
    graph.add_node("guardrail", guardrail_check)
    graph.add_node("blocked", blocked)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_node("give_up", give_up)
    graph.add_node("resolve_delete", resolve_delete)
    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", route_after_guardrail, {"call_model": "call_model", "blocked": "blocked"})
    graph.add_edge("blocked", END)
    graph.add_conditional_edges(
        "call_model", route_after_model, {"tools": "tools", "resolve_delete": "resolve_delete", END: END}
    )
    graph.add_conditional_edges("tools", route_after_tools, {"call_model": "call_model", "give_up": "give_up"})
    graph.add_edge("give_up", END)
    graph.add_edge("resolve_delete", END)

    return graph.compile(checkpointer=InMemorySaver())
