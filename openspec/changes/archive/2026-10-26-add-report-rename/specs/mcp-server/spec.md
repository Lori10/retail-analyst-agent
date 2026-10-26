## MODIFIED Requirements

### Requirement: Exposed tool set
The server SHALL expose exactly `get_schema`, `run_query`, `save_report`, `list_reports`, `rename_report`, `preview_delete` and `confirm_delete`, and SHALL NOT expose a single-step delete tool. It SHALL NOT require `GEMINI_API_KEY`.

#### Scenario: Listing tools
- **WHEN** a client lists the server's tools
- **THEN** no tool deletes reports in one step

#### Scenario: Rename is exposed
- **WHEN** a client lists the server's tools
- **THEN** `rename_report` is among them and takes a report id and a new title

### Requirement: Owner scoping
All report tools SHALL be scoped to the server process's OS user, and reports saved through MCP SHALL use conversation id `mcp-session-<owner>`.

#### Scenario: Another owner's reports
- **WHEN** a client previews a delete with scope `all`
- **THEN** no other owner's report is a candidate

#### Scenario: Renaming another owner's report
- **WHEN** a client calls `rename_report` with the id of another owner's report
- **THEN** the client gets a tool error saying the report was not found, and that report keeps its title

#### Scenario: Blank title over MCP
- **WHEN** a client calls `rename_report` with a title that is blank after trimming
- **THEN** the client gets a tool error saying the title cannot be blank, and nothing changes
