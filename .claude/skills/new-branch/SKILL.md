---
name: new-branch
description: Create a new feature/fix branch off an up-to-date main, following this project's git workflow. Use when starting new work in this repo.
---

# New branch

Use when starting a new piece of work in this repo. Never create work directly on `main`.

1. Check `git status`. If there are uncommitted changes, stop and ask the user
   what to do with them — don't stash or discard automatically.
2. Update `main`:
   ```
   git checkout main
   git pull origin main
   ```
3. Ask the user for a short branch name if not already given. Create the
   branch with a prefix matching the work: `feat/`, `fix/`, or `chore/`.
   ```
   git checkout -b <prefix>/<short-name>
   ```
4. Confirm the new branch is checked out (`git branch --show-current`) before
   starting work.
