"""Content checks for the compatibility matrix and the harness contract.

These are static documentation tests: they read repository files only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
CODING = REPO_ROOT / "coding" / "skills"
HARNESS = CODING / "_shared" / "harness.md"
ADAPTER = CODING / "_shared" / "forge.py"
REVIEW = CODING / "review" / "SKILL.md"
PLAYBOOK = CODING / "review" / "references" / "playbook.md"

ROWS = [
    ("`blueprint`, `clickhouse`", ["yes", "yes", "yes", "yes"]),
    ("`audit-codebase-security`", ["yes", "yes", "yes", "yes"]),
    ("`review` local scopes", ["yes", "yes", "yes", "yes"]),
    (
        "`review` PR, `file-issue`, `resolve-issue`",
        ["yes", "planned", "planned", "planned"],
    ),
    ("Plan workflows", ["yes", "partial", "partial", "partial"]),
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalized(text: str) -> str:
    return " ".join(text.split())


def flat(text: str) -> str:
    return normalized(re.sub(r"(?m)^> ?", "", text))


F_SENTENCE = (
    "`$F` is `_shared/forge.py` next to this skill's directory: "
    "`<base directory>/../_shared/forge.py`"
)
F_FALLBACK = "`${CLAUDE_PLUGIN_ROOT}/skills/_shared/forge.py` is a Claude-only fallback"
F_EXIT2 = "Exit 2 alone does not prove the forge is unsupported"


def table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows


class CompatibilityDocsTest(unittest.TestCase):
    def test_readme_compatibility_matrix(self):
        text = read(README)
        self.assertIn("## Compatibility", text)
        self.assertLess(
            text.index("## Compatibility"),
            text.index("## Usage"),
            "Compatibility must come before Usage",
        )
        section = text.split("## Compatibility", 1)[1].split("## Usage", 1)[0]
        rows = table_rows(section)
        header = rows[0]
        self.assertEqual(
            header,
            ["Area", "Claude Code", "OpenCode", "Codex", "Forgejo/Gitea"],
        )
        by_area = {row[0]: row[1:] for row in rows[2:] if len(row) == 5}
        for area, expected in ROWS:
            with self.subTest(area=area):
                self.assertIn(area, by_area)
                self.assertEqual(by_area[area], expected)
        self.assertIn("planned", section)
        self.assertIn("coding/skills/_shared", section)

    def test_readme_does_not_overclaim_forge_support(self):
        section = read(README).split("## Compatibility", 1)[1].split(
            "## Usage", 1
        )[0]
        body = normalized(section)
        self.assertIn("not claimed to run on Forgejo/Gitea", body)
        self.assertIn("plan 014", body)

    def test_harness_contract(self):
        self.assertTrue(HARNESS.is_file(), "coding/skills/_shared/harness.md missing")
        text = read(HARNESS)
        for needle in (
            "Invocation text",
            "AGENT_MODEL_REVIEWER",
            "AGENT_MODEL_SWEEP",
            "AGENT_MODEL_EXECUTOR",
            "unvetted",
            "unsupported forge",
            "isolation: \"worktree\"",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_harness_defines_forge_path(self):
        body = flat(read(HARNESS))
        for needle in (
            F_SENTENCE,
            F_FALLBACK,
            F_EXIT2,
            'never `python3 "$F" --repo',
            "When detection prints `unsupported forge` (exit 2), stop and report the host and configuration",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)
        self.assertNotIn("plan 014", body)

    def test_review_defines_forge_path(self):
        text = read(REVIEW)
        body = flat(text)
        for needle in (
            F_SENTENCE,
            F_FALLBACK,
            F_EXIT2,
            "The invocation text (Claude Code: `$ARGUMENTS`)",
            "Requires git; PR targets use the bundled forge adapter.",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)
        self.assertEqual(text.count("$ARGUMENTS"), 1)

    def test_review_pr_reads_use_forge(self):
        body = flat(read(REVIEW))
        for needle in (
            '`python3 "$F" pr view N --repo "$REPO"` (fields',
            'python3 "$F" pr diff N --repo "$REPO"',
            'python3 "$F" issue view',
            'python3 "$F" repo',
            'python3 "$F" pr list --head <branch> --state open --repo "$REPO"',
            "each `commit_messages[].message` (subject and body)",
            "per `closing_issues_external` entry its `number` with `--repo <HOST>/<repo>`",
            "with `fork: true` and a `parent`, `$REPO` is `<host>/<parent>`",
            "with `fork: true` and no `parent`, ask which repository holds the PR",
            "head_sha",
            "<base_branch>",
            "PR scope: on a non-zero `$F` exit print its stderr and stop",
            "a failed `pr diff` takes the large-diff fallback",
            "If `pr view` exits 4, ask whether `issue N` was meant",
            'list it under "Not reviewed" and continue; do not stop',
            "with exactly 100 the list may be truncated",
            "commit whose parent is not itself listed",
            '(`python3 "$F" issue view N`, with `--repo "$REPO"` when set',
            "ends in `/OWNER/NAME` or `:OWNER/NAME` (optionally `.git`)",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)
        for banned in ("headRefOid", "baseRefName", "with the tool the PR scope uses"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, body)

    def test_review_has_no_gh_reads(self):
        body = flat(read(REVIEW))
        start = body.index("`--comment` → after showing")
        end = body.index("- `issue N` →", start)
        inside = body[start:end]
        outside = body[:start] + body[end:]
        self.assertIn("gh pr comment", inside)
        self.assertIn("gh repo view --json visibility", inside)
        self.assertIn(
            "`--comment` uses `gh`, so it posts only on GitHub; on another forge, say so and do not post",
            inside,
        )
        self.assertIsNone(re.search(r"\bgh (pr|repo|issue|api|auth)\b", outside))
        self.assertIn(
            'python3 "$F" pr list --state merged --limit 10 --repo "$REPO"', outside
        )
        self.assertIn("skip this step and say so", outside)

    def test_review_local_scopes_survive_forge_failure(self):
        body = flat(read(REVIEW))
        for needle in (
            "keep `origin`",
            "local review works on every host",
            "never add one yourself",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)

    def test_harness_keeps_unvetted_distinct_from_self_vetted(self):
        body = normalized(read(HARNESS))
        self.assertIn("`unvetted` is not `self-vetted`", body)
        self.assertIn("must not silently downgrade a reviewer", body)

    def test_adapter_exists_and_is_stdlib_only(self):
        self.assertTrue(ADAPTER.is_file(), "coding/skills/_shared/forge.py missing")
        text = read(ADAPTER)
        self.assertIn("#!/usr/bin/env python3", text)
        for forbidden in ("import requests", "from requests", "import httpx"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_no_skill_uses_the_adapter_yet(self):
        """Plan 013 builds the foundation; plan 014 performs the migration."""

        targets = list(CODING.glob("*/SKILL.md"))
        targets += list(CODING.glob("*/references/*.md"))
        self.assertTrue(targets, "no skills found")
        for path in targets:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertNotIn(
                    "forge.py",
                    read(path),
                    "a skill was migrated in the foundation plan",
                )

    def test_shared_directory_is_documentation_and_adapter_only(self):
        shared = sorted(
            path.name for path in (CODING / "_shared").iterdir() if path.is_file()
        )
        self.assertEqual(shared, ["forge.py", "harness.md"])


if __name__ == "__main__":
    unittest.main()
