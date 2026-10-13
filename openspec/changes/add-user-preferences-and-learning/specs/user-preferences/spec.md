# Spec Delta

## Purpose

Remembers how each manager likes answers presented, so they don't have to repeat formatting or depth instructions every conversation.

## ADDED Requirements

### Requirement: Explicit preferences are remembered
When a manager states an answer-format or depth preference, the system SHALL store it for that manager and apply it in later turns and conversations.

#### Scenario: Numbers only
- **WHEN** a manager says "just give me the numbers" and later asks a new question
- **THEN** the answer is given as numbers without narrative

### Requirement: Preferences are per manager
A preference SHALL apply only to the manager who set it.

#### Scenario: Different manager
- **WHEN** manager A sets a preference
- **THEN** manager B's answers are unaffected

### Requirement: No automatic model or prompt changes
The system SHALL NOT change prompts or model weights automatically based on interactions; system-level changes SHALL go through human review.

#### Scenario: Negative feedback
- **WHEN** a user gives negative feedback on an answer
- **THEN** the interaction is queued for human review and no prompt changes automatically
