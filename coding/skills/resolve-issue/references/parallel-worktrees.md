# Parallel agents across several branches

Read when several branches are being worked at once by agents, or when the current
checkout is a linked worktree.

- Create the branch in the primary checkout and push it before adding a worktree:
  `git switch -c <branch> && git push -u origin <branch> && git switch <main>`. Then
  `git worktree add --detach <path> origin/<branch>`. Agents push with
  `git push origin HEAD:refs/heads/<branch>`. Using `git switch -c` inside a worktree, or
  switching branches in the primary, changes files under other agents mid-read.
- Inside a linked worktree, `git switch <main>` fails when `<main>` is checked out in the
  primary. Branch from `origin/<main>` after `git fetch origin` instead, and pull `<main>`
  with `git -C <primary> pull`.
- Do not delete worktrees created by others; some hold commits that are not on origin.
- A fresh worktree lacks gitignored files the suites need (licence files, `node_modules`,
  fixtures). Project rules list them; pass the same setup to `$S/base-run.sh -s`. When
  symlinking a directory from the primary checkout, check it does not already exist in
  the worktree first.
- If the repo forbids parallel suite runs, serialize the suite across all agents.
- Use the detached-worktree push and pull variants in `pr-and-merge.md`.
