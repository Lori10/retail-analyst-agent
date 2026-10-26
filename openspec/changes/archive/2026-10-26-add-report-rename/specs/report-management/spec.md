## ADDED Requirements

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
