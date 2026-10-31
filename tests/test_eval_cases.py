"""Static tests for the trigger eval cases.

These tests catch schema, naming, and fixture drift before any paid eval run.
They do not prove that a model fires a skill; only the live eval does that.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

BLUEPRINT_FIXTURE = REPO_ROOT / "blueprint" / "evals" / "blueprint-fires-on-import-no-name"
BLUEPRINT_NEGATIVE = (
    REPO_ROOT / "blueprint" / "evals" / "blueprint-silent-on-other-go-framework"
)
CLICKHOUSE_CASE = (
    REPO_ROOT / "clickhouse" / "evals" / "clickhouse-fires-on-merge-tree-no-name"
)
REVIEW_CASE = REPO_ROOT / "coding" / "evals" / "review-fires-on-security-review"

NEW_CASES = (BLUEPRINT_FIXTURE, BLUEPRINT_NEGATIVE, CLICKHOUSE_CASE, REVIEW_CASE)

BLUEPRINT_IMPORT = "github.com/oddbit-project/blueprint"


def read(path: Path) -> str:
    """Return the file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def load_case(case_dir: Path) -> dict:
    """Parse a case's case.yaml."""

    return yaml.safe_load(read(case_dir / "case.yaml"))


def grader_names(case: dict) -> list[str]:
    """Return the case's grader names."""

    return [grader.get("name", "") for grader in case.get("graders", [])]


def graders(case: dict) -> list[dict]:
    """Return the case's grader entries."""

    return case.get("graders", [])


class BlueprintFixtureTests(unittest.TestCase):
    """The product-name-free import case tests a real Blueprint dependency."""

    def test_blueprint_fixture_has_real_import(self) -> None:
        self.assertTrue(
            BLUEPRINT_FIXTURE.is_dir(), f"missing case directory: {BLUEPRINT_FIXTURE}"
        )
        go_mod = read(BLUEPRINT_FIXTURE / "files" / "go.mod")
        main_go = read(BLUEPRINT_FIXTURE / "files" / "cmd" / "service" / "main.go")
        prompt = read(BLUEPRINT_FIXTURE / "prompt.md")

        self.assertIn(BLUEPRINT_IMPORT, go_mod)
        self.assertIn(BLUEPRINT_IMPORT, main_go)
        self.assertNotIn("Blueprint", prompt)
        self.assertNotIn("blueprint", prompt)

    def test_blueprint_case_has_scaffold_and_llm_grader(self) -> None:
        case = load_case(BLUEPRINT_FIXTURE)

        self.assertEqual(case["context"]["scaffold_script"], "place-files.sh")
        script = read(BLUEPRINT_FIXTURE / "place-files.sh")
        self.assertIn("cp -R", script)

        llm_graders = [grader for grader in graders(case) if grader.get("type") == "llm"]
        self.assertTrue(llm_graders, "expected at least one llm grader")
        self.assertTrue(
            any(
                grader.get("focus") == "last_message"
                and "framework" in grader.get("criteria", "")
                for grader in llm_graders
            ),
            "expected an llm grader with focus last_message and a criteria "
            "that mentions the framework",
        )

        tool_graders = [
            grader for grader in graders(case) if grader.get("type") == "tool_used"
        ]
        self.assertTrue(
            any(grader.get("min") == 1 for grader in tool_graders),
            "expected a tool_used grader with min: 1",
        )

    def test_blueprint_hard_negative_exists(self) -> None:
        case = load_case(BLUEPRINT_NEGATIVE)
        go_mod = read(BLUEPRINT_NEGATIVE / "files" / "go.mod")

        self.assertIn("gin-gonic/gin", go_mod)
        self.assertNotIn("oddbit-project/blueprint", go_mod)
        self.assertTrue(
            any(grader.get("max") == 0 for grader in graders(case)),
            "expected a silence grader with max: 0",
        )


class ClickHouseCaseTests(unittest.TestCase):
    """The MergeTree case tests the trigger without the product name."""

    def test_clickhouse_prompt_names_no_product(self) -> None:
        prompt = read(CLICKHOUSE_CASE / "prompt.md")

        self.assertIn("MergeTree", prompt)
        self.assertIn("ORDER BY", prompt)
        self.assertIn("40 seconds", prompt)
        self.assertNotIn("clickhouse", prompt.lower())

    def test_clickhouse_case_has_quality_grader(self) -> None:
        case = load_case(CLICKHOUSE_CASE)

        self.assertTrue(
            any(
                grader.get("type") == "llm"
                and grader.get("focus") == "last_message"
                and "sorting key" in grader.get("criteria", "")
                and "granules" in grader.get("criteria", "")
                for grader in graders(case)
            ),
            "expected an llm grader that grades the sorting-key/granule explanation",
        )


class ReviewRoutingTests(unittest.TestCase):
    """A security-only review selects review, not audit or improve."""

    def test_review_security_case_prefers_review(self) -> None:
        case = load_case(REVIEW_CASE)
        by_name = {grader.get("name"): grader for grader in graders(case)}

        self.assertIn("fires-review", by_name)
        self.assertEqual(by_name["fires-review"].get("min"), 1)

        for negative in (
            "does-not-fire-audit-codebase-security",
            "does-not-fire-improve",
        ):
            with self.subTest(negative=negative):
                self.assertIn(negative, by_name)
                self.assertEqual(by_name[negative].get("max"), 0)

        self.assertFalse(
            any("security-review" in name for name in by_name),
            "security-review is a built-in command, not a skill in this repo",
        )


class AllCasesTests(unittest.TestCase):
    """Every case in the repository is schema-loadable and consistently named."""

    def test_all_case_yaml_parses(self) -> None:
        case_files = sorted(REPO_ROOT.glob("*/evals/*/case.yaml"))
        self.assertTrue(case_files, "no eval cases found")

        for case_file in case_files:
            case_dir = case_file.parent
            with self.subTest(case=str(case_dir.relative_to(REPO_ROOT))):
                case = yaml.safe_load(case_file.read_text(encoding="utf-8"))
                self.assertIsInstance(case, dict)
                self.assertIsInstance(case.get("schema_version"), str)
                self.assertEqual(case.get("name"), case_dir.name)

                names = grader_names(case)
                self.assertEqual(
                    len(names), len(set(names)), f"duplicate grader names: {names}"
                )

                execution_prompt = (case.get("execution") or {}).get("prompt") or ""
                self.assertTrue(
                    (case_dir / "prompt.md").is_file() or execution_prompt,
                    "case needs prompt.md or execution.prompt",
                )

    def test_new_cases_run_twice(self) -> None:
        for case_dir in NEW_CASES:
            with self.subTest(case=case_dir.name):
                case = load_case(case_dir)
                self.assertEqual(
                    case.get("runs"),
                    2,
                    f"{case_dir.name} must use the release protocol --runs 2",
                )
                self.assertIsInstance(case.get("runs"), int)


if __name__ == "__main__":
    unittest.main()
