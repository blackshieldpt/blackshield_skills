"""Content tests for the plan-backlog DONE detection procedure.

The reconcile procedure is prose executed by an agent. Most tests pin the
ordering and the vocabulary that keep a squash- or rebase-merged plan from being
reported as approved but unintegrated forever; ``DetectionCommandsRunTest`` runs
the git commands the procedure documents against a temporary repository.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOSING_THE_LOOP = REPO_ROOT / "coding" / "skills" / "improve" / "references" / "closing-the-loop.md"
PLAN_TEMPLATE = REPO_ROOT / "coding" / "skills" / "improve" / "references" / "plan-template.md"
PLANS_INDEX = REPO_ROOT / "plans" / "README.md"
RECONCILE_SKILL = REPO_ROOT / "coding" / "skills" / "reconcile-plans" / "SKILL.md"
EXECUTE_SKILL = REPO_ROOT / "coding" / "skills" / "execute-plan" / "SKILL.md"
IMPROVE_SKILL = REPO_ROOT / "coding" / "skills" / "improve" / "SKILL.md"
INDEX_ENV = "PLAN_BACKLOG_INDEX"

STATUS_VALUES = {"TODO", "IN PROGRESS", "DONE", "BLOCKED", "REJECTED", "SUPERSEDED"}

#: The final merge-state token a DONE row's status cell may end with.
MERGE_TOKEN_RE = re.compile(
    r"(?:merged `[0-9a-f]{7,40}`|merged #\d+|not merged)$"
)


def index_path() -> Path:
    """Return the index to inspect, honouring the test-only path override.

    An override that names a missing file is an error, not a reason to skip.
    """

    override = os.environ.get(INDEX_ENV)
    if not override:
        return PLANS_INDEX
    path = Path(override)
    if not path.is_file():
        raise FileNotFoundError(f"{INDEX_ENV} names a missing index: {override}")
    return path


def done_statuses(text: str) -> list[str]:
    """Return the status cell of every DONE row in an index document."""

    statuses = []
    for line in text.splitlines():
        if not line.startswith("|") or "| DONE" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 6:
            raise ValueError(f"DONE row has no status column: {line}")
        statuses.append(cells[5])
    return statuses


def normalise(text: str) -> str:
    """Collapse whitespace so Markdown line wrapping does not decide a check."""

    return " ".join(text.split())


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def done_bullet(text: str) -> str:
    """Return the normalised DONE bullet from the reconcile section."""

    start = text.index("- **DONE**")
    end = text.index("- **BLOCKED**", start)
    return normalise(text[start:end])


class DoneDetectionProcedureTest(unittest.TestCase):
    """The DONE bullet must read the index, then fall back to git/PR evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = read(CLOSING_THE_LOOP)
        cls.bullet = done_bullet(cls.raw)

    def test_done_reads_recorded_merge_state(self) -> None:
        self.assertIn("read the recorded merge state in the index first", self.bullet)
        self.assertNotIn("do not read", self.bullet)
        for phrase in ("recorded merge state", "squash", "rebase"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)

    def test_recorded_state_is_checked_first(self) -> None:
        self.assertIn("read the recorded merge state in the index first", self.bullet)
        self.assertNotIn("do not read", self.bullet)
        self.assertIn("git merge-base", self.bullet)
        self.assertLess(
            self.bullet.index("recorded merge state"),
            self.bullet.index("git merge-base"),
            "the recorded merge state must be read before commit ancestry",
        )

    def test_patch_equivalence_fallback(self) -> None:
        for phrase in (
            "git cherry <default> <plan commit>",
            'python3 -I "$F" pr list --head <plan branch> --state merged',
            "every output line starts with `-`",
            "no line starts with `+`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)

    def test_ancestry_is_not_the_only_check(self) -> None:
        old = normalise(
            "first check whether the plan's recorded branch is an ancestor of HEAD"
        )
        # Also match the backticked form (`HEAD`) so restoring the old sentence
        # verbatim cannot slip past the check.
        old_prefix = "first check whether the plan's recorded branch is an ancestor of"
        text = normalise(self.raw)
        self.assertNotIn(old, text)
        self.assertNotIn(old_prefix, text)

    def test_unintegrated_branch_still_skips_reaverification(self) -> None:
        self.assertIn("do **not** re-verify", self.bullet)
        self.assertIn("approved but unintegrated", self.bullet)

    def test_unverified_is_not_a_status_value(self) -> None:
        self.assertIn("report it as **unverified**", self.bullet)
        self.assertIn("leave the status cell `DONE`", self.bullet)
        self.assertNotIn("status cell `unverified`", self.bullet)
        self.assertIn("do not invent a status value", self.bullet)

        template = read(PLAN_TEMPLATE)
        section = template[template.index("Status values") :]
        found = set(re.findall(r"^- `([A-Z ]+)` — ", section, flags=re.MULTILINE))
        self.assertEqual(found, STATUS_VALUES)

    def test_done_row_has_merge_vocabulary(self) -> None:
        raw = read(PLAN_TEMPLATE)
        start = raw.index("- `DONE` —")
        end = raw.index("- `BLOCKED` —", start)
        definition = normalise(raw[start:end])
        self.assertIn("status cell ends with", definition)
        self.assertIn("merged <merge-commit-or-PR>", definition)
        self.assertIn("not merged", definition)
        self.assertNotIn("must not end with", definition)

    def test_plan_template_records_merge_state(self) -> None:
        self.assertIn("whether it is merged", normalise(read(PLAN_TEMPLATE)))

    def test_status_token_placement(self) -> None:
        self.assertIn(
            "prepend `verified <date>` before the final merge-state token",
            self.bullet,
        )
        self.assertIn("final merge-state token", self.bullet)
        self.assertNotIn("append `verified <date>` to the status cell", self.bullet)

        template = read(PLAN_TEMPLATE)
        start = template.index("- `DONE` —")
        end = template.index("- `BLOCKED` —", start)
        definition = normalise(template[start:end])
        self.assertIn("must not remove or move that final token", definition)

    def assert_done_status_tokens(self, statuses: list[str]) -> None:
        self.assertTrue(statuses, "no DONE rows found in the index")
        for status in statuses:
            with self.subTest(status=status):
                self.assertRegex(
                    status,
                    MERGE_TOKEN_RE,
                    "the merge-state token must be last in the status cell",
                )

    def test_live_index_tokens(self) -> None:
        path = index_path()
        if not path.is_file():
            self.skipTest(
                "plans/README.md is a local index and is not tracked in this checkout"
            )
        self.assert_done_status_tokens(done_statuses(read(path)))

    def test_live_index_accepts_not_merged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "README.md"
            index.write_text(
                "| Plan | Title | Priority | Effort | Depends on | Status | Issue |\n"
                "|---|---|---|---|---|---|---|\n"
                "| 001 | Example | P1 | S | — | "
                "DONE — `advisor/001-example` @ `abc1234`, not merged | — |\n",
                encoding="utf-8",
            )
            self.assert_done_status_tokens(done_statuses(read(index)))

    def test_merge_token_matches_template_forms_only(self) -> None:
        for token in ("merged `abc1234`", "merged #12", "not merged"):
            with self.subTest(token=token):
                self.assertRegex(f"DONE — `advisor/001` @ `abc1234`, {token}", MERGE_TOKEN_RE)
        self.assertNotRegex("DONE — `advisor/001` @ `abc1234`, merged PR #12", MERGE_TOKEN_RE)

    def test_short_done_row_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            done_statuses("| 001 | DONE — `advisor/001` @ `abc1234`, merged `abc1234` |\n")

    def test_missing_index_override_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "README.md")
            previous = os.environ.get(INDEX_ENV)
            os.environ[INDEX_ENV] = missing
            try:
                with self.assertRaises(FileNotFoundError):
                    index_path()
            finally:
                if previous is None:
                    del os.environ[INDEX_ENV]
                else:
                    os.environ[INDEX_ENV] = previous

    def test_missing_commit_is_never_merged(self) -> None:
        for phrase in (
            "`git cat-file -e <plan commit>^{commit}`",
            "only when it exits 0, prints at least one line,",
            "Empty output or exit 128 is never evidence of a merge",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)
        self.assertLess(
            self.bullet.index("git cat-file -e"),
            self.bullet.index("git cherry <default>"),
            "the commit must be confirmed present before git cherry runs",
        )

    def test_squash_compares_the_branch_as_one_patch(self) -> None:
        for phrase in (
            "`git diff $(git merge-base <default> <plan commit>) <plan commit> | git patch-id --stable`",
            "`git log -p --no-merges $(git merge-base <default> <plan commit>)..<default> | git patch-id --stable`",
            "the second field of the matching line is the squash commit",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)

    def test_pr_fallback_checks_the_head_commit(self) -> None:
        for phrase in (
            "its head commit is the plan commit or descends from it",
            "`git merge-base --is-ancestor <plan commit> <PR head commit>`",
            "`head_sha`",
            "first fetch the PR head",
            "`git fetch origin pull/<number>/head`",
            "exit 128, a commit still missing, means unverified, not \"no\"",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)

    def test_recorded_merge_is_checked_against_default_branch(self) -> None:
        self.assertIn(
            "`git merge-base --is-ancestor <merge commit> <default>`",
            self.bullet,
        )
        self.assertIn("report the plan as **regressed**", self.bullet)
        self.assertIn("None of these is a status value", self.bullet)
        report = normalise(read(RECONCILE_SKILL))
        self.assertIn("**regressed**", report)
        self.assertIn("**unpushed**", report)

    def test_checks_use_one_default_ref(self) -> None:
        self.assertIn("never against `HEAD`", self.bullet)
        self.assertIn("the work is merged locally and **unpushed**", self.bullet)
        self.assertNotIn("<plan commit> HEAD", self.bullet)
        self.assertNotIn("git cherry HEAD", self.bullet)
        self.assertNotIn("origin/<default>", self.bullet)

    def test_no_patch_id_match_goes_to_pr_fallback(self) -> None:
        self.assertIn(
            "Without a match, whether every line starts with `+` or the output is "
            "mixed (a partial merge), the git checks have not shown a merge, so use "
            "the PR fallback or report it unverified",
            self.bullet,
        )
        self.assertIn("the PR fallback ran and listed no merged PR", self.bullet)

    def test_pr_fallback_failure_is_not_evidence(self) -> None:
        for phrase in (
            "If `$F` exits non-zero",
            "2 (unsupported forge, missing configuration or usage)",
            "3 (authentication)",
            "4 (repository not found)",
            "5 (conflict)",
            "1 (any other failure)",
            "rely on the git checks",
            "Do not treat a missing PR check, or an empty list on Forgejo/Gitea, "
            "as evidence that the work is unintegrated",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)
        for phrase in (
            "gh pr list",
            "gh pr view",
            "mergeCommit",
            "headRefOid",
            "the remote is not GitHub",
            "An empty list is not evidence",
        ):
            with self.subTest(absent=phrase):
                self.assertNotIn(phrase, self.bullet)

    def test_empty_pr_list_counts_only_on_github(self) -> None:
        for phrase in (
            '`python3 -I "$F" detect` reports `github`',
            "`PR_HEAD_SCAN_LIMIT`",
            "stops at the first page shorter than 50",
            "so an empty list there leaves the plan unverified",
            "listed no merged PR for the plan branch on GitHub, where that list is complete",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.bullet)

    def test_detected_merge_is_recorded(self) -> None:
        self.assertIn("**Record a merge you detect.**", self.bullet)
        self.assertIn("Replace the cell's final `not merged`", self.bullet)
        template = read(PLAN_TEMPLATE)
        start = template.index("- `DONE` —")
        end = template.index("- `BLOCKED` —", start)
        definition = normalise(template[start:end])
        self.assertIn(
            "The one rewrite it makes is replacing `not merged` with "
            "`merged <merge-commit-or-PR>` once it detects the merge",
            definition,
        )
        self.assertIn("a PR as `merged #12`", definition)

    def test_verified_date_does_not_pile_up(self) -> None:
        self.assertIn("replacing any earlier `verified <date>`", self.bullet)
        template = read(PLAN_TEMPLATE)
        start = template.index("- `DONE` —")
        end = template.index("- `BLOCKED` —", start)
        self.assertIn(
            "prepend `verified <date>`, replacing any earlier one",
            normalise(template[start:end]),
        )


