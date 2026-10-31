"""Content tests for stack-skill loading in the security audit skill."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "coding" / "skills" / "audit-codebase-security" / "SKILL.md"
METHODOLOGY = (
    REPO_ROOT
    / "coding"
    / "skills"
    / "audit-codebase-security"
    / "references"
    / "audit-methodology.md"
)
FINDING_FORMAT = (
    REPO_ROOT
    / "coding"
    / "skills"
    / "audit-codebase-security"
    / "references"
    / "finding-format.md"
)


def read(path: Path) -> str:
    """Return the file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def normalized(text: str) -> str:
    """Collapse all whitespace runs so wrapped prose can be matched."""

    return " ".join(text.split())


class AuditStackSkillTests(unittest.TestCase):
    """Assert the audit skill loads matching stack skills and reports them."""

    def setUp(self) -> None:
        self.skill = read(SKILL)
        self.methodology = read(METHODOLOGY)
        self.finding_format = read(FINDING_FORMAT)
        self.skill_flat = normalized(self.skill)
        self.methodology_flat = normalized(self.methodology)
        self.finding_format_flat = normalized(self.finding_format)

    def test_audit_loads_stack_skills(self) -> None:
        self.assertIn("Load the skills that match the stack", self.skill_flat)
        self.assertIn("invoke every available skill that matches", self.skill_flat)

    def test_audit_reads_manifests(self) -> None:
        for manifest in ("go.mod", "package.json", "pyproject.toml", "requirements.txt"):
            with self.subTest(manifest=manifest):
                self.assertIn(manifest, self.skill_flat)

    def test_loaded_skills_are_in_coverage(self) -> None:
        for name, text in (
            ("SKILL.md", self.skill_flat),
            ("audit-methodology.md", self.methodology_flat),
            ("finding-format.md", self.finding_format_flat),
        ):
            with self.subTest(document=name):
                self.assertIn("loaded skills", text)

        self.assertIn("which skills you loaded", self.skill_flat)
        self.assertIn("Stack skills loaded:", self.finding_format_flat)
        self.assertIn("`loaded_skills` | array of strings", self.finding_format_flat)

    def test_none_available_is_normal(self) -> None:
        self.assertIn('"none available" is a normal answer', self.skill_flat)

    def test_read_only_precedence(self) -> None:
        marker = self.skill_flat.find("invoke every available skill that matches")
        self.assertNotEqual(marker, -1, "stack-skill instruction is missing")
        instruction = self.skill_flat[marker : marker + 900]
        self.assertIn("do not override", instruction)
        self.assertIn("read-only", instruction)

    def test_scope_aware_loading(self) -> None:
        self.assertIn("For a targeted or diff audit", self.skill_flat)
        self.assertIn("not every skill in the repository", self.skill_flat)

    def test_json_example_has_loaded_skills(self) -> None:
        example = self.finding_format[self.finding_format.find("## JSON example") :]
        self.assertIn('"loaded_skills"', example)


if __name__ == "__main__":
    unittest.main()
