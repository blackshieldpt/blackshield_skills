# PR and merge

## Commits

One commit per logical change, usually one. Subject: conventional-commit line scoped to the
area (`fix(web):`, `fix(drive):`, `test(retention):`, `docs:`). End with the
`Co-Authored-By` trailer used elsewhere in the history. Stage by path; never `git add -A`.

```bash
git add -- <paths>
git commit
git push -u origin HEAD
gh pr create --title "fix(web): ..." --body "$(cat <<'EOF'
...
EOF
)"
```

On re-entry with commits already pushed, add a commit rather than amending; squash only
if the repo's history shows squash merges. If `gh pr list --head <branch>` found an open
PR, `gh pr edit <PR> --body-file <file>` instead of creating a second one.

## PR body

Redact per SKILL.md Setup — the evidence table's failure column is raw test output and
is the place a hostname or path leaks.
Sections, not a wall:

- `Closes #<N>` on the first line, so the merge closes the issue;
- what the change is, and how it was found;
- the mechanism, with `path:line`, and a table when several call sites share one shape.
  This is the plan as built, including where it departed;
- **Verification**: the evidence table, plus the suite, e2e and typecheck results from
  the run after the last review round, with any suite that could not run marked
  "not run" and why;
- **Review**: how many adversarial rounds, and what each changed, then **one line per
  finding naming its defect class** (e.g. "recover placed before the state it skips is
  reset") — the next issue's reviewer brief pastes these as known failure classes. If a
  round changed nothing, say that;
- **Not fixed here**: anything found and filed instead, with issue numbers;
- what the CHANGELOG entry carries, and which docs were updated.

## Merge sequence

Only when asked. Each line is a step; stop at the first that fails and never continue to
`gh pr merge` past a failure. Two exceptions are defined: a rebase conflict confined to
`CHANGELOG.md` is resolved by keeping both sides; any other conflict is `git rebase
--abort` and stop. A suite failure that is pre-existing, already filed, and reproduced at
`origin/<main>` does not block; name it in the PR. `<PR>` is the pull request number, not
the issue number.

```bash
git fetch origin
git rebase origin/<main>               # immediately before the merge, never in a batch
git diff origin/<main> -- CHANGELOG.md # after a keep-both conflict: this PR's entry appears once
<suite command for the touched area>   # <main> may have grown a call site this PR never saw
git push --force-with-lease
gh pr checks <PR> --watch              # every check green. "no checks reported" = no CI: say so in the PR body (testing.md) and ask before merging
gh pr merge <PR> --<strategy>          # squash, merge, or rebase per the repo's history; ask if unclear
git switch <main> && git pull          # worktree with <main> checked out elsewhere: git -C <primary> pull
```

A batch rebase goes stale after the first merge because every PR touches
`CHANGELOG.md [Unreleased]`. A PR with a follow-up commit that rewrites its own entry gets
silently duplicated by "keep both"; the diff step catches it. A long-open PR can merge
with zero conflicts and still regress; the suite re-run catches that.
