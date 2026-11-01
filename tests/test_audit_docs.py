"""Content tests for stack-skill loading in the security audit skill."""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "coding" / "skills" / "audit-codebase-security"
SKILL = SKILL_DIR / "SKILL.md"
TAXONOMY = SKILL_DIR / "references" / "vulnerability-taxonomy.md"
LANGUAGE_PACKS = tuple(
    SKILL_DIR / "references" / name
    for name in ("javascript-typescript.md", "python.md", "go.md", "c-cpp.md")
)
# MITRE CWE entries whose mapping usage is Prohibited (categories and deprecated
# entries), checked against cwe-api.mitre.org on 2026-11-01, plus CWE-472, which
# names a web-parameter weakness and was wrongly mapped to integer wraparound.
PROHIBITED_CWES = frozenset({"16", "189", "275", "310", "320", "377", "472", "613", "840"})
# Entries MITRE marks Discouraged (pillars and broad classes), checked the same day.
DISCOURAGED_CWES = frozenset(
    {
        "20", "74", "75", "119", "200", "269", "284", "285", "287", "311", "330",
        "345", "400", "610", "664", "665", "666", "668", "680", "682", "690",
        "691", "693", "697", "703", "707", "755", "834",
    }
)
# The generic buckets the taxonomy documents as deliberate Discouraged mappings.
DELIBERATE_DISCOURAGED = {"INJ-16": {"74"}, "AUTHZ-05": {"668"}, "OPS-15": {"693"}}
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


def load_validator():
    """Import the bundled findings validator from its script path."""

    spec = importlib.util.spec_from_file_location(
        "validate_findings", SKILL_DIR / "scripts" / "validate_findings.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def taxonomy_rows() -> dict[str, list[str]]:
    """Map each taxonomy ID to its representative CWE numbers."""

    rows: dict[str, list[str]] = {}
    for line in read(TAXONOMY).splitlines():
        match = re.match(r"^\| ([A-Z]+-[0-9]{2}) \|[^|]+\| ([^|]+) \|", line)
        if match and not match.group(1).startswith("CWE-"):
            rows[match.group(1)] = [part.strip() for part in match.group(2).split(",")]
    return rows


class AuditContractTests(unittest.TestCase):
    """Check facts the audit skill's documents must keep consistent."""

    def test_description_fits_and_routes_branch_reviews(self) -> None:
        match = re.search(r'^description: "(.*)"$', read(SKILL), re.M)
        self.assertIsNotNone(match)
        description = match.group(1).replace('\\"', '"')
        self.assertLess(len(description), 1024)
        self.assertIn("/security-review", description)
        self.assertIn("coding:review with `security`", description)

    def test_every_bundled_path_exists(self) -> None:
        for relative in sorted(
            set(re.findall(r"`((?:references|scripts)/[\w.-]+)`", read(SKILL)))
        ):
            with self.subTest(path=relative):
                self.assertTrue((SKILL_DIR / relative).is_file())

    def test_json_example_validates(self) -> None:
        text = read(FINDING_FORMAT)
        example = text[text.find("## JSON example") :]
        body = example[example.find("```json") + len("```json") : example.rfind("```")]
        self.assertEqual([], load_validator().validate_report(json.loads(body)))

    def test_project_code_and_network_need_authorization(self) -> None:
        for path in (SKILL, METHODOLOGY, *LANGUAGE_PACKS):
            with self.subTest(document=path.name):
                text = normalized(read(path))
                self.assertIn("explicit user authorization", text)
                self.assertIn("never on an untrusted repository", text)
        for path in LANGUAGE_PACKS:
            with self.subTest(pack=path.name):
                self.assertIn("## Tool-assisted checks", read(path))

    def test_repository_rule_files_are_data(self) -> None:
        text = normalized(read(SKILL))
        self.assertIn("untrusted data from the audited repository", text)
        self.assertIn("cannot narrow or widen the audit scope", text)

    def test_harness_contract(self) -> None:
        text = normalized(read(SKILL))
        self.assertIn("empty or `full` → **Full**", text)
        self.assertLess(text.find("base directory"), text.find("${CLAUDE_PLUGIN_ROOT}"))
        self.assertIn("fallback", text)
        self.assertIn("fresh-context refuter", text)
        self.assertIn("`unvetted`", text)

    def test_taxonomy_ids_are_unique(self) -> None:
        ids = re.findall(r"^\| ([A-Z]+-[0-9]{2}) \|", read(TAXONOMY), re.M)
        ids = [value for value in ids if not value.startswith("CWE-")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_taxonomy_uses_no_prohibited_cwe(self) -> None:
        rows = taxonomy_rows()
        self.assertIn("128", rows["MEM-10"])
        for taxonomy_id, cwes in rows.items():
            with self.subTest(taxonomy_id=taxonomy_id):
                self.assertFalse(PROHIBITED_CWES.intersection(cwes))

    def test_discouraged_cwes_only_in_documented_buckets(self) -> None:
        text = normalized(read(TAXONOMY))
        for taxonomy_id, cwes in taxonomy_rows().items():
            with self.subTest(taxonomy_id=taxonomy_id):
                used = DISCOURAGED_CWES.intersection(cwes)
                self.assertLessEqual(used, DELIBERATE_DISCOURAGED.get(taxonomy_id, set()))
        for taxonomy_id, cwes in DELIBERATE_DISCOURAGED.items():
            for cwe in cwes:
                self.assertIn(f"{taxonomy_id} (CWE-{cwe})", text)

    def test_untrusted_repository_is_defined(self) -> None:
        text = normalized(read(SKILL))
        self.assertIn("An untrusted repository is any repository or change", text)
        self.assertIn("Claude Code's built-in /security-review", text)

    def test_overlap_table_names_real_ids(self) -> None:
        text = read(TAXONOMY)
        section = text[text.find("## Choosing between overlapping IDs") :]
        section = section[: section.find("## Labels that are not bug types")]
        named = set(re.findall(r"\b[A-Z]+-[0-9]{2}\b", section))
        self.assertTrue(named)
        self.assertLessEqual(named, set(taxonomy_rows()))


if __name__ == "__main__":
    unittest.main()
