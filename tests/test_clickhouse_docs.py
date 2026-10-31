"""Content tests for the ClickHouse skill's FINAL/PREWHERE guidance."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "clickhouse" / "skills" / "clickhouse" / "SKILL.md"
QUERY_OPTIMIZATION = (
    REPO_ROOT
    / "clickhouse"
    / "skills"
    / "clickhouse"
    / "references"
    / "query-optimization.md"
)


def read(path: Path) -> str:
    """Return the file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def normalise(text: str) -> str:
    """Collapse whitespace so Markdown line wrapping does not decide a check."""

    return " ".join(text.split())


class ClickHouseFinalPrewhereTests(unittest.TestCase):
    """Assert the version-aware FINAL/PREWHERE rule is documented."""

    def setUp(self) -> None:
        self.documents = {
            "SKILL.md": read(SKILL),
            "query-optimization.md": read(QUERY_OPTIMIZATION),
        }

    def test_final_prewhere_is_versioned(self) -> None:
        old_absolutes = (
            "defaults to 0, since PREWHERE runs before FINAL",
            "= 0) because PREWHERE runs before FINAL",
        )
        for name, text in self.documents.items():
            with self.subTest(document=name):
                plain = normalise(text)
                self.assertIn(
                    "`optimize_move_to_prewhere_if_final` defaults to `1`", plain
                )
                self.assertIn("Before 26.10 the default is `0`", plain)
                self.assertIn("26.10", plain)
                self.assertIn("compatibility", plain)
                for phrase in old_absolutes:
                    self.assertNotIn(phrase, plain)

    def test_zero_on_setting_line_is_qualified(self) -> None:
        for name, text in self.documents.items():
            with self.subTest(document=name):
                self.assertIn("Before 26.10 the default is `0`", normalise(text))

    def test_final_explanation_is_present(self) -> None:
        for name, text in self.documents.items():
            with self.subTest(document=name):
                plain = normalise(text)
                self.assertIn("conditions that depend on the sorting key", plain)
                self.assertIn("deterministic within the query", plain)

    def test_manual_prewhere_warning_is_preserved(self) -> None:
        for name, text in self.documents.items():
            with self.subTest(document=name):
                plain = normalise(text)
                self.assertIn("`apply_prewhere_after_final` is false", plain)
                self.assertIn("still runs before FINAL", plain)
                self.assertIn("do not hand-write", plain.lower())
                self.assertIn("*MergeTree", plain)

    def test_effective_setting_is_documented(self) -> None:
        for name, text in self.documents.items():
            with self.subTest(document=name):
                plain = normalise(text)
                self.assertIn("check `system.settings`", plain)
                self.assertIn("effective value", plain)


if __name__ == "__main__":
    unittest.main()
