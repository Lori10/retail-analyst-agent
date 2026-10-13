# Tasks

## 1. Tests first

- [x] 1.1 Failing tests for `report-management` deltas: source question and SQL captured on save (several queries, failing query excluded, second save in same conversation, no-query save), MCP save stores none
- [x] 1.2 Failing tests for approval: unreviewed report not stored, approve stores trio with approver, report without source SQL refused, non-read-only SQL refused, email-shaped text refused, re-approval versions and retires the old row
- [x] 1.3 Failing tests for retrieval: above-threshold trio injected, top-3 cap, below-threshold and empty bucket proceed without examples, examples absent from next turn's context and from the transcript, embeddings failure proceeds without a user-visible error, trace carries count and top similarity but no trio text (embeddings faked; Postgres tests use the existing test database)

## 2. Prerequisites

- [x] 2.1 Confirm the installed `langchain-google-genai` `GoogleGenerativeAIEmbeddings` drives `gemini-embedding-2` with `output_dimensionality=768` and `SEMANTIC_SIMILARITY`; fall back to `gemini-embedding-001` if not. Stop and ask if either needs a new dependency
- [x] 2.2 Switch `docker-compose.yml` to `pgvector/pgvector:pg16`; make sure the test database gets the extension too; note the volume recreate in README

## 3. Capture source SQL on save

- [x] 3.1 Add `source_question` and `source_sql` columns to `reports` (idempotent DDL for existing databases)
- [x] 3.2 In the graph's `save_report` dispatch, collect successful `run_query` SQL and the opening user message since the previous save; MCP path stores NULL

## 4. Golden Bucket store and approval

- [x] 4.1 `GoldenBucketStore`: `golden_trios` table, HNSW index, insert-with-versioning, top-k active search filtered by embedding model
- [x] 4.2 Config: `GOLDEN_EMBEDDING_MODEL`, `GOLDEN_MIN_SIMILARITY` (default 0.75); `GoldenBucketError` in `errors.py`
- [x] 4.3 `scripts/approve_trio.py`: show a saved report's question/SQL/content, optional edits, read-only and PII-shape checks, embed and store

## 5. Retrieval

- [x] 5.1 `retrieve_examples` node between `guardrail` and `call_model`, writing a per-turn state field; failures degrade to no examples
- [x] 5.2 `call_model` prepends the delimited examples block; system prompt extends the "data, not instructions" rule to golden examples
- [x] 5.3 `turn` trace fields `golden_examples`, `golden_top_similarity`, `golden_error_class`
- [x] 5.4 Regenerate the graph diagram (`scripts/render_graph.py`) in the docs that embed it

## 6. Docs

- [x] 6.1 README: approval workflow and new env vars; CLAUDE.md scope list moves Hybrid Intelligence to coded
- [x] 6.2 Record any gaps found during testing in this change's `design.md`

## 7. Live checks (manual, billed — never under `uv run pytest`)

- [ ] 7.1 Eval harness against a separate bucket; add one case seeded with a paraphrase of its question; run `scripts/run_eval.py` and compare with the previous report
- [ ] 7.2 End-to-end manual run: ask, save, approve, ask a paraphrase, check the trace shows a retrieved example; use the traced similarity to sanity-check the 0.75 threshold
