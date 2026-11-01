"""Static tests for the trigger eval cases.

These tests catch schema, naming, and fixture drift before any paid eval run.
They do not prove that a model fires a skill; only the live eval does that.
"""

from __future__ import annotations

import re
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
CODING_EVALS = REPO_ROOT / "coding" / "evals"
REVIEW_CASE = CODING_EVALS / "review-fires-on-security-review"
REVIEW_PROSE = CODING_EVALS / "review-silent-on-prose"
REVIEW_READ_ONLY = CODING_EVALS / "review-stays-read-only"
REVIEW_COMMENT = CODING_EVALS / "review-comment-without-pr-does-not-post"

NEW_CASES = (
    BLUEPRINT_FIXTURE,
    BLUEPRINT_NEGATIVE,
    CLICKHOUSE_CASE,
    REVIEW_CASE,
    REVIEW_PROSE,
    REVIEW_READ_ONLY,
    REVIEW_COMMENT,
)

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


def skill_call_matches(grader: dict, skill: str) -> bool:
    """Whether the grader's input_match matches a Skill call for `skill`."""

    return bool(re.search(grader["input_match"], f'{{"skill": "{skill}"}}'))


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
    """A security-only review selects review, not audit, improve or the built-in."""

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

        # The built-in /security-review is the most likely mis-route.
        security = by_name.get("does-not-fire-security-review")
        self.assertIsNotNone(security, "missing the built-in security-review negative")
        self.assertEqual(security.get("max"), 0)
        self.assertTrue(skill_call_matches(security, "security-review"))
        self.assertFalse(skill_call_matches(security, "coding:review"))

    def test_review_positives_match_only_this_plugin(self) -> None:
        for case_dir in sorted(CODING_EVALS.glob("review*/")):
            case = load_case(case_dir)
            for grader in graders(case):
                if not grader.get("min"):
                    continue
                if grader.get("tool") != "Skill":
                    continue
                skill = grader["name"].removeprefix("fires-")
                with self.subTest(case=case_dir.name, grader=grader["name"]):
                    self.assertTrue(skill_call_matches(grader, f"coding:{skill}"))
                    self.assertTrue(skill_call_matches(grader, skill))
                    self.assertFalse(skill_call_matches(grader, f"other:{skill}"))

    def test_review_fires_cases_exclude_the_builtin_code_review(self) -> None:
        for name in ("review-fires-on-branch", "review-fires-on-pr-number"):
            by_name = {g.get("name"): g for g in graders(load_case(CODING_EVALS / name))}
            with self.subTest(case=name):
                grader = by_name.get("does-not-fire-code-review")
                self.assertIsNotNone(grader)
                self.assertEqual(grader.get("max"), 0)
                self.assertTrue(skill_call_matches(grader, "code-review"))


class ReviewBehaviourTests(unittest.TestCase):
    """Silent and behavioural review cases cannot pass vacuously."""

    def test_prose_case_has_a_document_and_requires_engagement(self) -> None:
        case = load_case(REVIEW_PROSE)
        self.assertEqual(case["context"]["scaffold_script"], "place-files.sh")
        self.assertTrue((REVIEW_PROSE / "files" / "docs" / "rollout-plan.md").is_file())
        by_name = {g.get("name"): g for g in graders(case)}
        self.assertEqual(by_name["reads-the-document"].get("min"), 1)
        self.assertEqual(by_name["gives-prose-feedback"].get("type"), "llm")

    def test_behaviour_cases_scaffold_a_branch_with_a_bug(self) -> None:
        for case_dir in (REVIEW_READ_ONLY, REVIEW_COMMENT):
            with self.subTest(case=case_dir.name):
                case = load_case(case_dir)
                self.assertEqual(case["context"]["scaffold_script"], "place-files.sh")
                self.assertIn("Bash", case["execution"]["allowed_tools"])
                script = read(case_dir / "place-files.sh")
                self.assertIn("git remote add origin", script)
                self.assertIn("git checkout -qb fix-last-page", script)
                head = read(case_dir / "files" / "head" / "pager.py")
                self.assertIn("return len(items) // size + 1", head)
                self.assertTrue(
                    any(g.get("type") == "llm" for g in graders(case)),
                    "expected an llm grader on the final message",
                )

    def test_read_only_case_forbids_edits(self) -> None:
        case = load_case(REVIEW_READ_ONLY)
        self.assertIn("fix", read(REVIEW_READ_ONLY / "prompt.md"))
        self.assertIn("Edit", case["execution"]["allowed_tools"])
        by_name = {g.get("name"): g for g in graders(case)}
        for name, tool in (
            ("does-not-edit-code", "Edit"),
            ("does-not-write-code", "Write"),
            ("does-not-mutate-git", "Bash"),
        ):
            with self.subTest(grader=name):
                self.assertEqual(by_name[name].get("tool"), tool)
                self.assertEqual(by_name[name].get("max"), 0)
        git = re.compile(by_name["does-not-mutate-git"]["input_match"])
        self.assertTrue(git.search("git commit -qm fix"))
        self.assertFalse(git.search("git worktree add /tmp/x abc123"))

    def test_comment_case_forbids_posting(self) -> None:
        case = load_case(REVIEW_COMMENT)
        self.assertIn("--comment", read(REVIEW_COMMENT / "prompt.md"))
        by_name = {g.get("name"): g for g in graders(case)}
        post = by_name["does-not-post"]
        self.assertEqual(post.get("max"), 0)
        pattern = re.compile(post["input_match"])
        for write in (
            "gh pr comment 7 --body-file /tmp/r.md",
            "gh api -X POST repos/o/r/issues/7/comments",
            "gh api repos/o/r/issues/7/comments -f body=x",
            'python3 "/s/_shared/forge.py" pr comment 7 --body-file /tmp/r.md',
        ):
            with self.subTest(write=write):
                self.assertTrue(pattern.search(write))
        for read_call in ("gh pr view 7", "gh api repos/o/r --jq .visibility"):
            with self.subTest(read=read_call):
                self.assertFalse(pattern.search(read_call))


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

    def test_every_case_runs_twice(self) -> None:
        case_dirs = sorted(path.parent for path in REPO_ROOT.glob("*/evals/*/case.yaml"))
        self.assertTrue(set(NEW_CASES) <= set(case_dirs))
        for case_dir in case_dirs:
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
