# Spec Delta

## Purpose

Lets the business change the agent's tone and presentation style without a code change, while keeping safety behaviour fixed.

## ADDED Requirements

### Requirement: Persona is loaded from an external document
The system SHALL read the persona instructions from a versioned document outside the code at the start of each session.

#### Scenario: Tone change
- **WHEN** an admin edits the persona document and a new session starts
- **THEN** the agent uses the new tone with no code change or redeploy

### Requirement: Persona cannot override safety rules
The persona SHALL NOT change PII handling, the treatment of tool output as data, delete confirmation, or the analysis-only scope.

#### Scenario: Persona asks to show emails
- **WHEN** the persona document says to include customer emails
- **THEN** PII is still stripped and never shown

### Requirement: Missing persona falls back to default
If the persona document is missing or invalid, the system SHALL use a built-in default persona and log a warning.

#### Scenario: Invalid document
- **WHEN** the persona document cannot be parsed
- **THEN** the session starts with the default persona
