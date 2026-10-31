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
                self.assertIn("optimize_move_to_prewhere_if_final", text)
                self.assertIn("26.10", text)
                self.assertIn("compatibility", text)
                for phrase in old_absolutes:
                    self.assertNotIn(phrase, text)

    def test_zero_on_setting_line_is_qualified(self) -> None:
        for name, text in self.documents.items():
            for line in text.splitlines():
                if "optimize_move_to_prewhere_if_final" not in line:
                    continue
                if "0" in line:
                    lowered = line.lower()
                    with self.subTest(document=name, line=line):
                        self.assertTrue(
                            "before 26.10" in lowered or "compatibility" in lowered,
                            "a zero default must be qualified by the version "
                            "boundary or compatibility",
                        )

    def test_final_explanation_is_present(self) -> None:
        text = self.documents["SKILL.md"]
        self.assertIn("conditions that depend on the sorting key", text)
        self.assertIn("deterministic within the query", text)

    def test_manual_prewhere_warning_is_preserved(self) -> None:
        for name, text in self.documents.items():
            with self.subTest(document=name):
                self.assertIn("do not hand-write", text.lower())
                self.assertIn("*MergeTree", text)

    def test_effective_setting_is_documented(self) -> None:
        for name, text in self.documents.items():
            with self.subTest(document=name):
                self.assertIn("system.settings", text)
                self.assertIn("effective value", text)


if __name__ == "__main__":
    unittest.main()
