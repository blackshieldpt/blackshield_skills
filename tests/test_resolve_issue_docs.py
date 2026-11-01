"""Content tests for the resolve-issue subagent policy.

These are static documentation tests: the resolve-issue workflow is prose
executed by an agent, so there is no code path to exercise. They pin the
closing sentence of the Setup "Subagents" paragraph so it allows the roles the
same paragraph defines instead of contradicting them, and the git and handoff
rules whose absence lost commits or stalled a run. The helper scripts are
exercised by scripts/selftest.sh, not here.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "coding" / "skills" / "resolve-issue"
SKILL = SKILL_DIR / "SKILL.md"
REFS = SKILL_DIR / "references"
FILE_ISSUE = REPO_ROOT / "coding" / "skills" / "file-issue" / "SKILL.md"

NEW_SENTENCE = "Spawn no subagents beyond these and the review skills named in phase 6"
OLD_SENTENCE = "Spawn no other subagents except the review skills named in phase 6"


def normalise(text: str) -> str:
    """Collapse whitespace so Markdown line wrapping does not decide a check."""

    return " ".join(text.split())


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def subagents_paragraph(text: str) -> str:
    """Return the normalised Setup "Subagents" paragraph."""

    start = text.index("**Subagents.**")
    end = text.index("**Project rules.**", start)
    return normalise(text[start:end])


class SubagentPolicyTest(unittest.TestCase):
    """The allowed subagent set must be defined and not contradicted."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = read(SKILL)
        cls.paragraph = subagents_paragraph(cls.raw)

    def test_subagent_policy_is_unambiguous(self) -> None:
        self.assertIn(NEW_SENTENCE, self.paragraph)

    def test_old_contradiction_is_gone(self) -> None:
        self.assertNotIn(OLD_SENTENCE, self.raw)

    def test_all_allowed_subagents_are_named(self) -> None:
        for phrase in (
            "adversarial reviewer",
            "second-attempt",
            "Explore",
            "review skills",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.paragraph)

    def test_review_brief_is_still_linked(self) -> None:
        for reference in ("references/review.md", "references/second-attempt.md"):
            with self.subTest(reference=reference):
                self.assertIn(reference, self.raw)
                self.assertTrue((SKILL_DIR / reference).is_file())


def flat(text: str) -> str:
    """Normalise after dropping blockquote markers, so a quoted brief reads as prose."""

    return normalise(re.sub(r"(?m)^> ?", "", text))


def section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin + len(start))]


class ReferencedFilesTest(unittest.TestCase):
    """Every bundled file SKILL.md names exists."""

    def test_named_references_and_scripts_exist(self) -> None:
        names = set(re.findall(r"(?:references|scripts)/[\w.-]+\.(?:md|sh)", read(SKILL)))
        self.assertGreaterEqual(len(names), 8)
        for name in sorted(names):
            with self.subTest(name=name):
                self.assertTrue((SKILL_DIR / name).is_file(), name)


class MergeSequenceTest(unittest.TestCase):
    """The force-push is leased to the tip that was checked, and nothing needs a TTY."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = read(REFS / "pr-and-merge.md")
        cls.block = section(cls.raw, "## Merge sequence", "```\n\n")

    def test_lease_names_the_recorded_tip(self) -> None:
        self.assertIn("git push --force-with-lease=<branch>:<lease> origin <branch>", self.block)
        self.assertNotRegex(self.raw, r"(?m)^git push --force-with-lease(\s|$)")

    def test_remote_branch_checked_and_recorded_before_rebase(self) -> None:
        rebase = self.block.index("git rebase origin/<main>")
        self.assertLess(
            self.block.index("git merge-base --is-ancestor origin/<branch> HEAD"), rebase
        )
        self.assertLess(self.block.index("record it as <lease>, before the rebase"), rebase)

    def test_commit_message_from_file(self) -> None:
        self.assertIn("git commit -F <scratchpad>/commit-msg.txt", self.raw)
        self.assertNotRegex(self.raw, r"(?m)^git commit\s*$")

    def test_pulls_are_fast_forward_only(self) -> None:
        for path in (SKILL, *sorted(REFS.glob("*.md"))):
            with self.subTest(path=path.name):
                self.assertNotRegex(read(path), r"git (?:-C \S+ )?pull(?! --ff-only)")
        self.assertNotIn("git -C <primary> pull", read(REFS / "parallel-worktrees.md"))

    def test_merge_gates_and_cleanup(self) -> None:
        text = normalise(self.raw)
        for phrase in (
            "`CHANGES_REQUESTED` or `REVIEW_REQUIRED`), stops the merge",
            "\"no checks\" straight after the push is not yet \"no CI\"",
            "`git worktree remove <path>`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


class BranchAndReentryTest(unittest.TestCase):
    """Branches start at origin/<main>; re-entry resumes a dirty, behind or remote-only branch."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = read(SKILL)
        cls.reentry = normalise(section(cls.raw, "**Re-entry.**", "**Features.**"))

    def test_branch_starts_at_origin_main(self) -> None:
        self.assertIn(
            "git switch --no-track -c <type>/<N>-<short-slug> origin/<main>", self.raw
        )

    def test_branch_search_asks_on_several_names(self) -> None:
        self.assertIn("more than one name (`fix/42-a`, `feat/42-b`): stop and ask which", self.reentry)
        self.assertIn("(an `origin/` prefix dropped)", self.reentry)

    def test_dirty_tree_on_the_branch_resumes(self) -> None:
        self.assertIn("already checked out here, stay on it, dirty tree included", self.reentry)
        self.assertIn("(or on the branch already checked out)", self.reentry)

    def test_behind_remote_fast_forwards_or_stops(self) -> None:
        self.assertIn("`git log HEAD..<pushed>` is non-empty", self.reentry)
        self.assertIn("`git merge --ff-only <pushed>`", self.reentry)


