# Design

## Context

Designed in the baseline (`openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "Golden Bucket"). Summary below; that section has the full reasoning.

## Goals / Non-Goals

**Goals:**
- Retrieve analyst-approved examples for each question and use them as context.

**Non-Goals:**
- Automatic growth of the bucket from unreviewed answers.
- Fine-tuning.

## Decisions

- **Embed only the question** with `text-embedding-004` at insert and query time; SQL and report are payload. Stays inside the Google ecosystem already used for Gemini.
- **pgvector on the existing Postgres** rather than Vertex AI Vector Search: the human-curated corpus stays in the thousands, and the instance is already there. Revisit if seeded from a large archive.
- **Top-k = 3 cosine search**; trios injected before SQL generation and again before report writing.
- **Human-curated updates only**, to avoid an unreviewed mistake compounding into future retrievals. Maintenance: dedupe near-identical trios, archive trios older than ~12 months, version rather than overwrite.

## Risks / Trade-offs

- [Stale or wrong examples steer the model] → human approval gate, freshness horizon, versioning.
- [Extra latency per turn from embedding + search] → one small embedding call and an indexed search; measure via existing `turn` latency traces.
