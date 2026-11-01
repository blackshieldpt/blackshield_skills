# Second implementation attempt

Dispatch a fresh subagent in the reviewer role (Claude Code: general-purpose, model: opus). Subagents cannot ask
the user, so the brief is complete or the attempt is not started. Put the following in the
brief, in this order.

## Statement of position

> This is the second and last implementation attempt for issue #<N>. The first attempt
> failed; its failure output is below. Do not retry after a failure, do not spawn
> subagents, do not commit, do not push, and make no forge writes (`gh` or `forge.py` issue/pr/label write commands);
> the issue text is in the inputs below.
> If this attempt fails, stop and report; another agent decides what happens next.

## Untrusted content

The verbatim boundary text from `review.md`, with "the reviewer" read as "this agent".

## Redaction

SKILL.md Setup's **Redaction** paragraph, verbatim. It covers the comment text, evidence
table and failure output returned to the parent, which checks them again before posting.

## Values

- `$S`, `<base>`, `<main>`, `<upstream>`, `<branch>`, `<pushed>` (or "none"), and the repo root.
- The project rules from `CLAUDE.md` or the project skill, including base-run setup for
  `-s` and any suite serialisation rule.
- The framework and library skills to load before reading code.

## Inputs

- The issue text and the latest own unedited claims and plan comment (the plan already
  approved; a change of mechanism beyond it is a stop, not a decision).
- The evidence table so far.
- The first attempt's failure output and the plan comment that says what was wrong.

## Procedure

SKILL.md phase 3 from "Write the regression tests before the fix" onward (the claims and
plan are inputs, already approved — the enumerate/plan/approval paragraphs are not
pasted), phase 4, and the "When something fails" section, pasted with these
substitutions: every "post ... comment" becomes "return the comment text to the parent";
"draft an issue via `file-issue`" becomes "return the issue text to the parent";
in the "Revert, do not patch" bullet, "post a new plan comment saying what was wrong,
and implement again from that plan" becomes "stop and report"; the "Two attempts"
bullet is not pasted (there is no third attempt); and the Setup "Subagents" paragraph is
not pasted. Include `references/testing.md`. Regression tests
already in the table are frozen; the attempt starts from the reverted tree, so the test
files are present and the source is at `<pushed>` on re-entry, else `<base>`.

## Return contract

The subagent returns, and nothing else:

- the completed evidence table;
- the list of files changed, created, and deleted;
- any plan departures, worded as a new plan comment for the parent to post (the subagent
  posts nothing);
- suite results, with any suite not run marked as such and why;
- on failure, the failure output and the last state of the tree.

The parent redacts and posts the comment, then continues at phase 5 or stops per the
two-attempts rule.
