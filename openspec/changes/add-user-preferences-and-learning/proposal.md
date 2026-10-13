# Proposal

## Why

Requirement 4 (Continuous Improvement) is designed but not built. Managers repeat the same preferences ("just give me the numbers"), and there is no path from good or bad interactions back into the system.

## What Changes

- Per-manager preference profile (format, analysis depth), read into the system prompt each turn.
- Preferences written after an explicit request or a recurring implicit signal.
- System-level learning stays human-gated: saved/approved reports become Golden Bucket candidates; low-signal interactions feed a periodic human review. No automatic fine-tuning.

## Capabilities

### New Capabilities
- `user-preferences`: storing and applying a manager's answer preferences.

### Modified Capabilities
- None.

## Impact

- New preference store (Firestore in production; the prototype may use the existing Postgres).
- System prompt construction becomes per-user.
- Depends on `add-golden-bucket` for the system-level half.
- Background: `openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "User Preference Store", and proposal.md requirement 4.
