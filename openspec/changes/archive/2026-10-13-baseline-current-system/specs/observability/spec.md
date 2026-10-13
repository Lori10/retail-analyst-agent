# Spec Delta

## Purpose

Makes every model call, tool call and turn reconstructable from structured JSON logs in a shape Cloud Logging parses natively, with optional LangSmith tracing, all on post-PII-strip data.

## ADDED Requirements

### Requirement: One JSON line per event
The system SHALL emit exactly one JSON line per `llm_call`, per `tool_call`, and per `turn`, with `message` set to the event name and `severity` set to `ERROR` when the event has an `error_class`, else `INFO`.

#### Scenario: Successful turn with one query
- **WHEN** a turn makes one tool call and two model calls
- **THEN** two `llm_call`, one `tool_call` and one `turn` line are emitted

#### Scenario: Error severity
- **WHEN** an event carries a non-empty `error_class`
- **THEN** its `severity` is `ERROR`

#### Scenario: Non-serializable field
- **WHEN** an event field is not JSON-serializable
- **THEN** it is written as its string form instead of failing

### Requirement: Event fields
Every event SHALL carry `conversation_id` and `turn_id`. `llm_call` SHALL carry `latency_ms`, `tokens`, `error_class`. `tool_call` SHALL carry `tool`, `sql` (for queries), `rows_returned`, `latency_ms`, `error_class`, `self_correct_attempt`. `turn` SHALL carry `intent`, `outcome` (`answered`, `blocked`, `give_up`, `delete_pending`, `agent_error`, `exception`), `latency_ms`, `error_class`, `self_correct_attempt`.

#### Scenario: Failed tool call
- **WHEN** a query fails with a syntax error on the first self-correct attempt
- **THEN** its `tool_call` line records the error class and the attempt number

#### Scenario: Blocked turn
- **WHEN** the guardrail blocks a message
- **THEN** the `turn` line has `outcome` `blocked`

### Requirement: Configurable destination
Trace lines SHALL go to stderr by default, to stdout when `TRACE_LOG_DESTINATION` is `stdout` or `-`, and otherwise be appended to the named file. Reconfiguring SHALL NOT duplicate output.

#### Scenario: File destination
- **WHEN** `TRACE_LOG_DESTINATION` is a file path
- **THEN** events are appended to that file as JSON lines

### Requirement: Optional LangSmith tracing
LangSmith tracing SHALL be enabled only by the `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` environment variables, and an invalid or missing key SHALL NOT break a turn.

#### Scenario: Tracing unset
- **WHEN** the LangSmith variables are not set
- **THEN** the agent runs normally with no tracing sent