def documented_command(bullet: str, prefix: str) -> str:
    """Return the one backticked command in the DONE bullet that starts with prefix."""

    matches = [span for span in re.findall(r"`([^`]+)`", bullet) if span.startswith(prefix)]
    if len(matches) != 1:
        raise AssertionError(f"expected one documented `{prefix}...` command, found {matches}")
    return matches[0]


GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


class ForgeFallbackDocsTest(unittest.TestCase):
    """The reconcile PR fallback runs through the forge adapter."""

    def test_closing_the_loop_defines_forge_path(self) -> None:
        text = normalise(read(CLOSING_THE_LOOP))
        for phrase in (
            "`$F` is `_shared/forge.py` next to the loading skill's directory: "
            "`<base directory>/../_shared/forge.py`",
            "Exit 2 alone does not prove the forge is unsupported",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_reconcile_skill_uses_forge_fallback(self) -> None:
        text = normalise(read(RECONCILE_SKILL))
        for phrase in (
            'python3 -I "$F" pr list --head <plan branch> --state merged',
            '`python3 -I "$F" detect`',
            "`$F` is `_shared/forge.py` next to this skill's directory",
            "never stops the reconcile",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertNotIn("gh pr list", text)


class TempRepoCase(unittest.TestCase):
    """Shared helpers: a temporary repository and the documented commands."""

    repo: str

    @classmethod
    def load_commands(cls) -> None:
        bullet = done_bullet(read(CLOSING_THE_LOOP))
        cls.default_cmd = documented_command(bullet, "git symbolic-ref")
        cls.exists_cmd = documented_command(bullet, "git cat-file -e")
        cls.ancestor_cmd = documented_command(bullet, "git merge-base --is-ancestor <plan commit> <default>")
        cls.cherry_cmd = documented_command(bullet, "git cherry ")
        cls.combined_cmd = documented_command(bullet, "git diff $(git merge-base")
        cls.per_commit_cmd = documented_command(bullet, "git log -p --no-merges")

    @classmethod
    def git(cls, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd or cls.repo, env=GIT_ENV, check=True,
            capture_output=True, text=True,
        )

    @classmethod
    def init_repo(cls, path: str) -> None:
        cls.repo = path
        cls.git("init", "-q", "-b", "main", path, cwd=os.path.dirname(path))
        for name in ("a", "b", "c"):
            Path(path, f"{name}.txt").write_text(
                "".join(f"{i}\n" for i in range(1, 21)), encoding="utf-8"
            )
        cls.git("add", ".")
        cls.git("commit", "-qm", "init")

    @classmethod
    def branch(cls, name: str, path: str, lines: tuple[int, ...]) -> str:
        cls.git("checkout", "-q", "-b", name, "main")
        for line in lines:
            target = Path(cls.repo, f"{path}.txt")
            content = target.read_text(encoding="utf-8").splitlines()
            content[line - 1] = f"{name}-{line}"
            target.write_text("\n".join(content) + "\n", encoding="utf-8")
            cls.git("commit", "-qam", f"{name} {line}")
        sha = cls.git("rev-parse", "HEAD").stdout.strip()
        cls.git("checkout", "-q", "main")
        return sha

    def default_ref(self) -> str:
        result = subprocess.run(
            ["sh", "-c", self.default_cmd], cwd=self.repo, env=GIT_ENV,
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    def run_documented(
        self, command: str, commit: str, default: str | None = None
    ) -> subprocess.CompletedProcess:
        command = command.replace("<plan commit>", commit)
        command = command.replace("<default>", default or self.default_ref())
        return subprocess.run(
            ["sh", "-c", command], cwd=self.repo, env=GIT_ENV,
            capture_output=True, text=True,
        )

    def squash_match(self, commit: str) -> str | None:
        combined = self.run_documented(self.combined_cmd, commit)
        self.assertEqual(combined.returncode, 0, combined.stderr)
        patch_id = combined.stdout.split()[0] if combined.stdout.split() else None
        per_commit = self.run_documented(self.per_commit_cmd, commit)
        self.assertEqual(per_commit.returncode, 0, per_commit.stderr)
        for line in per_commit.stdout.splitlines():
            fields = line.split()
            if patch_id and fields and fields[0] == patch_id:
                return fields[1]
        return None

    def cherry_marks(self, commit: str) -> list[str]:
        cherry = self.run_documented(self.cherry_cmd, commit)
        self.assertEqual(cherry.returncode, 0, cherry.stderr)
        return [line[:1] for line in cherry.stdout.splitlines()]


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class DetectionCommandsRunTest(TempRepoCase):
    """Run the documented detection commands in a repository with no remote."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.load_commands()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.init_repo(os.path.join(cls.tmp.name, "repo"))

        cls.squashed = cls.branch("squashed", "a", (1, 2, 3))
        cls.deleted = cls.branch("deleted", "b", (1, 2))
        cls.unmerged = cls.branch("unmerged", "c", (1, 2))
        cls.near = cls.branch("near", "a", (10, 11))

        Path(cls.repo, "other.txt").write_text("unrelated\n", encoding="utf-8")
        cls.git("add", "other.txt")
        cls.git("commit", "-qm", "unrelated")
        for name in ("squashed", "deleted"):
            cls.git("merge", "-q", "--squash", name)
            cls.git("commit", "-qm", f"squash {name}")
            if name == "squashed":
                cls.squash_commit = cls.git("rev-parse", "HEAD").stdout.strip()
        # A change on main within the context lines of the "near" branch,
        # then its squash: the patch-ids no longer match.
        target = Path(cls.repo, "a.txt")
        content = target.read_text(encoding="utf-8").splitlines()
        content[7] = "main-8"
        target.write_text("\n".join(content) + "\n", encoding="utf-8")
        cls.git("commit", "-qam", "near change on main")
        cls.git("merge", "-q", "--squash", "near")
        cls.git("commit", "-qm", "squash near")

        cls.git("checkout", "-q", "-b", "feature")
        cls.git("branch", "-qD", "deleted")
        cls.git("reflog", "expire", "--expire=now", "--all")
        cls.git("gc", "-q", "--prune=now")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_default_resolves_to_local_branch_without_remote(self) -> None:
        self.assertEqual(self.default_ref(), "main")

    def test_multi_commit_squash_is_detected_by_combined_patch_id(self) -> None:
        self.assertEqual(self.run_documented(self.exists_cmd, self.squashed).returncode, 0)
        self.assertEqual(self.cherry_marks(self.squashed), ["+", "+", "+"],
                         "git cherry alone misses a multi-commit squash")
        self.assertEqual(self.squash_match(self.squashed), self.squash_commit)

    def test_unmerged_branch_has_no_squash_match(self) -> None:
        self.assertEqual(self.cherry_marks(self.unmerged), ["+", "+"])
        self.assertIsNone(self.squash_match(self.unmerged))

    def test_squash_after_nearby_change_needs_the_pr_fallback(self) -> None:
        # All `+` and no patch-id match, although the work is on main: the
        # procedure must send this case to the PR fallback, not report it
        # unintegrated on the git checks alone.
        self.assertEqual(self.cherry_marks(self.near), ["+", "+"])
        self.assertIsNone(self.squash_match(self.near))
        bullet = done_bullet(read(CLOSING_THE_LOOP))
        self.assertIn("whether every line starts with `+` or the output is mixed", bullet)

    def test_deleted_branch_commit_is_missing_not_merged(self) -> None:
        exists = self.run_documented(self.exists_cmd, self.deleted)
        self.assertNotEqual(exists.returncode, 0, "the gc'd commit should be gone")
        cherry = self.run_documented(self.cherry_cmd, self.deleted)
        self.assertEqual(cherry.returncode, 128)
        self.assertEqual(cherry.stdout, "", "exit 128 prints no line to read as merged")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class DefaultRefRunTest(TempRepoCase):
    """A remote whose origin/HEAD is unset, and a merge that was never pushed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.load_commands()
        cls.tmp = tempfile.TemporaryDirectory()
        remote = os.path.join(cls.tmp.name, "origin.git")
        cls.git("init", "-q", "--bare", remote, cwd=cls.tmp.name)
        cls.init_repo(os.path.join(cls.tmp.name, "repo"))
        cls.git("remote", "add", "origin", remote)
        cls.git("push", "-q", "origin", "main")
        cls.local_only = cls.branch("local-only", "a", (1,))
        cls.git("merge", "-q", "--no-ff", "-m", "merge local-only", "local-only")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_unset_origin_head_falls_back_to_origin_main(self) -> None:
        unset = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=self.repo, env=GIT_ENV, capture_output=True, text=True,
        )
        self.assertNotEqual(unset.returncode, 0, "origin/HEAD should be unset here")
        self.assertEqual(self.default_ref(), "origin/main")

    def test_unpushed_merge_is_not_detected_on_default(self) -> None:
        # HEAD (local main) carries the merge; origin/main does not. Detection
        # against <default> fails, the local branch passes: "unpushed".
        on_default = self.run_documented(self.ancestor_cmd, self.local_only)
        self.assertEqual(on_default.returncode, 1, on_default.stderr)
        on_local = self.run_documented(self.ancestor_cmd, self.local_only, default="main")
        self.assertEqual(on_local.returncode, 0, on_local.stderr)


class PlanSkillHarnessTest(unittest.TestCase):
    """The plan skills follow coding/skills/_shared/harness.md."""

    def test_references_resolve_from_base_directory_first(self) -> None:
        for skill in (EXECUTE_SKILL, RECONCILE_SKILL):
            with self.subTest(skill=skill.parent.name):
                text = normalise(read(skill))
                self.assertIn(
                    "`$R` is `../improve/references` resolved against this skill's base directory",
                    text,
                )
                self.assertIn("is a Claude-only fallback", text)
        improve = normalise(read(IMPROVE_SKILL))
        self.assertIn("`references/audit-playbook.md` under this skill's base directory", improve)

    def test_execute_plan_reads_invocation_text(self) -> None:
        text = normalise(read(EXECUTE_SKILL))
        self.assertIn("chosen by the invocation text (Claude Code: `$ARGUMENTS`", text)
        self.assertEqual(text.count("$ARGUMENTS"), 1)

    def test_execute_plan_creates_worktree_without_isolation(self) -> None:
        self.assertIn("`git worktree add`", normalise(read(EXECUTE_SKILL)))
        loop = normalise(read(CLOSING_THE_LOOP))
        self.assertIn("`git worktree add -b <plan branch> <path> <base commit>`", loop)
        self.assertIn("`git worktree remove <path>`", loop)
        self.assertIn("only after the final verdict — APPROVE or BLOCK, never between REVISE rounds", loop)

    def test_reviewer_roles_never_downgrade_silently(self) -> None:
        for skill in (EXECUTE_SKILL, IMPROVE_SKILL):
            with self.subTest(skill=skill.parent.name):
                text = normalise(read(skill))
                self.assertIn("`AGENT_MODEL_REVIEWER`", text)
                self.assertIn("use the session model and say so in the report", text)
        self.assertIn("`AGENT_MODEL_SWEEP`", normalise(read(IMPROVE_SKILL)))
        self.assertIn("`AGENT_MODEL_EXECUTOR`", normalise(read(CLOSING_THE_LOOP)))

    def test_unvetted_label_when_no_fresh_context(self) -> None:
        self.assertIn("label those findings `unvetted`", normalise(read(IMPROVE_SKILL)))
        self.assertIn("label its findings `unvetted`", normalise(read(CLOSING_THE_LOOP)))

    def test_improve_asks_before_running_repo_code(self) -> None:
        text = normalise(read(IMPROVE_SKILL))
        self.assertIn("Ask the user before running anything that executes the audited repo's own code", text)
        self.assertIn("**once the user agrees**", text)
        self.assertIn("any linter, formatter or type checker whose configuration is code or loads plugins", text)
        self.assertNotIn("`go vet`, `mypy`)", text)
        self.assertNotIn("the test suite if it's cheap and side-effect free", text)

    def test_stacking_on_several_unintegrated_dependencies(self) -> None:
        self.assertIn(
            "With more than one unintegrated dependency, the base must contain all of them",
            normalise(read(CLOSING_THE_LOOP)),
        )


if __name__ == "__main__":
    unittest.main()
