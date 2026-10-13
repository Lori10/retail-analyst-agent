# conversation-persistence Specification

## Purpose
Keeps a durable, owner-scoped transcript of each conversation so a manager who restarts the CLI picks up where they left off.

## Requirements

### Requirement: Completed turns are appended to the transcript
After a turn completes and its answer has been shown, the system SHALL append the user's message and the agent's final reply, in order, to the conversation's transcript. A turn left awaiting delete confirmation SHALL NOT be appended.

#### Scenario: Normal turn
- **WHEN** a turn completes with an answer
- **THEN** a user row and an assistant row are appended with increasing turn indexes

#### Scenario: Pending delete confirmation
- **WHEN** a turn ends paused for delete confirmation
- **THEN** nothing is appended for that turn

### Requirement: Transcript write failure does not fail the turn
A failure writing to the transcript SHALL be logged as a warning and SHALL NOT affect the answer already shown or stop the CLI.

#### Scenario: Store down after answering
- **WHEN** the transcript store is unreachable after a turn
- **THEN** the user still has the answer and the next prompt is shown

### Requirement: Resume on startup
On startup the CLI SHALL load the owner's prior transcript and replay it as conversation history, printing "Resuming previous conversation (N message(s) loaded)." when N > 0. Rows with an unrecognized role SHALL be skipped.

#### Scenario: Restart
- **WHEN** the user restarts the CLI after a 3-turn conversation
- **THEN** the 6 prior messages are loaded and the model sees them on the next turn

### Requirement: Transcripts are owner-scoped
Each OS user SHALL have their own conversation id, and a transcript SHALL only be readable by its owner.

#### Scenario: Different owners
- **WHEN** two OS users run the CLI against the same database
- **THEN** each gets a distinct conversation id and cannot read the other's messages

#### Scenario: Wrong owner
- **WHEN** a transcript is requested with an owner who doesn't own it
- **THEN** an empty list is returned

### Requirement: Only post-PII-strip content is stored
The transcript SHALL contain only user messages and final agent replies, never raw tool results.

#### Scenario: Query turn
- **WHEN** a turn ran a query
- **THEN** the transcript holds the question and final answer, not the query rows
