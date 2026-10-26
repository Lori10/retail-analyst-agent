---
name: land-branch
description: Commit, push, open a PR, watch CI until green, and merge the current feature branch into main following this project's git workflow — merge commit only, explicit confirmation required before the merge. Use when a piece of work is ready to land on main.
---

# Land branch

Use when work on a feature branch is ready to land on `main`. `main` is protected by a
GitHub ruleset (PR + green `checks` job required, no bypass), so the merge happens on
GitHub, never locally. Commits and feature-branch pushes need no confirmation; the
merge does.

1. Confirm we are NOT on `main` (`git branch --show-current`). If we are, stop.
2. Commit any remaining work with a conventional message (`feat: …`, `fix: …`, `chore: …`).
   No `Co-Authored-By: Claude` trailer.
3. Run `.claude/skills/verify/check.sh --if-stale`. If it prints `already verified`, this exact
   tree passed every gate and nothing changed since; go on. Otherwise run the full `/verify`.
   Don't continue on FAIL.
4. `git push -u origin <branch>`. Then show what went out:
   `git log --oneline --stat origin/main..HEAD`.
5. Open the PR if none exists: `gh pr create --base main --title "<conventional title>" --body …`.
   For an OpenSpec change, the body summarises its `proposal.md` and links the change folder.
   No "Generated with Claude Code" line. If a draft PR already exists (opened during
   `/opsx:apply`), update its body if the plan changed and mark it ready: `gh pr ready`.
6. `gh pr checks --watch --fail-fast`.
   - Red: `gh run view --log-failed`, fix the cause on the branch, commit, push, watch again.
     Never weaken a test, skip a check, or use `--admin` / `--no-verify` to get green.
   - Same failure twice after a fix: stop and show the user the log.
7. Green: show the PR URL and the checks, and ask whether to merge. On approval:
   ```
   gh pr merge --merge --delete-branch
   git checkout main
   git pull origin main
   ```
   Merge commit only; no rebase or squash unless the user explicitly asks.
8. Confirm the CI run on `main` for the merge commit: `gh run list --branch main --limit 1`.
