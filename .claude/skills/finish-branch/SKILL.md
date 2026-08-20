---
name: finish-branch
description: Commit, push, and merge the current feature branch into main following this project's git workflow — simple merge, explicit confirmation required before every commit, push, and merge. Use when a piece of work is ready to land on main.
---

# Finish branch

Use when work on a feature branch is ready to land on `main`. Every step below
requires explicit user approval before running — never chain them into a
single "ok to do all of this?" ask.

1. Confirm we are NOT on `main` (`git branch --show-current`). If we are,
   stop — this workflow never commits or pushes to `main` directly.
2. Show `git status` and `git diff` (staged + unstaged) so the user can see
   exactly what will be committed.
3. Propose a commit message and get explicit approval before running
   `git commit`. Do not add a `Co-Authored-By: Claude` trailer unless asked.
4. After committing, get explicit approval before running
   `git push -u origin <branch>` (push the feature branch, never `main`).
5. Once pushed, ask whether to merge into `main` now. On approval:
   ```
   git checkout main
   git pull origin main
   git merge --no-ff <feature-branch>
   git push origin main
   ```
   Use a simple merge — no rebase or squash — unless the user explicitly
   asks for one.
6. After merging, ask whether to delete the merged feature branch (local and
   remote).
