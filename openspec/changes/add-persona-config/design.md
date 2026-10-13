# Design

## Context

Designed in the baseline (`openspec/changes/archive/2026-10-13-baseline-current-system/design.md`, Components → "Persona Config").

## Goals / Non-Goals

**Goals:**
- Tone changes ship by editing a document, not by deploying code.

**Non-Goals:**
- Letting the persona change safety behaviour.

## Decisions

- **Single versioned document** (Firestore row or Cloud Storage file in production, whichever fits the admin surface; `persona.yaml` locally), read at session start.
- **Two-part system prompt:** the safety core stays in code and is placed so it takes precedence; the persona is appended.
- **Fallback:** if the document is missing or invalid, use a built-in default persona and log a warning.

## Risks / Trade-offs

- [Persona text tries to override safety rules] → safety core is fixed in code; eval harness re-run before a persona change ships.
