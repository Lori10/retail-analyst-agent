# Tasks

## 1. Tests first

- [x] 1.1 Failing store tests in `tests/test_reports_store.py` (Postgres test database): rename own report returns id/old_title/title and leaves content, tags, created_at unchanged; surrounding whitespace trimmed; blank and whitespace-only title raise `ValueError` and leave the title unchanged; duplicate title accepted; foreign id and missing id both return `None` and leave B's title unchanged; underlying `psycopg.Error` raises `ReportsStoreError`. Verify: `uv run pytest tests/test_reports_store.py` fails only on the new tests
- [x] 1.2 Failing graph tests in `tests/test_graph.py`: `rename_report` goes through the normal tools node with no interrupt and the payload carries `renamed`/`old_title`/`title`; blank title and not-found return an `error` payload that the model answers (turn does not end in `give_up`, self-correct count unchanged); store error during rename ends with the graceful reports-store message. Add `rename_report` to the test file's `FakeReportsStore` (helper, not an assertion change). Verify: new tests fail, existing ones still pass
- [x] 1.3 Failing MCP tests in `tests/test_mcp_server.py`: `rename_report` renames an own report; foreign id gives a "not found" tool error; blank title gives a tool error; a `tool_call` trace event with `transport` `mcp` is emitted. Update the exact-tool-set assertion in `test_no_single_step_delete_tool_is_exposed` to include `rename_report` — allowed because the `mcp-server` "Exposed tool set" requirement changes in this change. Verify: new tests fail

## 2. Store

- [x] 2.1 `ReportsStore.rename_report(owner, report_id, new_title)` per design decision 3 (trim, `ValueError` on blank before any DB call, single CTE `UPDATE ... RETURNING` scoped by owner and id, `None` when no row, wrapped in `_call`). Verify: all 1.1 tests pass

## 3. CLI agent

- [x] 3.1 `RENAME_REPORT` schema in `tools.py` (integer `report_id`, string `new_title`, description telling the model to find the id with `list_reports` and ask when two or more reports could match); add it to `TOOLS`. Verify: the tool appears in `TOOLS`
- [x] 3.2 `_run_tool` dispatches `rename_report` and maps outcomes per design decision 4 (`ValueError` → `{"error": "The new title can't be blank."}`, `None` → `{"error": "Report not found."}`); update the docstring's tool list. Verify: all 1.2 tests pass
- [x] 3.3 Add the rename sentence to `SYSTEM_INSTRUCTION` in `cli.py` (find the id with `list_reports`; ask which one when two or more could match). Verify: `uv run pytest tests/test_cli.py` passes; if `test_cli.py`'s `FakeReportsStore` needs `rename_report` for any new path, add it there too

## 4. MCP server

- [x] 4.1 `rename_report(report_id: int, new_title: str)` tool in `mcp_server.py` using `RENAME_REPORT["description"]`, wrapped in `traced("rename_report")`, raising `ToolError` with the same texts for blank and not-found. Verify: all 1.3 tests pass

## 5. Docs and gates

- [x] 5.1 README: mention renaming next to save/list/delete in the Saved Reports Store section, and list `rename_report` among the MCP tools if the README lists them. Verify: `grep -n rename_report README.md`
- [x] 5.2 Record any gaps found during testing under Risks / Trade-offs in this change's `design.md`
- [x] 5.3 Run `/verify` (ruff, offline pytest, `openspec validate add-report-rename --strict`, weakened-test scan) and confirm PASS

## 6. Live check (manual, billed — never under `uv run pytest`)

- [ ] 6.1 Manual CLI run against real Gemini: save two reports with "sales" in the title; ask "rename the sales report to X" and confirm the agent asks which one and renames nothing; answer with one, confirm the reply names the old and new title and `list_reports` shows it; ask to rename to a blank title and confirm nothing changes

## Workflow follow-up

- `/opsx:archive` on the branch, updating `report-management`'s Purpose line to include renaming.
- `/land-branch`.
