"""Content tests for the plan-backlog DONE detection procedure.

These are static documentation tests: the reconcile procedure is prose executed
by an agent, so there is no code path to exercise. They pin the ordering and the
vocabulary that keep a squash- or rebase-merged plan from being reported as
approved but unintegrated forever.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOSING_THE_LOOP = REPO_ROOT / "coding" / "skills" / "improve" / "references" / "closing-the-loop.md"
PLAN_TEMPLATE = REPO_ROOT / "coding" / "skills" / "improve" / "references" / "plan-template.md"
PLANS_INDEX = REPO_ROOT / "plans" / "README.md"
INDEX_ENV = "PLAN_BACKLOG_INDEX"

STATUS_VALUES = {"TODO", "IN PROGRESS", "DONE", "BLOCKED", "REJECTED", "SUPERSEDED"}

#: The final merge-state token a DONE row's status cell may end with.
MERGE_TOKEN_RE = re.compile(
    r"(?:merged `[0-9a-f]{7,40}`|merged (?:PR )?#\d+|not merged)$"
)


def index_path() -> Path:
    """Return the index to inspect, honouring the test-only path override."""

    override = os.environ.get(INDEX_ENV)
    return Path(override) if override else PLANS_INDEX


def done_statuses(text: str) -> list[str]:
    """Return the status cell of every DONE row in an index document."""

    statuses = []
    for line in text.splitlines():
        if not line.startswith("|") or "| DONE" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 6:
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
            "git cherry HEAD <plan commit>",
            "gh pr list",
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


if __name__ == "__main__":
    unittest.main()
