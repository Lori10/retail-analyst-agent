# Spec Delta

## Purpose

Supplies the model with analyst-approved examples of similar past questions, their SQL and their reports, so new answers follow proven query patterns and report style.

## ADDED Requirements

### Requirement: Only approved trios are stored
The system SHALL add a (question, SQL, report) trio to the Golden Bucket only after a human analyst has approved or edited the report.

#### Scenario: Unreviewed answer
- **WHEN** the agent produces a report that no analyst has approved
- **THEN** it is not added to the Golden Bucket

### Requirement: Similar examples are retrieved for each question
For each new analysis question the system SHALL retrieve up to 3 most similar approved trios and provide them to the model before it writes SQL and before it writes a report.

#### Scenario: Similar trio exists
- **WHEN** a user asks a question similar to an approved trio's question
- **THEN** that trio is included in the model's context for the turn

#### Scenario: Empty bucket
- **WHEN** the Golden Bucket is empty
- **THEN** the turn proceeds without examples

### Requirement: Retrieved content is PII-free
Trios SHALL be stored only with post-PII-strip content.

#### Scenario: Storing a trio
- **WHEN** an approved trio is stored
- **THEN** its report contains no PII column values
