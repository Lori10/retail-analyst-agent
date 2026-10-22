---
name: verify
description: Verify finished work before calling it done — run every gate (ruff, offline pytest, openspec validate, weakened-test scan), read the diff against the plan, and report PASS/FAIL with evidence. Use after an /opsx:apply task group, after any code change, and before /finish-branch.
---

# Verify

"Done" means the gates ran and their results are stated — not that tests look green.

1. Run `.claude/skills/verify/check.sh` from the repo root. It needs the docker-compose
   Postgres (`docker compose up -d postgres`); if it's down, say so — don't report PASS.
2. Read the diff: `git diff main...HEAD` plus `git diff` / `git status` for uncommitted work.
   If the branch has an OpenSpec change (`openspec/changes/<name>/tasks.md`), check the files
   its tasks name first, then list every changed file **outside** the plan and say why it changed.
3. If check.sh flags a possibly weakened test, read that hunk and decide using
   [reference.md](reference.md). A weakened test is a FAIL unless the change's delta spec changed
   that behaviour.
4. Report:
   ```
   VERIFY: PASS|FAIL
   - ruff: …  - pytest: N passed, M skipped  - openspec validate: …  - weakened tests: none|<list>
   - out-of-plan files: none|<list + reason>
   ```
   Quote the failing output tail for any FAIL.
5. For non-trivial changes, suggest `/code-review` and a cold review by a fresh sub-agent that
   didn't write the code. Never run live checks (tests/integration, scripts/run_eval.py) here —
   they're billed and the user runs or approves them.
