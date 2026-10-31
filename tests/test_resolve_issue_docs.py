"""Content tests for the resolve-issue subagent policy.

These are static documentation tests: the resolve-issue workflow is prose
executed by an agent, so there is no code path to exercise. They pin the
closing sentence of the Setup "Subagents" paragraph so it allows the roles the
same paragraph defines instead of contradicting them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "coding" / "skills" / "resolve-issue" / "SKILL.md"

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


if __name__ == "__main__":
    unittest.main()
