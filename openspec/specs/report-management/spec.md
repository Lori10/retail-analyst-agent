# report-management Specification

## Purpose
Lets a manager save, list and delete analysis reports, with every operation scoped to the requesting owner and every deletion confirmed by the human against the exact list of reports it will remove.

## Requirements

### Requirement: Saving and listing reports
The system SHALL persist a report with title, content, optional tags, owner, conversation id and creation time, and SHALL list only the requesting owner's reports.

#### Scenario: Save then list
- **WHEN** a user saves a report with tags and then lists reports
- **THEN** the report is returned with the same title, content and tags

#### Scenario: Other owners' reports are invisible
- **WHEN** owner A lists reports
- **THEN** no report saved by owner B is returned

### Requirement: Delete candidates are resolved by scope and title
A delete request SHALL resolve to candidate reports of the requesting owner only, filtered by scope (`conversation` = this conversation, `all` = every report of the owner) and an optional case-insensitive title substring.

#### Scenario: Conversation scope
- **WHEN** a delete is requested with scope `conversation`
- **THEN** only the owner's reports from the current conversation are candidates

#### Scenario: Title filter
- **WHEN** a delete is requested with `title_contains` "churn"
- **THEN** only reports whose title contains "churn" in any case are candidates

### Requirement: Deletion requires explicit confirmation of exact candidates
When a delete request resolves to one or more reports, the agent SHALL pause, show each candidate's id, title and save date, and delete only if the user's next message is an affirmative reply (`y`, `yes`, `yeah`, `yep`, `confirm`, `confirmed`, `sure`, `go ahead`, `do it`, case-insensitive, trimmed). Any other reply SHALL abort without deleting.

#### Scenario: Confirmed deletion
- **WHEN** the agent lists 2 candidates and the user replies "yes"
- **THEN** both reports are deleted and the agent reports "Deleted 2 report(s)."

#### Scenario: Anything else aborts
- **WHEN** the agent lists candidates and the user replies with a new question
- **THEN** nothing is deleted, the agent replies "Okay, I didn't delete anything.", and the new question is not answered in that turn

#### Scenario: Zero candidates
- **WHEN** a delete request matches no reports
- **THEN** the agent replies "I couldn't find any saved reports matching that — nothing was deleted." without asking for confirmation

### Requirement: Deletion is re-scoped to the owner at execution time
The delete operation SHALL only remove reports owned by the requesting owner, even if given ids belonging to another owner.

#### Scenario: Foreign ids
- **WHEN** a delete is executed for owner A with an id of owner B's report
- **THEN** nothing is deleted

### Requirement: A delete request takes over the whole model round
If a model round contains a delete request together with other tool calls, the system SHALL handle only the delete flow for that round.

#### Scenario: Mixed round
- **WHEN** the model requests a delete and a query in the same round
- **THEN** only the delete confirmation flow runs

### Requirement: Store failures are graceful
A reports-store failure SHALL surface as "I couldn't reach the saved reports store right now. Please try again shortly." and SHALL NOT crash the CLI or expose a raw database error.

#### Scenario: Database unavailable during delete
- **WHEN** the store raises an error while resolving delete candidates
- **THEN** the agent declines gracefully and nothing is deleted
