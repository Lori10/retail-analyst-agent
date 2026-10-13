# error-resilience Specification

## Purpose
Ensures every failure — bad SQL, permissions, transient outages, empty results, provider or store errors — ends a turn in either a valid answer or a bounded, plain-language message, and never crashes the CLI.

## Requirements

### Requirement: Failures are classified as self-correctable or not
Every handled failure SHALL carry a type that states whether a model-written retry could fix it and a fixed user-facing message. Self-correctable: SQL safety rejection, over-cost query, query syntax error, external-tool error. Not self-correctable: permission, transient/timeout after backoff, provider, store, external-tool-unavailable, guardrail block.

#### Scenario: Unknown error type
- **WHEN** a failure type has no registered user-facing message
- **THEN** the generic agent error message is used

### Requirement: Bounded self-correction
On a self-correctable tool error, the system SHALL return the error to the model as the tool result so it can retry with different input, and SHALL allow at most 2 such retries per turn. A third consecutive failure SHALL end the turn with the last error's user-facing message.

#### Scenario: Recovers within budget
- **WHEN** the first query has a syntax error and the rewritten query succeeds
- **THEN** the turn ends with an answer

#### Scenario: Budget exhausted
- **WHEN** three attempts in one turn fail with self-correctable errors
- **THEN** the turn ends with the user-facing message of the last error and no further model call is made

#### Scenario: Retries count across error types
- **WHEN** consecutive attempts fail with different self-correctable error types
- **THEN** they share the same budget of 2 retries

### Requirement: Non-self-correctable errors end the turn immediately
A non-self-correctable tool error SHALL end the turn on its first occurrence with its user-facing message. If a round mixes one with other results, the whole round SHALL give up.

#### Scenario: Permission error
- **WHEN** BigQuery returns a permission error
- **THEN** the agent replies "I don't have permission to access the data needed for that request." without retrying

#### Scenario: Mixed round
- **WHEN** one tool call in a round succeeds and another fails non-self-correctably
- **THEN** the turn gives up

### Requirement: Backoff on transient failures
Transient BigQuery and Gemini failures (server errors, rate limits, timeouts, connection errors) SHALL be retried automatically with exponential backoff, 2 attempts in total, before being reported. Non-transient failures SHALL NOT be retried.

#### Scenario: Transient error recovers
- **WHEN** the first BigQuery execution fails transiently and the second succeeds
- **THEN** the result is returned and no failure is logged

#### Scenario: Transient error persists
- **WHEN** both attempts fail transiently
- **THEN** a non-self-correctable transient error is reported and the failure is logged exactly once

#### Scenario: Syntax error is not retried
- **WHEN** BigQuery rejects a query as a bad request
- **THEN** it is reported immediately as a self-correctable syntax error

### Requirement: Empty-result sanity check
The first `run_query` result with zero rows in a turn SHALL be returned to the model with a note asking it to double-check filters, joins and date range. Later zero-row results in the same turn SHALL carry no note.

#### Scenario: First empty result
- **WHEN** a query returns 0 rows for the first time in a turn
- **THEN** the tool result includes the sanity-check note

#### Scenario: Second empty result
- **WHEN** a second query in the same turn returns 0 rows
- **THEN** the result has no note and the model may accept zero as the answer

### Requirement: The CLI never crashes on a turn failure
Any error raised during a turn SHALL be reported to the user and the REPL SHALL continue to the next prompt.

#### Scenario: Provider failure
- **WHEN** Gemini fails after backoff
- **THEN** an error message is printed and the next prompt is shown
