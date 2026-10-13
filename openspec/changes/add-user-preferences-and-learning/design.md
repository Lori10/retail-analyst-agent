# Design

## Context

Designed in the baseline (`openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "User Preference Store"; proposal.md requirement 4).

## Goals / Non-Goals

**Goals:**
- Remember each manager's preferred format and depth and apply it automatically.

**Non-Goals:**
- Automatic fine-tuning or unreviewed prompt changes — high risk for a decision-support tool.

## Decisions

- **One document per manager** (format, analysis depth, updated_at). Firestore in production because every access is a single lookup by manager id; the prototype can use a Postgres table to avoid a new service.
- **Write on explicit signal** ("just give me the numbers") or on a recurring implicit signal; read into the system prompt each turn.
- **System-level learning is human-gated:** approved reports → Golden Bucket candidates; self-correct-exhausted, abandoned or negatively rated turns (visible in `turn` traces) → weekly human review → prompt changelog reviewed by an engineer.

## Risks / Trade-offs

- [Inferred preference is wrong] → only recurring signals are written implicitly; user can override at any time.
