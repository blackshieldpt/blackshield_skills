# Parallel agents and linked worktrees

Read when several branches are being worked at once by agents, or when the current
checkout is a linked worktree.

## Starting a branch in its own worktree

Run from the primary checkout; nothing switches branches there, so other agents' files
are not disturbed:

```bash
git fetch origin
git worktree add -b <type>/<N>-<slug> <path> origin/<main>
git -C <path> push -u origin HEAD
```

The worktree is on the new branch, not detached, so the plain `git push -u origin HEAD`
and `gh pr create` forms in `pr-and-merge.md` work there. Never `git switch` inside the
primary while worktrees are active.

## Inside a linked worktree

- `git switch <main>` fails when `<main>` is checked out in the primary. Do not need it:
  `<base>` and rebases use `origin/<main>` after `git fetch origin`, and `<main>` is
  pulled with `git -C <primary> pull --ff-only`.
- Do not delete worktrees created by others; some hold commits that are not on origin.
- A fresh worktree lacks gitignored files the suites need (licence files, `node_modules`,
  fixtures). Project rules list them; pass the same setup to `$S/base-run.sh -s`. When
  symlinking a directory from the primary checkout, check it does not already exist in
  the worktree first.
- If project rules forbid parallel suite runs, serialize the suite across all agents.
