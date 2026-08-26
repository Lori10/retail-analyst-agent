# Retail Data Analysis Agent

A CLI chat agent for non-technical Store/Regional Managers to ask natural
language questions over `bigquery-public-data.thelook_ecommerce`, discuss
results conversationally, and get reports with action items — built as a
take-home technical assignment.

## Docs

- [docs/assignment.md](docs/assignment.md) — the original brief
- [docs/design.md](docs/design.md) — High-Level Design: architecture,
  component reasoning, data flow, error handling, requirement-by-requirement
  coverage, and full setup instructions (source of truth — start here)
- [docs/implementation-notes.md](docs/implementation-notes.md) —
  implementation-level detail, rejected alternatives, and gaps found during
  testing
- [CLAUDE.md](CLAUDE.md) — build constraints and current prototype scope

## Quick start

Two things need configuring with different prerequisites: Gemini is a
single API key; BigQuery needs the `gcloud` CLI and a real GCP project (even
though the queried dataset is public). Full detail, including
troubleshooting, is in [design.md §8](docs/design.md#8-setup-instructions--example-run).

```bash
# Prerequisites: Python 3.11, uv, gcloud CLI, a GCP project with the
# BigQuery API enabled and billing active, a Gemini API key from
# https://aistudio.google.com/apikey

gcloud auth application-default login
cp .env.example .env   # fill in GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY

uv sync
uv run retail-agent
```

Run tests: `uv run pytest` (unit tests only — see design.md §8 for how to
run the live integration tests).

## Scope

Prototype implements Safety & PII Masking and Resilience & Graceful Error
Handling in code; the other three assignment requirements are designed in
full in `docs/design.md` but left as docs only — see
[design.md §9](docs/design.md#9-prototype-vs-production-scope-matrix) for
the full scope matrix and reasoning.
