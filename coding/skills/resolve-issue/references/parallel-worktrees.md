# Parallel agents and linked worktrees

Read when several branches are being worked at once by agents, or when the current
checkout is a linked worktree.

## Starting a branch in its own worktree

Run from the primary checkout (`<primary>`, the first entry of `git worktree list`);
nothing switches branches there, so other agents' files are not disturbed:

```bash
git fetch origin
git worktree add -b <type>/<N>-<slug> <path> origin/<main>
```

Do not push at creation; the branch is pushed in phase 7 like any other. Every later git
command in that worktree is `git -C <path> ...`, since the shell's cwd does not persist,
and the helper scripts take the same flag: `$S/base-run.sh -C <path> ...` and
`$S/mutate.sh -C <path> backup|restore <file>` (`<file>` relative to `<path>`). Without
it they act on the checkout the shell is in, usually the primary and another branch.

The worktree is on the new branch, not detached, so the plain `git push -u origin HEAD`
and PR-create forms in `pr-and-merge.md` work there. Never `git switch` inside the
primary while worktrees are active.

## Inside a linked worktree

- `git switch <main>` fails when `<main>` is checked out in the primary. Do not need it:
  `<base>`, new branches and rebases use `origin/<main>` after `git fetch origin`. Never
  pull in the primary: it may have another agent's branch checked out, and the pull
  would land there.
- On re-entry, a branch already checked out in a worktree (`git worktree list`) is worked
  in that worktree; `git switch` to it anywhere else fails.
- Do not delete worktrees created by others; some hold commits that are not on origin.
- A fresh worktree lacks gitignored files the suites need (licence files, `node_modules`,
  fixtures). Project rules list them; pass the same setup to `$S/base-run.sh -s`. When
  symlinking a directory from the primary checkout, check it does not already exist in
  the worktree first.
- If project rules forbid parallel suite runs, serialize the suite across all agents.
