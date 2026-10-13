# Tasks

## 1. Tests first

- [ ] 1.1 Write failing tests for the golden-bucket spec scenarios (approval gate, top-3 retrieval, empty bucket)

## 2. Store

- [ ] 2.1 Get approval for new dependencies (pgvector, embeddings client)
- [ ] 2.2 Golden Bucket table with pgvector column on the existing Postgres
- [ ] 2.3 Approve-trio entry point for analysts

## 3. Retrieval

- [ ] 3.1 Embed the question and retrieve top-3 trios
- [ ] 3.2 Inject trios into the model context before SQL generation and report writing

## 4. Verify

- [ ] 4.1 Extend the eval golden set with a case that benefits from a retrieved example
