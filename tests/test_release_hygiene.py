"""Static release-hygiene checks for the plugin collection.

These tests read repository files only; they do not invoke the Claude CLI or any
paid eval. They guard the license, CI workflow, eval protocol, dead-manifest
removal, skill frontmatter, local links and plugin/skill versions.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSE = REPO_ROOT / "LICENSE.md"
README = REPO_ROOT / "README.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skills.yml"
OPENAI_MANIFEST = (
    REPO_ROOT
    / "coding"
    / "skills"
    / "audit-codebase-security"
    / "agents"
    / "openai.yaml"
)
SKILL_FILES = sorted(REPO_ROOT.glob("*/skills/*/SKILL.md"))

PLUGIN_MANIFESTS = {
    "blueprint": REPO_ROOT / "blueprint" / ".claude-plugin" / "plugin.json",
    "clickhouse": REPO_ROOT / "clickhouse" / ".claude-plugin" / "plugin.json",
    "coding": REPO_ROOT / "coding" / ".claude-plugin" / "plugin.json",
}

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read(path: Path) -> str:
    """Return file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def normalized(text: str) -> str:
    """Collapse whitespace runs so wrapped prose can be matched."""

    return " ".join(text.split())


def workflow_runs() -> list[str]:
    """Return every step's ``run:`` value from the CI workflow."""

    workflow = yaml.safe_load(read(WORKFLOW))
    runs = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if run is not None:
                runs.append(str(run))
    return runs


