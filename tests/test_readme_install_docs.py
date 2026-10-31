"""Static checks for the OpenCode and Codex installation docs in README.md."""

import re
import unittest
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
TEXT = README.read_text(encoding="utf-8")


def section(name):
    """Return the body of the ``### <name>`` README section."""
    match = re.search(rf"^### {re.escape(name)}\s*$", TEXT, re.MULTILINE)
    if match is None:
        return ""
    rest = TEXT[match.end():]
    following = re.search(r"^#{2,3} ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def plain(text):
    """Drop inline-code backticks and collapse whitespace for phrase checks."""
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


def bash_blocks(text):
    return re.findall(r"```bash\n(.*?)```", text, re.DOTALL)


class ReadmeInstallDocsTest(unittest.TestCase):
    def test_sections_exist(self):
        headings = re.findall(
            r"^### (Claude Code|OpenCode|Codex)\s*$", TEXT, re.MULTILINE
        )
        for name in ("Claude Code", "OpenCode", "Codex"):
            with self.subTest(section=name):
                self.assertIn(name, headings)
        self.assertEqual(
            [name for name in headings if name in ("Claude Code", "OpenCode", "Codex")],
            ["Claude Code", "OpenCode", "Codex"],
        )

    def test_section_order(self):
        markers = [
            "### Claude Code",
            "claude --plugin-dir",
            "### Release gate",
            "### OpenCode",
        ]
        for earlier, later in zip(markers, markers[1:]):
            with self.subTest(earlier=earlier, later=later):
                self.assertLess(
                    TEXT.index(earlier),
                    TEXT.index(later),
                    f"{earlier!r} must come before {later!r}",
                )

    def test_opencode_paths(self):
        body = section("OpenCode")
        for path in (
            "~/.config/opencode/skills",
            ".opencode/skills",
            "~/.agents/skills",
            "~/.claude/skills",
        ):
            with self.subTest(path=path):
                self.assertIn(path, body)

    def test_codex_paths(self):
        body = section("Codex")
        self.assertIn("~/.agents/skills", body)
        # The repository path must be named on its own, not only as ~/.agents/skills
        # or as $PWD/.agents/skills.
        self.assertRegex(body, r"(?<!~/)(?<!\$PWD/)\.agents/skills")

        legacy_sentences = [
            sentence
            for sentence in re.split(r"(?<=[.!?])\s+", plain(body))
            if "~/.codex/skills" in sentence
        ]
        self.assertTrue(legacy_sentences, "~/.codex/skills is not mentioned")
        for sentence in legacy_sentences:
            with self.subTest(sentence=sentence):
                self.assertRegex(sentence.lower(), r"legacy|older")

    def test_install_is_idempotent(self):
        for name in ("OpenCode", "Codex"):
            with self.subTest(section=name):
                blocks = "\n".join(bash_blocks(section(name)))
                self.assertIn("mkdir -p", blocks)
                self.assertIn("ln -sfn", blocks)
                self.assertIn("test -r", blocks)
        opencode_blocks = "\n".join(bash_blocks(section("OpenCode")))
        self.assertIn('ln -sfn "$d"', opencode_blocks)

    def test_iteration_flags_are_documented(self):
        body = section("Claude Code")
        self.assertIn("--scaffold", body)
        self.assertIn("18 cases", body)

    def test_relative_reference_warning(self):
        body = plain(section("OpenCode"))
        self.assertIn("../improve/references", body)
        self.assertIn("copy the whole coding/skills tree", body)

    def test_opencode_invocation(self):
        body = plain(section("OpenCode"))
        self.assertIn("Use the blueprint skill", body)
        self.assertIn("@blueprint", body)
        self.assertIn("Do not rely on /blueprint", body)

    def test_opencode_loop_aborts(self):
        body = plain(section("OpenCode"))
        self.assertIn("loop aborts if a folder has no SKILL.md", body)
        self.assertNotIn("loop skips", body)

    def test_codex_invocation(self):
        body = plain(section("Codex"))
        for invocation in ("$blueprint", "$clickhouse", "$review", "/skills"):
            with self.subTest(invocation=invocation):
                self.assertIn(invocation, body)

    def test_shared_install_note(self):
        body = plain(section("Codex"))
        self.assertIn("both", body.lower())
        self.assertIn("~/.agents/skills", body)
        self.assertIn(
            "Codex shows duplicate names in its selector rather than merging them",
            body,
        )


if __name__ == "__main__":
    unittest.main()
