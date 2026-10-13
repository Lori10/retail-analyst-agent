# Spec Delta

## ADDED Requirements

### Requirement: Saved reports record their source question and SQL
When the agent saves a report, the system SHALL record with it the ordered successful queries run in that conversation since the previous save, and the last user message before the first of those queries. The model SHALL NOT supply either.

#### Scenario: Report built from several queries
- **WHEN** a conversation runs one failing and two successful queries, then saves a report
- **THEN** the saved report records the two successful queries, in order, and not the failing one

#### Scenario: Chat before the analysis
- **WHEN** a user greets the agent, then asks a question that runs a query, then saves a report
- **THEN** the recorded question is the question that ran the query, not the greeting

#### Scenario: Second report in the same conversation
- **WHEN** a report has been saved and the conversation runs one more successful query before saving a second report
- **THEN** the second report records only that one query

#### Scenario: Report saved without any query
- **WHEN** a report is saved in a conversation that ran no successful query since the previous save
- **THEN** the report is saved with no source SQL

### Requirement: Reports saved over MCP carry no source SQL
A report saved through the MCP server SHALL be stored with no source question and no source SQL.

#### Scenario: MCP save
- **WHEN** an MCP client saves a report
- **THEN** the report is stored and listed as usual, with no source SQL
