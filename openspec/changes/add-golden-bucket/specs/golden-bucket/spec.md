# Spec Delta

## Purpose

Supplies the model with analyst-approved examples of similar past questions, their SQL and their reports, so new answers follow proven query patterns and report style.

## ADDED Requirements

### Requirement: Only analyst-approved trios are stored
The system SHALL add a (question, SQL, report) trio to the Golden Bucket only through an explicit analyst approval of a saved report, never automatically from agent output.

#### Scenario: Unreviewed answer
- **WHEN** the agent produces or saves a report that no analyst has approved
- **THEN** the Golden Bucket is unchanged

#### Scenario: Analyst approves a saved report
- **WHEN** an analyst approves a saved report that has recorded source SQL
- **THEN** a trio with that report's question, SQL and content is added to the Golden Bucket with the approver and approval time

### Requirement: Only reports with recorded SQL can be promoted
The system SHALL refuse to promote a saved report that has no recorded source SQL, unless the analyst supplies the SQL.

#### Scenario: Report saved over MCP
- **WHEN** an analyst tries to approve a report with no recorded source SQL and supplies none
- **THEN** the approval is refused with a message saying the report has no source SQL, and nothing is stored

### Requirement: Analyst edits are re-checked before storing
SQL supplied or edited by the analyst SHALL pass the same read-only check as agent queries, and the question and report text SHALL contain no PII-shaped content, before a trio is stored.

#### Scenario: Edited SQL is not read-only
- **WHEN** an analyst approves a trio whose SQL is not a single read-only statement
- **THEN** the approval is refused and nothing is stored

#### Scenario: Edited report contains an email address
- **WHEN** an analyst approves a trio whose question or report contains an email-shaped string
- **THEN** the approval is refused and nothing is stored

### Requirement: Approved trios are versioned, not overwritten
Re-approving an edited version of an existing trio SHALL store a new version and retire the previous one from retrieval, keeping it in the store.

#### Scenario: Re-approval
- **WHEN** an analyst approves an edited version of a report that already has an active trio
- **THEN** only the new version is retrievable and the previous version is still stored

### Requirement: Similar examples are retrieved once per turn
For each analysis turn the system SHALL retrieve up to 3 active trios whose question similarity to the user's message is at or above a configured threshold, and provide them to the model for that turn.

#### Scenario: Similar trio exists
- **WHEN** a user asks a question whose similarity to an active trio's question is above the threshold
- **THEN** that trio is included in the model's context for the turn

#### Scenario: More than three matches
- **WHEN** five active trios are above the threshold
- **THEN** only the three most similar are provided

#### Scenario: Nothing similar enough
- **WHEN** no active trio is at or above the threshold
- **THEN** the turn proceeds without examples

#### Scenario: Empty bucket
- **WHEN** the Golden Bucket is empty
- **THEN** the turn proceeds without examples

### Requirement: Examples do not persist across turns
Retrieved examples SHALL be used for the current turn only and SHALL NOT be stored in the conversation history or the transcript.

#### Scenario: Next turn
- **WHEN** a turn used retrieved examples and the user asks an unrelated question next
- **THEN** the earlier examples are not in the model's context for the new turn

### Requirement: Examples are treated as data
Retrieved examples SHALL be presented to the model as reference data, and instructions contained in them SHALL NOT be followed.

#### Scenario: Instruction inside an example
- **WHEN** a retrieved trio's report contains text phrased as an instruction to the agent
- **THEN** the agent does not act on it

### Requirement: Retrieval failure does not block a turn
If embedding or search fails, the system SHALL proceed with the turn without examples, record the failure in the trace, and SHALL NOT show an error to the user.

#### Scenario: Embeddings service unavailable
- **WHEN** the embeddings call fails during a turn
- **THEN** the agent answers the question without examples and no retrieval error is shown

### Requirement: Retrieval is traced without content
The turn trace SHALL record the number of examples retrieved and the top similarity score, and SHALL NOT record the question, SQL or report text of any trio.

#### Scenario: Turn with examples
- **WHEN** a turn retrieves 2 examples
- **THEN** its trace event carries the count 2 and the top similarity score, and no trio text
