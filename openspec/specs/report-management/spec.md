# report-management Specification

## Purpose
Lets a manager save, list, rename and delete analysis reports, with every operation scoped to the requesting owner and every deletion confirmed by the human against the exact list of reports it will remove.

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

### Requirement: Renaming a report
The system SHALL let a user rename one of their own saved reports by id, changing only its title. The rename SHALL run as a single step without a confirmation pause. The result SHALL include the report id, its previous title and its new title.

#### Scenario: Rename an own report
- **WHEN** owner A renames their report "Weekly sales" to "Q3 weekly sales"
- **THEN** listing A's reports shows the report titled "Q3 weekly sales" with unchanged content, tags and save date
- **AND** the result reports old title "Weekly sales" and new title "Q3 weekly sales"

#### Scenario: No confirmation is asked
- **WHEN** the agent renames a report
- **THEN** the turn completes without pausing for user confirmation

### Requirement: Rename title rules
The new title SHALL be trimmed of leading and trailing whitespace before it is stored. A title that is empty after trimming SHALL be rejected without changing anything. A title equal to another report's title SHALL be accepted, and no length limit SHALL apply.

#### Scenario: Surrounding whitespace is trimmed
- **WHEN** a report is renamed to "  Q3 churn  "
- **THEN** its stored title is "Q3 churn"

#### Scenario: Blank title is rejected
- **WHEN** a report is renamed to "   "
- **THEN** the rename is rejected with a reason saying the title cannot be blank, and the report keeps its previous title

#### Scenario: Duplicate title is allowed
- **WHEN** a report is renamed to a title another of the owner's reports already has
- **THEN** the rename succeeds and both reports have that title

### Requirement: Rename is scoped to the owner at execution time
A rename SHALL change only a report owned by the requesting owner. An id that does not exist and an id owned by another owner SHALL give the same not-found result, so the result never reveals that another owner's report exists.

#### Scenario: Foreign id
- **WHEN** owner A renames an id belonging to owner B's report
- **THEN** the result is not found and B's report keeps its title

#### Scenario: Missing id
- **WHEN** owner A renames an id that no report has
- **THEN** the result is the same not-found result as for a foreign id

### Requirement: Rename target is resolved before renaming
The agent SHALL be instructed to find the report's id from the user's saved reports before renaming, and to ask the user which report they mean when two or more reports plausibly match, instead of picking one.

#### Scenario: Ambiguous request
- **WHEN** the user asks to rename "the sales report" and two saved reports have "sales" in their title
- **THEN** the agent asks which report to rename and renames nothing in that turn

### Requirement: Rename store failures are graceful
A reports-store failure during a rename SHALL surface as "I couldn't reach the saved reports store right now. Please try again shortly." and SHALL NOT crash the CLI or expose a raw database error.

#### Scenario: Database unavailable during rename
- **WHEN** the store raises an error while renaming
- **THEN** the agent replies with the graceful reports-store message and no title changes
