# Design

## Context

All decisions below were made in the baseline (`openspec/changes/archive/2026-10-13-baseline-current-system/design.md`); this change exists to track building them.

## Goals / Non-Goals

**Goals:**
- Multi-user hosted service with durable state and real identity.

**Non-Goals:**
- Token-by-token streaming of answers (left as future work in the baseline).

## Decisions

- **Cloud Run** over GKE (cluster overhead) or Cloud Functions (timeout too tight for self-correct + dry-run/execute).
- **Memorystore for checkpoints** — high write churn, ephemeral; kept off the durable stores. The Conversation Store is the record; Redis is a cache in front of it.
- **Cloud SQL for Postgres** for reports, conversations and (later) pgvector — relational access with joins.
- **IAM-authenticated service-to-service calls**; the service reaches BigQuery, Cloud SQL, Memorystore and Firestore with its own identity.
- **MCP over Streamable HTTP** with OAuth-derived `owner`; delete tokens moved to Memorystore so preview and confirm can hit different instances.

## Risks / Trade-offs

- [Paused delete confirmation lost on checkpoint eviction] → by design not rehydrated; user re-asks.
- [Third-party tracing at production scale] → self-host a tracer (e.g. Langfuse) if data-sharing posture requires it.
