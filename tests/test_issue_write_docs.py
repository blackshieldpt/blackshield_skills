"""Content tests for the file-issue and improve --issues forge migration.

These are static documentation tests: both flows are prose executed by an
agent, so they pin the phrases and the exact `$F` call inventory that route
issue and label writes through the bundled forge adapter.
"""

from __future__ import annotations

import re
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FILE_ISSUE = REPO_ROOT / "coding" / "skills" / "file-issue" / "SKILL.md"
IMPROVE = REPO_ROOT / "coding" / "skills" / "improve" / "SKILL.md"
CLOSING = (
    REPO_ROOT / "coding" / "skills" / "improve" / "references" / "closing-the-loop.md"
)

TARGET_BULLET = '- Resolve the **target repo**: `python3 "$F" repo` reads `origin`. With `fork: true` and a `parent`, the default target is the parent (the GitHub CLI\'s default base repository for a fork; `review` targets it too): read it with `python3 "$F" repo --repo <host>/<parent>` and offer the fork (`<host>/<owner>/<name>`) as the alternative. Fall back to the fork only when the parent\'s `has_issues` is false. If reading the parent exits non-zero (3 or 4: a parent this login cannot see), print its stderr and offer the fork as the target; use it only on the user\'s explicit yes. With `fork: true` and no `parent`, ask which repository to file in. Otherwise the target is `<host>/<owner>/<name>`. Confirm the target with the user before the duplicate search, then set `REPO` to it: `<host>/<parent>` or `<host>/<owner>/<name>`. A non-empty `$REPO` handed in by a caller (§2) is used as given instead. Then `python3 "$F" repo --repo "$REPO"` must show `"has_issues": true`; if the confirmed target\'s `has_issues` is false, stop and say so. Every later `$F` call in this skill carries `--repo "$REPO"`.'
ISSUES_PREFLIGHT = '1. Preflight: `python3 "$F" repo` succeeds. The target follows file-issue\'s rule: with `fork: true` and a `parent`, the target is the parent, read with `python3 "$F" repo --repo <host>/<parent>`, falling back to the fork only when the parent\'s `has_issues` is false; with `fork: true` and no `parent`, skip issues and say so; otherwise the target is `<host>/<owner>/<name>`. The target\'s `has_issues` is true; set `REPO` to the target; `python3 "$F" auth --repo "$REPO"` prints `"authenticated": true`. If any fails, write the plan files as normal and say why issues were skipped, quoting `$F`\'s stderr.'
ISSUES_CONFIRM = "3. Show the list of titles about to become issues and the target repository; confirm once if interactive. In a non-interactive run, when the target is not origin's own repository (`<host>/<owner>/<name>` of the first `repo` output), publish nothing and list every plan as skipped with that reason."


def normalized(text: str) -> str:
    return " ".join(re.sub(r"(?m)^> ?", "", text).split())


class FileIssueForgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = FILE_ISSUE.read_text(encoding="utf-8")
        cls.body = normalized(cls.raw)

    def test_file_issue_defines_f(self) -> None:
        for phrase in (
            "`$F` is `_shared/forge.py` next to this skill's directory: "
            "`<base directory>/../_shared/forge.py`",
            "Exit 2 alone does not prove the forge is unsupported",
            "stop without filing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.body)

    def test_file_issue_forge_calls_name_repo(self) -> None:
        captures = re.findall(r'python3 "\$F" ([^`\n]*)', self.raw)
        kinds = Counter(
            " ".join(c.split()[:2]) if c.split()[0] in ("issue", "label") else c.split()[0]
            for c in captures
        )
        self.assertEqual(
            kinds,
            Counter(
                {
                    "repo": 3,
                    "auth": 1,
                    "issue list": 2,
                    "issue view": 1,
                    "issue create": 1,
                    "label list": 1,
                    "label create": 1,
                }
            ),
        )
        self.assertEqual(captures.count("repo"), 1)
        self.assertEqual(captures.count("repo --repo <host>/<parent>"), 1)
        for capture in captures:
            if capture in ("repo", "repo --repo <host>/<parent>"):
                continue
            with self.subTest(capture=capture):
                self.assertIn('--repo "$REPO"', capture)

    def test_file_issue_fork_defaults_to_parent(self) -> None:
        for phrase in (
            "the default target is the parent",
            'python3 "$F" repo --repo <host>/<parent>',
            "offer the fork",
            "only when the parent's `has_issues` is false",
            "then set `REPO` to it: `<host>/<parent>` or `<host>/<owner>/<name>`",
            "if the confirmed target's `has_issues` is false, stop and say so",
            "If reading the parent exits non-zero",
            "use it only on the user's explicit yes",
            "A non-empty `$REPO` handed in by a caller (§2) is used as given",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.body)
        self.assertNotIn("REPO=<host>/<owner>/<name>", self.body)
        self.assertNotIn("REPO=<nameWithOwner>", self.body)

    def test_file_issue_confirms_target(self) -> None:
        self.assertIn(TARGET_BULLET, self.raw.splitlines())
        self.assertIn(
            "Show the target repository (`$REPO`), the full title, labels", self.body
        )

    def test_file_issue_has_no_gh_commands(self) -> None:
        self.assertIsNone(re.search(r"\bgh (issue|label|repo|auth|pr|api)\b", self.raw))
        for text in (
            '-R "$REPO"',
            "--json",
            '"$(cat <<',
            "the remote is GitHub",
            "Requires gh.",
            "files it with gh",
        ):
            with self.subTest(text=text):
                self.assertNotIn(text, self.raw)

    def test_file_issue_body_and_labels(self) -> None:
        self.assertIn("--body-file <scratchpad>/issue-body.md", self.raw)
        self.assertIn(
            'python3 "$F" label create <name> --repo "$REPO" --description '
            '"<what it covers>" --color <hex> --if-not-exists',
            self.body,
        )
        self.assertEqual(
            self.body.count(
                "Report the `url` field of its output. When `resolve-issue` "
                "invoked this skill, return the number to it"
            ),
            1,
        )
        for text in ('--body "', "--create-labels", "Report the issue URL"):
            with self.subTest(text=text):
                self.assertNotIn(text, self.raw)

    def test_file_issue_roles_and_unvetted(self) -> None:
        for phrase in (
            "the sweep role (Claude Code: Explore, model: haiku; sonnet when the sweep must judge)",
            "the reviewer role (Claude Code: general-purpose, model: opus)",
            "label every verdict `unvetted`",
            "explicitly accepts an `unvetted` draft",
            "The invocation text (Claude Code: `$ARGUMENTS`)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.body)
        self.assertEqual(self.raw.count("$ARGUMENTS"), 1)
        self.assertNotIn("`general-purpose`", self.raw)
        self.assertNotIn("`Explore`", self.raw)

    def test_file_issue_constraints_forbid_forge_writes(self) -> None:
        start = self.raw.index("> CONSTRAINTS:")
        block = normalized(self.raw[start : self.raw.index("\n\n", start)])
        self.assertIn(
            "no forge writes (`gh` or `forge.py` issue/pr/label write commands)", block
        )
        self.assertNotIn("Do not use `gh` for anything that writes", self.body)


class ImproveIssuesForgeTest(unittest.TestCase):
    def test_improve_issues_use_forge(self) -> None:
        raw = CLOSING.read_text(encoding="utf-8")
        section = normalized(raw[raw.index("## `--issues`") :])
        for phrase in (
            'python3 "$F" repo` succeeds',
            'python3 "$F" repo --repo <host>/<parent>',
            "falling back to the fork only when the parent's `has_issues` is false",
            'python3 "$F" auth --repo "$REPO"',
            'python3 "$F" label create improve --repo "$REPO" --description '
            '"Plan written by coding:improve" --color 5319e7 --if-not-exists',
            'python3 "$F" label create <category> --repo "$REPO" --description '
            '"coding:improve <category> plan" --color 0075ca --if-not-exists',
            'python3 "$F" issue create --repo "$REPO" --title "<plan title>" '
            "--body-file <plan file>",
            "`visibility`",
            "skip the title confirmation but never the public-repo one",
            "rewrite any local absolute path in the plan",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
        lines = raw.splitlines()
        self.assertIn(ISSUES_PREFLIGHT, lines)
        self.assertIn(ISSUES_CONFIRM, lines)
        self.assertIsNone(re.search(r"\bgh (issue|label|repo|auth)\b", raw))
        self.assertNotIn("skip labels rather than fail", raw)
        self.assertNotIn("--create-labels", raw)
        skill = IMPROVE.read_text(encoding="utf-8")
        for text in ("gh issue create", "gh repo view", "via `gh`"):
            with self.subTest(text=text):
                self.assertNotIn(text, skill)


if __name__ == "__main__":
    unittest.main()
