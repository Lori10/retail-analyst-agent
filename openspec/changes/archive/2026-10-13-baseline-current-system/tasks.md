# Tasks

Baseline change: records work already completed before OpenSpec was adopted
(see CLAUDE.md build order). Every task is done.

## 1. Thin end-to-end skeleton

- [x] 1.1 CLI → LangGraph tool-calling loop → Gemini
- [x] 1.2 BigQuery wrapper with read-only check, dry-run cap, timeout, row limit and PII stripping

## 2. Resilience

- [x] 2.1 Typed errors with self-correctable flag and user-facing messages
- [x] 2.2 Bounded self-correct loop and give-up node
- [x] 2.3 Backoff on transient BigQuery and Gemini failures

## 3. High-Stakes Oversight

- [x] 3.1 Postgres-backed Saved Reports Store
- [x] 3.2 interrupt()-based confirm-then-delete flow

## 4. Observability

- [x] 4.1 Structured JSON trace events
- [x] 4.2 LangSmith auto-instrumentation via environment variables

## 5. Polish

- [x] 5.1 Docs and clean-machine setup test
- [x] 5.2 Conversation Store and resume on startup

## 6. Quality Assurance

- [x] 6.1 Golden set, deterministic checks, LLM judge, manual eval script

## 7. MCP server

- [x] 7.1 Stdio MCP server with server-side guarantees and two-step token delete

## 8. MCP client

- [x] 8.1 External MCP tools loaded into the agent, namespaced and filtered
