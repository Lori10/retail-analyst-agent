# Proposal

## Why

A manager can save, list and delete reports but cannot change one. When a title
turns out to be unclear ("report 2", a typo, a quarter that moved on), the only
fix today is to delete the report and ask the agent to rebuild it, which re-runs
billed queries and goes through the delete confirmation. Renaming is the smallest
edit managers ask for.

## What Changes

- New `rename_report(report_id, new_title)` tool for the CLI agent. The model
  resolves which report the manager means with `list_reports`, asks the manager
  when two or more reports plausibly match, then calls `rename_report` once.
  No `interrupt()` pause: a rename touches exactly one report and is reversible
  by renaming it back.
- New `ReportsStore.rename_report(owner, report_id, new_title)`. The new title is
  trimmed; a title that is blank after trimming is rejected and nothing changes.
  Duplicate titles are allowed and there is no length limit, matching
  `save_report`. The update is scoped to the owner in SQL, so a missing id and
  another owner's id give the same "not found" result.
- The result reports both the old and the new title, so the agent can say
  "Renamed X to Y".
- Store failures surface as the existing graceful reports-store message.
- `rename_report` is added to the MCP server as one plain tool, owner-scoped
  server-side. The MCP server's exact exposed tool set grows by one.
- The system instruction mentions `rename_report` and the "ask when ambiguous" rule.
- Out of scope: editing tags or content, and changing `save_report`'s title
  handling (it still accepts a blank title; see design Risks).

No new dependency.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `report-management`: adds a requirement for renaming a report (owner scoping,
  title rules, not-found result, old/new title in the result, graceful store
  failure). Its Purpose line ("save, list and delete") is updated to include
  renaming at archive time.
- `mcp-server`: the "Exposed tool set" requirement adds `rename_report`, and
  "Owner scoping" covers it.

## Mapping to the eight requirements

- **3. High-Stakes Oversight**: unchanged. Deletion keeps its confirmation.
  Rename is deliberately not gated, because it is a single, reversible change.
  The decision is recorded in design.md.
- **2. Safety & PII Masking**: rename stays owner-scoped at execution time, on
  both the CLI and MCP surfaces. No BigQuery access, so no new PII surface.
- **5. Resilience & Error Handling**: reuses `ReportsStoreError` and its
  graceful message.
- **7. Observability**: covered by the existing per-call `tool_call` trace event
  on both surfaces; no new event type.
- Requirements 1, 4, 6 and 8: not affected.

## Impact

- `src/retail_agent/reports_store.py`: new `rename_report` method.
- `src/retail_agent/tools.py`: new `RENAME_REPORT` schema, added to `TOOLS`.
- `src/retail_agent/graph.py`: `_run_tool` dispatches `rename_report`.
- `src/retail_agent/mcp_server.py`: new `rename_report` tool.
- `src/retail_agent/cli.py`: `SYSTEM_INSTRUCTION` text.
- Tests: `tests/test_reports_store.py`, `tests/test_graph.py` and
  `tests/test_mcp_server.py`. The exact-tool-set assertion in
  `test_mcp_server.py` changes, because the `mcp-server` spec changes.
- No schema migration: `title` already exists.
