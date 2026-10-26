# How to work on this project

Every change — feature, fix, backlog item — follows one loop: plan it as an
OpenSpec change, get the plan approved, implement it test-first, archive it,
merge it. Claude Code drives each step through a slash command; the gates
marked **you** are where nothing proceeds without an explicit yes.

The rules themselves live elsewhere and are not repeated here:
- [`CLAUDE.md`](CLAUDE.md) — constraints, build order, git rules.
- [`openspec/config.yaml`](openspec/config.yaml) — what each artifact must contain.
- [`.claude/skills/`](.claude/skills/) and [`.claude/commands/opsx/`](.claude/commands/opsx/) — what each command does.
- [`.claude/settings.json`](.claude/settings.json) + [`.claude/hooks/`](.claude/hooks/) — the rules that are enforced, not just asked for (git gates, dependency gate, pinned openspec, ruff after edits, pytest before a turn ends).

## The loop

| # | Command | What happens | Gate |
|---|---|---|---|
| 0 | `/new-branch` | Clean tree check, pull `main`, create `feat/…`, `fix/…`, `chore/…` or `docs/…`. First, because the plan is committed too. | — |
| 1 | `/opsx:explore` *(optional)* | Think the idea through against the specs and baseline design. Writes nothing unless you say so. | — |
| 2 | `/opsx:propose <idea>` | Creates `openspec/changes/<name>/` with `proposal.md`, delta `specs/`, `design.md`, `tasks.md`. Stops before any code. | — |
| 3 | Review | Read all four. Revise with `/opsx:update <name>` (keeps them coherent). Run `validate <name> --strict`. | **you** approve the plan and any new dependency |
| 4 | `/opsx:apply <name>` | Task group 1 writes failing tests from the spec scenarios; later groups make them pass; tasks get ticked `- [x]`. | — |
| 5 | `/verify` | ruff, offline `uv run pytest` (needs `docker compose up -d postgres`), `validate --all --strict`, weakened-test scan, diff vs plan → PASS/FAIL with evidence. For anything non-trivial also `/code-review` (or a fresh sub-agent review), `/security-review`, `/run`. | — |
| 6 | Live checks | Last task group: integration tests / `scripts/run_eval.py` / manual CLI run against live Gemini + BigQuery. Billed, never in default pytest. | **you** run or approve |
| 7 | `/opsx:archive <name>` | Delta specs merge into `openspec/specs/`; change moves to `openspec/changes/archive/YYYY-MM-DD-<name>/`. Done on the branch, before merging. | — |
| 8 | `/finish-branch` | Commit → push branch → `gh pr create` → `gh pr checks --watch` (red: Claude fixes and pushes again) → merge commit via `gh pr merge --merge --delete-branch` → pull `main`. | **you** approve the merge; GitHub blocks it until CI is green |

OpenSpec CLI is always run pinned:
`npx @fission-ai/openspec@1.14.1 <cmd>` (e.g. `list`, `list --specs`, `validate --all --strict`).

**Building a backlog change** (`openspec list` shows ones at 0/N tasks): it is
already proposed — skip steps 1–2, start at step 3 on a new branch.

**Plan turns out wrong mid-implementation:** stop, `/opsx:update <name>` to
fix spec/design/tasks, re-approve, continue `/opsx:apply`. Code never drifts
from the spec silently.

## Long sessions

- `/opsx:apply` on a big change: set a checkable finish line, e.g.
  `/goal all tasks in openspec/changes/<name>/tasks.md are checked and uv run pytest passes`.
- Compact with a focus: `/compact Focus on <name>: remaining tasks and decisions made`.
  After compaction a hook re-prints the branch, dirty files and open tasks.
- Drifted? Press Esc twice on an empty prompt to rewind code and/or conversation to an earlier prompt.
- Two changes at once: use separate worktrees. `.worktreeinclude` copies `.env` into each one.

## CI

- `.github/workflows/ci.yml` runs `.claude/skills/verify/check.sh` (the same gates as `/verify`:
  ruff, offline pytest with a pgvector Postgres service, `openspec validate --all --strict`,
  weakened-test scan) on every push and PR. It makes no live GCP/Gemini calls.
- `main` is protected by a GitHub ruleset: changes land only through a PR whose `checks` job is
  green; no force-push, no deletion, no bypass (admins included, since Claude uses your token).
  Only merge commits are allowed, which keeps the `--no-ff` history.
- Commits and feature-branch pushes need no confirmation; the merge does. The repo is public, so a
  push publishes: `.env` is gitignored, secret-scanning push protection is on, and `guard_git.sh`
  asks before a push that adds secret-looking files.
- `.github/workflows/claude.yml` answers `@claude` comments on issues and PRs (capped at 5 turns).
  One-time setup: run `/install-github-app` in Claude Code (repo admin), which sets the
  `ANTHROPIC_API_KEY` secret. This is dev tooling only; the agent itself stays Gemini-only.
- If this setup is ever shared across repos, package `.claude/` as a plugin
  (`.claude-plugin/plugin.json`). For one repo it isn't worth it.

## Example: renaming saved reports

```
/new-branch feat/rename-report
/opsx:propose add-report-rename: managers can rename their own saved reports by title
```

Produces `openspec/changes/add-report-rename/`. The delta spec adds to
`report-management`:

```markdown
## ADDED Requirements

### Requirement: Reports can be renamed by their owner
The system SHALL rename a saved report only when exactly one of the
requester's reports matches the given title.

#### Scenario: Ambiguous match
- **WHEN** two of the owner's reports match the title
- **THEN** nothing is renamed and the agent lists the candidates
```

`design.md` records the approach (owner re-scoped inside the `UPDATE`) and
rejected alternatives ("confirm via `interrupt()` — rejected, rename is
reversible"). `tasks.md` starts with failing tests in
`test_reports_store.py`, `test_graph.py`, `test_mcp_server.py` and ends with a
manual live run.

After you approve: `/opsx:apply add-report-rename` (red → green),
`uv run pytest`, the live run, `/opsx:archive add-report-rename`,
`/finish-branch`. `main` gets one merge commit carrying the code, the updated
`report-management` spec and the archived change with its design history.
