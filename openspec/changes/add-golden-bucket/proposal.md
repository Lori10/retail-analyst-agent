# Proposal

## Why

Requirement 1 (Hybrid Intelligence) is designed but not built. The agent writes SQL and reports from the schema alone; analyst-approved examples of past questions, SQL and reports would improve query accuracy and keep report style consistent.

The baseline design cannot be built as written: the SQL half of a trio is never persisted (the reports table and the conversation transcript keep only text), and the named embedding model, `text-embedding-004`, was shut down on the Gemini API on 2026-01-14. This change closes both gaps.

## What Changes

- Saving a report from the CLI agent records the question and SQL that produced it: the successful queries run in that conversation since the previous save, and the user message that led to the first of them, captured by the system, never supplied by the model.
- Add a Golden Bucket of analyst-approved (question, SQL, report) trios. An analyst promotes a saved report into the bucket with a separate command-line script, optionally editing the question, SQL or report; edited SQL is re-checked as read-only and edited text is checked for PII-shaped content before storing.
- For each new question, retrieve up to 3 approved trios whose question is similar above a configurable threshold, and give them to the model as reference examples for that turn only, marked as data, not instructions.
- Retrieval failures never block a turn: the turn proceeds without examples.
- Local Postgres image switches to one that ships the pgvector extension.

## Requirement mapping

- **1. Hybrid Intelligence** — moves from docs only to coded.
- **2. Safety & PII Masking** — reinforced: trios store post-strip report content; analyst-typed question and edits are checked; retrieved examples are treated as untrusted data.
- **4. Continuous Improvement** — the system-level path ("saved reports become Golden Bucket candidates") becomes real; preference profiles stay in `add-user-preferences-and-learning`.
- **5. Resilience** — embedding/search failures degrade to "no examples", never to an error shown to the user.
- **6. Quality Assurance** — the eval runs against a separate bucket and gains one case that benefits from a retrieved example.
- **7. Observability** — the `turn` trace records how many examples were retrieved and the top similarity score.

## Capabilities

### New Capabilities
- `golden-bucket`: promoting saved reports into approved trios and retrieving similar ones as per-turn context.

### Modified Capabilities
- `report-management`: a report saved through the agent records the question and SQL that produced it; reports saved over MCP record neither and cannot be promoted without analyst-supplied SQL.

## Impact

- **Code:** new Golden Bucket store and retrieval step in the graph; `save_report` path in the graph captures source SQL; new `scripts/approve_trio.py`; config for embedding model and similarity threshold; eval harness pointed at a separate bucket.
- **Data:** new `source_question` and `source_sql` columns on `reports`; new `golden_trios` table with a vector column.
- **Infra:** `docker-compose.yml` image `postgres:16-alpine` → `pgvector/pgvector:pg16`. The local `pgdata` volume may need recreating (Alpine → Debian collation); approved by the user.
- **Dependencies:** none new. Embeddings use `GoogleGenerativeAIEmbeddings` from the already-installed `langchain-google-genai`; vectors are passed to Postgres as text literals, so the `pgvector` Python package is not needed. If a task finds otherwise, it stops and asks.
- **External:** one embeddings call per question (`gemini-embedding-2`, fallback `gemini-embedding-001`).
- Background: `openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "Golden Bucket", and its proposal.md requirement 1.
