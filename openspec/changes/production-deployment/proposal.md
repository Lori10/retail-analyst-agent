# Proposal

## Why

The prototype runs as a local CLI process with in-memory checkpoints, OS-user identity and a stdio MCP server. Serving many managers needs a hosted service with real identity and durable, scalable state.

## What Changes

- Agent service on Cloud Run behind an IAM-authenticated `POST /chat` ({conversation_id, message}).
- Checkpoints in Memorystore for Redis with a short TTL; rehydrate from the Conversation Store on a miss.
- Saved Reports and Conversation Store on Cloud SQL for Postgres.
- `owner` from real per-manager auth instead of the OS user.
- MCP server over Streamable HTTP with OAuth identity; pending-delete tokens in Memorystore.
- MCP client server list from admin-managed config; remote servers over HTTP with the service identity.
- Gemini via Vertex AI; Cloud Monitoring dashboards and alerts on the structured logs.

## Capabilities

### New Capabilities
- None yet. Spec deltas (for example `owner` from authenticated identity in report-management and mcp-server) are to be written when this change is picked up.

### Modified Capabilities
- None yet.

## Impact

- Infrastructure only at proposal stage. Background: `openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "Conversation Checkpoint Store", "Agent Service compute", "Client ↔ Agent Service communication", and the "Production shape" paragraphs of "MCP Server" and "MCP Client".
