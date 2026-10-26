# Design

## Context

See proposal.md for why. Reports live in one Postgres `reports` table behind
`ReportsStore`, which owns its `psycopg` connection and turns every
`psycopg.Error` into `ReportsStoreError`. The CLI graph handles `save_report`
and `list_reports` in `_run_tool` (normal `tools` node). Only `delete_reports`
is sent to its own `resolve_delete` node with `interrupt()`. The MCP server
wraps each tool in `traced()`, which turns `AgentError` into a `ToolError` and
emits a `tool_call` event. Owner scoping is applied in the store's SQL for every
manager-reachable operation.

## Goals / Non-Goals

**Goals:**
- One store method that both surfaces (CLI graph, MCP) call. The title rules and
  owner scoping live there, so the two surfaces can't drift apart.
- No new graph node and no new trace event type.

**Non-Goals:**
- Undo or rename history. The previous title is returned once, not stored.
- Changing `save_report`'s title handling.

## Decisions

### 1. Identify the report by id; the model resolves it

`rename_report(report_id, new_title)`. The model calls `list_reports`, picks the
id, and asks the manager when two or more reports plausibly match.

*Rejected: target by `title_contains` and resolve in the store.* That makes the
matching testable offline, but a request matching several reports then needs
its own pick step: an `interrupt()` or an error round-trip. That would rebuild
much of the delete flow for an action that changes one reversible field.

### 2. No `interrupt()` confirmation

A rename changes one field of one report and can be undone by renaming back.
CLAUDE.md asks that LangGraph nodes are not added for show. `rename_report` goes
through `_run_tool` like `save_report`.

*Rejected: show old → new and wait for "yes".* It adds a node and a resume path
for no protection a manager needs. On MCP it would also need a token handshake
like `preview_delete`/`confirm_delete`.

### 3. Validation and scoping live in `ReportsStore.rename_report`

```
rename_report(owner, report_id, new_title) -> dict | None
  title = new_title.strip(); blank -> raise ValueError (no DB call)
  WITH old AS (SELECT id, title FROM reports
               WHERE owner = %s AND id = %s FOR UPDATE)
  UPDATE reports r SET title = %s FROM old WHERE r.id = old.id
  RETURNING r.id, old.title AS old_title, r.title
  no row -> None   (missing id and foreign id are the same case)
  row    -> {"id", "old_title", "title"}
```

A single statement returns the old title atomically, with no separate SELECT
that a concurrent rename could slip between. `psycopg.Error` is wrapped by the
existing `_call` and becomes `ReportsStoreError`.

*Rejected: validate in each caller (graph, MCP).* That puts the rule in two
places. The store is already where the owner rule is enforced.

*Rejected: a new `AgentError` subclass for blank/not-found.* In the graph, a
non-self-correctable `AgentError` ends the turn via `give_up`, and a
self-correctable one uses up the SQL self-correct budget. Neither fits: the
model should simply tell the manager or ask for a title.

### 4. How each surface reports the outcomes

| Outcome | CLI graph (`_run_tool` payload) | MCP |
|---|---|---|
| renamed | `{"renamed": true, "id", "old_title", "title"}` | same dict |
| blank title (`ValueError`) | `{"error": "The new title can't be blank."}` | `ToolError`, same text |
| not found (`None`) | `{"error": "Report not found."}` | `ToolError`, same text |
| store failure | `ReportsStoreError` → `give_up` → graceful message | `traced()` → `ToolError` with its message |

The graph's error payloads are ordinary tool results, so the model reads them
and replies. They don't count against the self-correct budget. The store-failure
path is the same one `save_report`/`list_reports` already use.

### 5. Prompt

`SYSTEM_INSTRUCTION` gets one sentence: use `list_reports` to find the id, and
when two or more reports could be the one meant, ask the user which before
calling `rename_report`. The `RENAME_REPORT` tool description says the same, so
MCP clients get the guidance too.

## Risks / Trade-offs

- [Ambiguity handling depends on the prompt and can't be tested offline] →
  a manual live CLI check in tasks covers the ambiguous case. If it proves
  unreliable, switch to option B (store-side matching) in a follow-up change.
- [`save_report` still accepts a blank title, so save and rename are
  inconsistent] → kept out of scope on purpose. A follow-up change can apply
  the same trim/reject rule to save.
- [The model could rename the wrong report if it picks a wrong id] → the result
  carries `old_title`, so the reply names what changed. Renaming back is one
  more call.
- [A duplicate title makes a later `title_contains` delete match both] →
  accepted. Delete always shows the exact candidates before removing anything.

### Gaps found during testing

- [The model can send a `report_id` that isn't an integer (Gemini tool args
  arrive as JSON and may be a float or string)] → `_run_tool` converts with
  `int()` before calling the store, and a value that can't be converted
  returns "Report not found." rather than raising (an uncaught `ValueError`
  would have been reported as a blank title, or crashed the tools node). MCP
  needs no such guard: the SDK validates `report_id: int` against the input
  schema before the tool runs.
- [The graph and MCP tests use the in-memory `FakeReportsStore`, which
  re-implements the trim/blank/owner rules] → the real rules are covered by
  the Postgres-backed `tests/test_reports_store.py`; the fake only has to
  produce the same three outcomes (dict, `None`, `ValueError`).

## Migration Plan

No schema change (`title` already exists). Deploy is a normal merge. Rollback is
reverting the change; renamed titles stay as they are and are valid data.
