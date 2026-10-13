# Spec Delta

## Purpose

Exposes the agent's data and reports tools to external MCP clients over stdio, with every safety guarantee (PII stripping, owner scoping, confirmed deletion) enforced server-side regardless of the client's behaviour.

## ADDED Requirements

### Requirement: Exposed tool set
The server SHALL expose exactly `get_schema`, `run_query`, `save_report`, `list_reports`, `preview_delete` and `confirm_delete`, and SHALL NOT expose a single-step delete tool. It SHALL NOT require `GEMINI_API_KEY`.

#### Scenario: Listing tools
- **WHEN** a client lists the server's tools
- **THEN** no tool deletes reports in one step

### Requirement: Same data guarantees as the agent
`run_query` and `get_schema` SHALL apply the same read-only check, cost cap, row limit and PII stripping as the CLI agent, and `get_schema` SHALL accept only the four dataset tables.

#### Scenario: Redacted columns are reported
- **WHEN** a client runs a query selecting `email` and `country`
- **THEN** the rows contain `country` only and `redacted_columns` lists `email`

### Requirement: Two-step token deletion
`preview_delete` SHALL delete nothing and return the exact candidates plus a random confirmation token; `confirm_delete(token)` SHALL delete exactly the previewed ids. A token SHALL be single-use (consumed even when rejected), bound to the owner, and expire after 300 seconds.

#### Scenario: Valid confirmation
- **WHEN** a client previews 2 reports and confirms with the token
- **THEN** exactly those 2 reports are deleted

#### Scenario: Report added after preview
- **WHEN** a matching report is saved between preview and confirm
- **THEN** it is not deleted

#### Scenario: Token reuse
- **WHEN** a token is used a second time
- **THEN** it is rejected and nothing is deleted

#### Scenario: Expired or unknown token
- **WHEN** a token is older than 300 seconds or was never issued
- **THEN** it is rejected and nothing is deleted

#### Scenario: Nothing to delete
- **WHEN** a preview matches no reports
- **THEN** no token is issued

### Requirement: Owner scoping
All report tools SHALL be scoped to the server process's OS user, and reports saved through MCP SHALL use conversation id `mcp-session-<owner>`.

#### Scenario: Another owner's reports
- **WHEN** a client previews a delete with scope `all`
- **THEN** no other owner's report is a candidate

### Requirement: Error exposure
A handled agent error SHALL be returned as an MCP tool error carrying its message, so the client model can correct itself. Any other exception SHALL be returned only as a generic error, with its message logged server-side.

#### Scenario: Unexpected exception
- **WHEN** a tool raises an unexpected exception
- **THEN** the client sees a generic error that does not include the exception's message

### Requirement: Fast handshake with lazy resources
The server SHALL complete the stdio handshake without connecting to BigQuery or Postgres, building each on first use. A failed build SHALL be reported to the client and retried on the next call. Logs and traces SHALL go to stderr. Every tool call SHALL emit a `tool_call` trace event with `transport` `mcp`.

#### Scenario: Postgres down at first call
- **WHEN** the first reports call fails because Postgres is not running
- **THEN** the client gets a readable error, and a later call succeeds once Postgres is up
