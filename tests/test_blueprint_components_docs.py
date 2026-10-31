"""Content tests for the Blueprint components reference's TaskRunner counter."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "blueprint" / "skills" / "blueprint" / "SKILL.md"
COMPONENTS = (
    REPO_ROOT
    / "blueprint"
    / "skills"
    / "blueprint"
    / "references"
    / "components.md"
)
REFERENCES = COMPONENTS.parent


def read(path: Path) -> str:
    """Return the file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def task_runner_queue_block(text: str) -> str:
    """Return the TaskRunner.Queue function, ending at its column-0 brace."""

    marker = "func (t *TaskRunner) Queue"
    start = text.index(marker)
    end = start
    for line in text[start:].splitlines(keepends=True):
        end += len(line)
        if line.rstrip() == "}":
            return text[start:end]
    raise AssertionError("TaskRunner.Queue has no closing brace at column 0")


class BlueprintTaskRunnerCounterTests(unittest.TestCase):
    """Assert TaskRunner rolls back its in-flight counter on dispatch failure."""

    def setUp(self) -> None:
        self.text = read(COMPONENTS)
        self.block = task_runner_queue_block(self.text)

    def test_task_runner_rolls_back_on_dispatch_error(self) -> None:
        self.assertIn(
            "if err := t.workerPool.Queue(ctx, wrapFn); err != nil", self.block
        )
        self.assertIn("t.runningTasks.Add(-1)", self.block)
        self.assertIn("return err", self.block)

    def test_counter_is_incremented_before_dispatch(self) -> None:
        increment = self.block.index("t.runningTasks.Add(1)")
        dispatch = self.block.index("t.workerPool.Queue(ctx, wrapFn)")
        rollback = self.block.index("t.runningTasks.Add(-1)", dispatch)
        self.assertLess(increment, dispatch)
        self.assertLess(dispatch, rollback)

    def test_dispatch_error_is_explained(self) -> None:
        self.assertIn("DispatchWithContext", self.text)
        self.assertIn("context is canceled", self.text)
        self.assertIn("pool is stopped", self.text)
        self.assertIn("job never runs", self.text)

    def test_reference_links_resolve(self) -> None:
        linked = set(re.findall(r"`(references/[^`]+\.md)`", read(SKILL)))
        self.assertTrue(linked, "SKILL.md links no reference files")
        for link in sorted(linked):
            with self.subTest(reference=link):
                self.assertTrue((REFERENCES.parent / link).is_file(), link)

        present = {f"references/{path.name}" for path in REFERENCES.glob("*.md")}
        with self.subTest(check="every reference is linked"):
            self.assertEqual(sorted(present - linked), [], "unlinked reference file")


if __name__ == "__main__":
    unittest.main()
