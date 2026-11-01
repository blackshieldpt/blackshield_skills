"""Content tests for the ClickHouse skill's versioned guidance."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "clickhouse" / "skills" / "clickhouse" / "SKILL.md"
REFERENCES = REPO_ROOT / "clickhouse" / "skills" / "clickhouse" / "references"
QUERY_OPTIMIZATION = REFERENCES / "query-optimization.md"
REPLICATION = REFERENCES / "distributed-and-replication.md"
GOTCHAS = REFERENCES / "community-gotchas.md"
INGESTION = REFERENCES / "ingestion-and-merges.md"


# A bare 0 value token, not a digit inside a version such as 26.10.
DEFAULT_ZERO = re.compile(r"(?<![\d.])0(?![\d.])")


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
            lines = [
                line
                for line in text.splitlines()
                if "optimize_move_to_prewhere_if_final" in line
            ]
            with self.subTest(document=name):
                self.assertTrue(lines, "setting is not mentioned")
            for line in lines:
                with self.subTest(document=name, line=line[:60]):
                    if DEFAULT_ZERO.search(line):
                        lowered = line.lower()
                        self.assertTrue(
                            "before 26.10" in lowered or "compatibility" in lowered,
                            "a 0 on the setting's line must be qualified",
                        )

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


class ClickHouseVersionedFactTests(unittest.TestCase):
    """Pin corrected, version-dependent facts so a revert fails loudly."""

    def setUp(self) -> None:
        self.skill = normalise(read(SKILL))
        self.replication = normalise(read(REPLICATION))
        self.gotchas = normalise(read(GOTCHAS))
        self.query = normalise(read(QUERY_OPTIMIZATION))
        self.ingestion = normalise(read(INGESTION))

    def test_pre_26_2_mv_dedup_is_version_split(self) -> None:
        # 00510_materizlized_view_and_deduplication_zookeeper: MV count 2 on
        # 24.6, 3 on 24.7+ (PR #61601); the default flipped to 1 in 26.2.
        documents = {
            "SKILL.md": self.skill,
            "distributed-and-replication.md": self.replication,
            "community-gotchas.md": self.gotchas,
        }
        for name, plain in documents.items():
            with self.subTest(document=name):
                self.assertIn("24.7-25.12", plain)
                self.assertIn("pushed through every MV", plain)
                self.assertIn("24.6", plain)
                self.assertIn("skipped by every MV", plain)
                self.assertIn("26.2", plain)
                self.assertRegex(plain, r"24\.7-25\.12[^|]*?doubl")
                self.assertRegex(plain, r"24\.6[^|]*?(?:skipped by every MV|stay lost|missing)")

    def test_live_server_guard_covers_executing_reads(self) -> None:
        section = self.skill.split("## 0. Live servers", 1)[1]
        section = section.split("## 1.", 1)[0]
        self.assertIn("`EXPLAIN ANALYZE` executes the query", section)
        self.assertIn("stream_like_engine_allow_direct_select", section)
        self.assertIn("explicit yes", section)
        self.assertNotIn("(they only wait)", section)

    def test_read_only_ladder_has_conditions(self) -> None:
        self.assertIn("Keeper metadata is lost", self.replication)
        self.assertIn("cannot drop the local replica", self.replication)
        self.assertIn("SYSTEM DROP REPLICA 'r' FROM TABLE db.t", self.replication)
        for name, plain in (
            ("SKILL.md", self.skill),
            ("distributed-and-replication.md", self.replication),
        ):
            with self.subTest(document=name):
                self.assertIn("last replica holding good data", plain)

    def test_changed_defaults_are_versioned(self) -> None:
        self.assertIn("10000 blocks since 25.9", self.skill)
        self.assertIn("3600 since 25.10", self.skill)
        self.assertIn("ie_join` since 26.8", self.query)
        self.assertIn("defer_partition_pruning_after_final", self.query)
        self.assertIn("defer_partition_pruning_after_final", self.skill)
        self.assertIn("Since **26.8** the default is `0`", self.skill)
        self.assertIn("Since 26.8 default `0`", self.ingestion)
        for name, plain in (("SKILL.md", self.skill), ("ingestion", self.ingestion)):
            with self.subTest(document=name):
                self.assertNotIn("8-16 for bulk loads. |", plain)
                self.assertNotIn("introduced 25.7", plain)
                self.assertNotIn("an ack means durable", plain)
                self.assertNotIn("the data is durable", plain)


if __name__ == "__main__":
    unittest.main()