class RevertAndAttemptsTest(unittest.TestCase):
    """The revert sees renames and spares working files; there is no third attempt."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = normalise(read(SKILL))

    def test_revert_ignores_rename_detection(self) -> None:
        bullet = section(self.text, "**Revert, do not patch.**", "**Tests are frozen.**")
        self.assertEqual(bullet.count("--no-renames --name-only"), 2)
        self.assertIn("never the test files or the working files", bullet)
        self.assertIn("scratchpad file outside the checkout", self.text)

    def test_no_third_attempt(self) -> None:
        self.assertIn("there is no third implementation", self.text)


class UntrustedAndBriefsTest(unittest.TestCase):
    """PR text is data; every brief carries redaction; review findings have three outcomes."""

    def test_pr_text_is_untrusted(self) -> None:
        self.assertIn("comments, PR titles and bodies, commit messages", normalise(read(SKILL)))
        self.assertIn(
            "PR titles and bodies (including the failure classes quoted from merged PRs)",
            flat(read(REFS / "review.md")),
        )

    def test_second_attempt_brief_carries_redaction(self) -> None:
        text = read(REFS / "second-attempt.md")
        self.assertIn("## Redaction", text)
        self.assertLess(text.index("## Redaction"), text.index("## Procedure"))

    def test_review_rounds_allow_filing(self) -> None:
        rounds = normalise(read(REFS / "review.md").split("## Rounds", 1)[1])
        self.assertIn("filed through `file-issue` and named in the PR body", rounds)


class WorktreeAndFileIssueTest(unittest.TestCase):
    """Scripts target the worktree; the file-issue handoff has an exit and a cleanup."""

    def test_scripts_take_worktree_dir(self) -> None:
        text = normalise(read(REFS / "parallel-worktrees.md"))
        self.assertIn("`$S/base-run.sh -C <path> ...`", text)
        self.assertIn("`$S/mutate.sh -C <path> backup|restore <file>`", text)
        self.assertIn("Never pull in the primary", text)

    def test_base_run_cwd_is_documented(self) -> None:
        text = normalise(read(REFS / "testing.md"))
        self.assertIn("run at the root of the base checkout", text)
        self.assertIn("Exit 126 or 127, flagged by a `base-run.sh: warning:` line", text)

    def test_file_issue_handoff(self) -> None:
        skill = normalise(read(SKILL))
        self.assertIn("if it files nothing (the user declines, or no claim survives", skill)
        self.assertIn("removed with `git worktree remove --force` when `file-issue` returns", skill)
        file_issue = normalise(read(FILE_ISSUE))
        self.assertIn("leave its removal to the caller", file_issue)
        self.assertIn("When `resolve-issue` invoked this skill, return the number to it", file_issue)
        self.assertIn("or `unrated` when the issue is filed without a `severity:` label", file_issue)



def strip_comment(line: str) -> str:
    return re.split(r"\s+#", line, maxsplit=1)[0].strip()


def block_command(block: str, prefix: str) -> str:
    """Return the one command line in a bash block that starts with prefix."""

    lines = [strip_comment(line) for line in block.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise AssertionError(f"expected one `{prefix}...` line, found {lines}")
    return lines[0]


GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=check, capture_output=True, text=True
    )


def commit(cwd: Path, name: str) -> str:
    (cwd / name).write_text(name + "\n", encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-qm", name)
    return git(cwd, "rev-parse", "HEAD").stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class LeaseSequenceRunTest(unittest.TestCase):
    """Run the documented merge-sequence guard and push against a remote with a teammate."""

    BRANCH = "fix/42-x"

    @classmethod
    def setUpClass(cls) -> None:
        block = section(read(REFS / "pr-and-merge.md"), "## Merge sequence", "```\n\n")
        cls.ancestor = block_command(block, "git merge-base --is-ancestor")
        cls.record = block_command(block, "git rev-parse origin/")
        cls.push = block_command(block, "git push --force-with-lease")

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.remote = root / "remote.git"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.me, self.mate = root / "me", root / "mate"
        git(root, "clone", "-q", str(self.remote), str(self.me))
        commit(self.me, "init")
        git(self.me, "push", "-q", "origin", "HEAD:main")
        git(self.me, "switch", "-qc", self.BRANCH)
        commit(self.me, "work")
        git(self.me, "push", "-q", "-u", "origin", self.BRANCH)
        git(root, "clone", "-q", str(self.remote), str(self.mate))
        git(self.mate, "switch", "-q", self.BRANCH)
        git(self.mate, "switch", "-q", "main")
        commit(self.mate, "on-main")
        git(self.mate, "push", "-q", "origin", "main")
        git(self.mate, "switch", "-q", self.BRANCH)

    def run_doc(self, command: str, lease: str = "") -> subprocess.CompletedProcess:
        text = command.replace("<branch>", self.BRANCH).replace("<lease>", lease)
        return git(self.me, *shlex.split(text)[1:], check=False)

    def teammate_pushes(self) -> str:
        sha = commit(self.mate, "teammate")
        git(self.mate, "push", "-q", "origin", self.BRANCH)
        return sha

    def on_remote(self, sha: str) -> bool:
        return git(self.remote, "merge-base", "--is-ancestor", sha, self.BRANCH, check=False).returncode == 0

    def test_teammate_commit_already_pushed_stops_before_rebase(self) -> None:
        self.teammate_pushes()
        git(self.me, "fetch", "-q", "origin")
        self.assertNotEqual(self.run_doc(self.ancestor).returncode, 0)

    def test_push_landing_after_fetch_is_rejected(self) -> None:
        git(self.me, "fetch", "-q", "origin")
        self.assertEqual(self.run_doc(self.ancestor).returncode, 0)
        lease = self.run_doc(self.record).stdout.strip()
        late = self.teammate_pushes()
        git(self.me, "rebase", "-q", "origin/main")
        git(self.me, "fetch", "-q", "origin")  # a later fetch moves origin/<branch>
        self.assertNotEqual(self.run_doc(self.push, lease).returncode, 0)
        self.assertTrue(self.on_remote(late))
        # the bare lease the sequence used to run overwrites the same commit
        git(self.me, "push", "-q", "--force-with-lease", "origin", self.BRANCH)
        self.assertFalse(self.on_remote(late))

    def test_uncontested_push_succeeds(self) -> None:
        git(self.me, "fetch", "-q", "origin")
        self.assertEqual(self.run_doc(self.ancestor).returncode, 0)
        lease = self.run_doc(self.record).stdout.strip()
        git(self.me, "rebase", "-q", "origin/main")
        self.assertEqual(self.run_doc(self.push, lease).returncode, 0)
        head = git(self.me, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(git(self.remote, "rev-parse", self.BRANCH).stdout.strip(), head)


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git or bash is not installed")
class BranchSearchRunTest(unittest.TestCase):
    """Run the documented re-entry branch search against local, origin and other-remote refs."""

    def test_names_are_switchable_and_from_origin_only(self) -> None:
        reentry = section(read(SKILL), "**Re-entry.**", "**Features.**")
        spans = [s for s in re.findall(r"`([^`]+)`", reentry) if s.startswith("git for-each-ref")]
        self.assertEqual(len(spans), 1, spans)
        command = spans[0].replace("<N>", "42")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("origin.git", "up.git"):
                git(root, "init", "-q", "--bare", "-b", "main", name)
            repo = root / "repo"
            git(root, "init", "-q", "-b", "main", str(repo))
            commit(repo, "init")
            for remote in ("origin", "up"):
                git(repo, "remote", "add", remote, str(root / f"{remote}.git"))
            for branch in ("fix/42-a", "feat/42-b", "fix/142-x", "fix/42-z"):
                git(repo, "branch", branch)
            git(repo, "push", "-q", "origin", "main", "fix/42-a", "feat/42-b")
            git(repo, "push", "-q", "up", "fix/42-a", "fix/42-z")
            git(repo, "fetch", "-q", "--all")
            git(repo, "branch", "-qD", "feat/42-b", "fix/42-z")  # feat/42-b: remote only
            out = subprocess.run(
                ["bash", "-c", command], cwd=repo, env=GIT_ENV, check=True,
                capture_output=True, text=True,
            ).stdout.split()
        self.assertEqual(out, ["feat/42-b", "fix/42-a"])
        for name in out:
            with self.subTest(name=name):
                self.assertFalse(name.startswith(("origin/", "up/", "remotes/")))


if __name__ == "__main__":
    unittest.main()
