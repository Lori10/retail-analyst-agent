# Proposal

## Why

Requirement 8 (Agility / Persona Management) is designed but not built. The agent's tone is hard-coded in the system prompt, so a tone change requested by leadership needs a code change and redeploy.

## What Changes

- Move the persona/tone instructions into a versioned document outside the code, read fresh each session.
- Safety instructions (untrusted tool output, delete confirmation, scope) stay in code and cannot be overridden by the persona.

## Capabilities

### New Capabilities
- `persona-config`: externally editable, versioned persona instructions.

### Modified Capabilities
- None.

## Impact

- System prompt assembly changes: fixed safety core + persona document.
- Production: Firestore or Cloud Storage with an admin surface; prototype: a local `persona.yaml`.
- Background: `openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "Persona Config", and proposal.md requirement 8.