def frontmatter(path: Path) -> dict[str, str]:
    """Return the simple scalar frontmatter keys of a SKILL.md file."""

    text = read(path)
    if not text.startswith("---"):
        raise AssertionError(f"{path}: missing frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AssertionError(f"{path}: unterminated frontmatter")
    values: dict[str, str] = {}
    current_key: str | None = None
    for line in parts[1].splitlines():
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            current_key = match.group(1)
            values[current_key] = match.group(2).strip()
        elif current_key and line.startswith((" ", "\t")):
            values[current_key] += " " + line.strip()
    return values


def unquote(value: str) -> str:
    """Unescape a JSON/YAML quoted or plain frontmatter scalar."""

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


class ReleaseHygieneTests(unittest.TestCase):
    """Assert the release artifacts and protocol exist and stay consistent."""

    def test_root_license_exists(self) -> None:
        self.assertTrue(LICENSE.is_file(), "LICENSE.md is missing")
        text = read(LICENSE)
        self.assertIn("MIT License", text)
        self.assertIn("\nCopyright (c) 2026 Blackshield\n", text)
        self.assertIn("Copyright (c) 2026 shadcn (original work)", text)
        self.assertIn("Copyright (c) 2026 Blackshield (modifications)", text)

    def test_plugin_manifests_declare_mit(self) -> None:
        for name, manifest in PLUGIN_MANIFESTS.items():
            with self.subTest(plugin=name):
                data = json.loads(read(manifest))
                self.assertEqual(data.get("license"), "MIT")

    def test_readme_documents_license(self) -> None:
        text = normalized(read(README))
        section = text.split("## License", 1)
        self.assertEqual(len(section), 2, "README has no `## License` section")
        body = section[1]
        for needle in ("MIT", "shadcn", "Blackshield", "LICENSE.md"):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)

    def test_ci_workflow_runs_docs_tests(self) -> None:
        self.assertTrue(WORKFLOW.is_file(), ".github/workflows/skills.yml is missing")
        commands = workflow_runs()
        self.assertTrue(
            any(
                "python3 -m unittest discover -s tests -p 'test_*.py'"
                in command
                for command in commands
            ),
            f"no CI step runs the documentation tests: {commands}",
        )

    def test_ci_workflow_runs_resolve_selftest(self) -> None:
        commands = workflow_runs()
        self.assertTrue(
            any(
                "coding/skills/resolve-issue/scripts/selftest.sh" in command
                and command.strip().startswith("bash ")
                for command in commands
            ),
            f"no CI step runs the resolve-issue selftest: {commands}",
        )

    def test_ci_workflow_runs_audit_tests(self) -> None:
        commands = workflow_runs()
        self.assertTrue(
            any(
                "python3 -m unittest discover -s "
                "coding/skills/audit-codebase-security/scripts" in command
                for command in commands
            ),
            f"no CI step runs the audit script tests: {commands}",
        )

    def test_dead_openai_manifest_is_gone(self) -> None:
        self.assertFalse(
            OPENAI_MANIFEST.exists(),
            "coding/skills/audit-codebase-security/agents/openai.yaml still exists",
        )

    def test_readme_defines_release_gate(self) -> None:
        text = normalized(read(README))
        self.assertIn("Release gate", text)
        self.assertIn("--runs 2", text)
        self.assertIn("current plugin version", text)

    def test_skill_names_match_dirs_and_descriptions_fit(self) -> None:
        self.assertTrue(SKILL_FILES, "no SKILL.md files found")
        for skill in SKILL_FILES:
            with self.subTest(skill=str(skill.relative_to(REPO_ROOT))):
                values = frontmatter(skill)
                self.assertEqual(
                    values.get("name"),
                    skill.parent.name,
                    "frontmatter name does not match the skill directory",
                )
                description = unquote(values.get("description", ""))
                self.assertTrue(description, "missing description")
                self.assertLessEqual(
                    len(description),
                    1024,
                    f"description is {len(description)} characters (max 1024)",
                )

    def test_reference_links_resolve(self) -> None:
        reference_re = re.compile(r"`(references/[^`\s]+\.md)`")
        link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        for skill in SKILL_FILES:
            with self.subTest(skill=str(skill.relative_to(REPO_ROOT))):
                text = read(skill)
                for relative in reference_re.findall(text):
                    target = skill.parent / relative
                    self.assertTrue(
                        target.is_file(),
                        f"missing reference file: {target}",
                    )
                for raw_target in link_re.findall(text):
                    target = raw_target.split("#", 1)[0].strip()
                    if not target or target.startswith(
                        ("http://", "https://", "mailto:", "#")
                    ):
                        continue
                    resolved = (skill.parent / target).resolve()
                    self.assertTrue(
                        resolved.exists(),
                        f"broken local Markdown link: {raw_target}",
                    )

    def test_release_versions_are_consistent(self) -> None:
        for plugin in ("blueprint", "clickhouse"):
            with self.subTest(plugin=plugin):
                manifest = json.loads(read(PLUGIN_MANIFESTS[plugin]))
                plugin_version = manifest["version"]
                skill = REPO_ROOT / plugin / "skills" / plugin / "SKILL.md"
                skill_version = frontmatter(skill).get("version")
                self.assertTrue(VERSION_RE.match(plugin_version))
                self.assertEqual(plugin_version, skill_version)

        coding_manifest = json.loads(read(PLUGIN_MANIFESTS["coding"]))
        self.assertTrue(
            VERSION_RE.match(coding_manifest["version"]),
            "coding plugin version is not a valid version string",
        )
        for skill in sorted((REPO_ROOT / "coding" / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=str(skill.relative_to(REPO_ROOT))):
                version = frontmatter(skill).get("version", "")
                self.assertTrue(
                    VERSION_RE.match(version),
                    f"missing or invalid `version:` in {skill}",
                )

    def test_release_versions_are_pinned(self) -> None:
        coding_manifest = json.loads(read(PLUGIN_MANIFESTS["coding"]))
        self.assertEqual(
            coding_manifest["version"],
            "0.18.0",
            "coding plugin version changed; update the release with the tests",
        )
        review = REPO_ROOT / "coding" / "skills" / "review" / "SKILL.md"
        self.assertEqual(
            frontmatter(review).get("version"),
            "0.7.0",
            "review version changed; update the release with the tests",
        )
        reconcile = REPO_ROOT / "coding" / "skills" / "reconcile-plans" / "SKILL.md"
        self.assertEqual(
            frontmatter(reconcile).get("version"),
            "0.2.0",
            "reconcile-plans version changed; update the release with the tests",
        )
        improve = REPO_ROOT / "coding" / "skills" / "improve" / "SKILL.md"
        self.assertEqual(
            frontmatter(improve).get("version"),
            "0.3.0",
            "improve version changed; update the release with the tests",
        )
        file_issue = REPO_ROOT / "coding" / "skills" / "file-issue" / "SKILL.md"
        self.assertEqual(
            frontmatter(file_issue).get("version"),
            "0.6.0",
            "file-issue version changed; update the release with the tests",
        )

    def test_license_scope_is_present(self) -> None:
        self.assertIn(
            "coding/skills/improve, review-plan, execute-plan and reconcile-plans are "
            "adapted from shadcn/improve",
            normalized(read(LICENSE)),
        )
        text = read(README)
        section = text.split("## License", 1)
        self.assertEqual(len(section), 2, "README has no `## License` section")
        body = normalized(section[1]).lower().replace("`", "")
        self.assertIn(
            "coding/skills/improve, review-plan, execute-plan and reconcile-plans are adapted",
            body,
        )
        self.assertIn("the other skills are blackshield's own work", body)
        self.assertNotIn("original notice is retained", body)


if __name__ == "__main__":
    unittest.main()
