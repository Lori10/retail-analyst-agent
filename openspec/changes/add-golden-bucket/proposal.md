# Proposal

## Why

Requirement 1 (Hybrid Intelligence) is designed but not built. The agent writes SQL and reports from the schema alone; analyst-approved examples of past questions, SQL and reports would improve query accuracy and keep report style consistent.

## What Changes

- Add a Golden Bucket of analyst-approved (question, SQL, report) trios.
- Retrieve the top-k most similar trios for each new question and give them to the model as few-shot context before SQL generation and before report writing.
- Trios enter the bucket only after a human analyst approves or edits a report.

## Capabilities

### New Capabilities
- `golden-bucket`: storing approved trios and retrieving similar ones as context for new questions.

### Modified Capabilities
- None.

## Impact

- New store (pgvector on the existing Postgres), an embeddings call per question (`text-embedding-004`), a new graph step before `call_model`.
- New dependencies to be approved: pgvector support for psycopg, and an embeddings client.
- Background design: `openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "Golden Bucket", and proposal.md requirement 1.
