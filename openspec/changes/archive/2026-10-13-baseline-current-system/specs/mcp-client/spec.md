# Spec Delta

## Purpose

Lets the CLI agent use tools from external MCP servers listed in a config file, under the same tool loop and error handling as its built-in tools, without letting an untrusted server break or endanger the agent.

## ADDED Requirements

### Requirement: Server list from config
When `AGENT_MCP_SERVERS` names a file in `.mcp.json` shape, the agent SHALL connect to each listed server (stdio command or HTTP URL). A missing file, invalid JSON, or an entry with neither command nor URL SHALL stop startup with an error. When the variable is unset, the agent SHALL run with built-in tools only.

#### Scenario: Invalid config
- **WHEN** the config file contains invalid JSON
- **THEN** startup fails with a configuration error

### Requirement: Namespaced tools
Each external tool SHALL be exposed to the model as `<server>__<tool>`, with the server name sanitized to characters Gemini accepts.

#### Scenario: Two servers
- **WHEN** servers `time` and `weather` both offer `get`
- **THEN** the model sees `time__get` and `weather__get`

### Requirement: Destructive tools are skipped unless allowlisted
A tool annotated `destructiveHint: true` SHALL NOT be loaded unless it appears in that server's `tools` allowlist. When an allowlist is present, only listed tools SHALL be loaded.

#### Scenario: Destructive tool without allowlist
- **WHEN** a server offers a tool marked destructive and no allowlist names it
- **THEN** the tool is not offered to the model

### Requirement: Unreachable servers are skipped
A server that fails to connect within 30 seconds, or a tool whose schema Gemini cannot accept, SHALL be logged and skipped while the agent starts with everything else.

#### Scenario: One server down
- **WHEN** one of two configured servers is unreachable
- **THEN** the agent starts with the other server's tools

### Requirement: External tool errors use the standard error flow
An error reported by an external tool SHALL be self-correctable. A closed session or a call exceeding 60 seconds SHALL be a non-self-correctable "unavailable" error. External tool calls SHALL emit `tool_call` events with `transport` `mcp`.

#### Scenario: Tool rejects arguments
- **WHEN** an external tool returns an error
- **THEN** the model receives it and may retry within the self-correct budget

#### Scenario: Session gone
- **WHEN** an external server's session has closed
- **THEN** the turn ends with "That external tool isn't available right now. Please try again shortly."

### Requirement: Non-text content is replaced
Image, audio and resource content in an external tool result SHALL be replaced with a placeholder such as `[image content omitted]`.

#### Scenario: Image result
- **WHEN** a tool returns an image block
- **THEN** the model receives `[image content omitted]` in its place
