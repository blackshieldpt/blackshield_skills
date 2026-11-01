"""Tests for the bundled repository profiler and findings validator."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

import profile_repository
import validate_findings


def valid_report() -> dict[str, Any]:
    """Return a minimal valid machine-readable audit report."""

    return {
        "schema_version": 1,
        "scope": {
            "mode": "full",
            "root": ".",
            "target": None,
            "commit_range": None,
            "excluded": [],
        },
        "summary": {
            "finding_count": 1,
            "reviewed": ["HTTP routes"],
            "taxonomy_families": ["AUTHZ"],
            "loaded_skills": [],
            "limitations": [],
        },
        "findings": [
            {
                "id": "SEC-001",
                "title": "Object lookup is not tenant scoped",
                "taxonomy_id": "AUTHZ-02",
                "severity": "high",
                "confidence": "high",
                "locations": [
                    {
                        "path": "src/routes.ts",
                        "line": 42,
                        "symbol": "download",
                    }
                ],
                "claim": "A user can retrieve another tenant's object.",
                "evidence": ["The request ID reaches a global lookup."],
                "exploit_path": "Request another tenant's object ID.",
                "preconditions": "Any authenticated account.",
                "impact": "Cross-tenant data disclosure.",
                "remediation": "Scope the lookup to the authenticated tenant.",
                "validation": "Assert a cross-tenant request returns no object.",
                "vetting": "refuter",
            }
        ],
    }


class ProfileRepositoryTests(unittest.TestCase):
    """Exercise repository profiling and output rendering."""

    def test_detects_languages_frameworks_and_surfaces(self) -> None:
        """Detect representative JavaScript and infrastructure evidence."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / "node_modules" / "ignored").mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "electron": "1",
                            "express": "1",
                            "react": "1",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "src" / "server.ts").write_text(
                """
                import { exec } from "child_process";
                import jwt from "jsonwebtoken";
                app.get("/fetch", async (request) => {
                  const row = await db.query("SELECT * FROM users");
                  return fetch(request.query.url);
                });
                exec(userCommand);
                """,
                encoding="utf-8",
            )
            (root / "Dockerfile").write_text("FROM node:24\n", encoding="utf-8")
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "jobs: {}\n", encoding="utf-8"
            )
            (root / "node_modules" / "ignored" / "bad.js").write_text(
                "eval(input)", encoding="utf-8"
            )
            link = root / "linked-source"
            try:
                link.symlink_to(root / "src", target_is_directory=True)
            except OSError:
                pass

            profile = profile_repository.build_profile(root)
            data = profile.to_dict()

            self.assertIn({"name": "TypeScript", "files": 1}, data["languages"])
            framework_names = {item["name"] for item in data["frameworks"]}
            self.assertTrue({"Electron", "Express", "React"} <= framework_names)
            surface_names = {item["name"] for item in data["security_surfaces"]}
            self.assertTrue(
                {
                    "authentication_or_session",
                    "database_or_query",
                    "dynamic_execution_or_process",
                    "infrastructure_or_deployment",
                    "mobile_or_desktop",
                    "outbound_network",
                    "web_or_api",
                }
                <= surface_names
            )
            self.assertNotIn("MEM", data["recommended_taxonomy_families"])
            self.assertIn("OPS", data["recommended_taxonomy_families"])
            self.assertEqual(
                ["references/javascript-typescript.md"],
                data["recommended_reference_packs"],
            )
            self.assertNotIn(
                "node_modules/ignored/bad.js", data["entry_point_candidates"]
            )
            scan = data["scan"]
            self.assertIn("node_modules", scan["ignored_directories"])

            markdown = profile_repository.render_markdown(profile)
            self.assertIn("# Repository Security Profile", markdown)
            self.assertIn("Express", markdown)
            self.assertIn("Recommended reference packs", markdown)
            self.assertIn("references/javascript-typescript.md", markdown)
            self.assertIn("Recommended taxonomy families", markdown)

    def test_native_code_and_binary_prefix(self) -> None:
        """Detect native code and avoid treating binary data as text."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "main.c").write_text("int main(void) { return 0; }\n")
            (root / "worker.go").write_text("package worker\n")
            (root / "blob.bin").write_bytes(b"abc\x00def")

            profile = profile_repository.build_profile(root)
            data = profile.to_dict()
            surface_names = {item["name"] for item in data["security_surfaces"]}
            self.assertIn("native_or_unsafe_code", surface_names)
            self.assertEqual(
                ["references/c-cpp.md", "references/go.md"],
                data["recommended_reference_packs"],
            )
            text, bytes_read = profile_repository.read_text_prefix(root / "blob.bin")
            self.assertEqual("", text)
            self.assertEqual(7, bytes_read)

    def test_exclusion_limits_and_invalid_arguments(self) -> None:
        """Apply exclusions and reject unsafe or invalid arguments."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "included").mkdir()
            (root / "excluded").mkdir()
            (root / "included" / "app.py").write_text("print('ok')\n")
            (root / "excluded" / "secret.py").write_text("print('skip')\n")
            for index in range(4):
                (root / f"file-{index}.py").write_text("pass\n")

            profile = profile_repository.build_profile(
                root, excludes=("excluded",), max_files=2, content_budget=0
            )
            scan = profile.to_dict()["scan"]
            self.assertTrue(scan["truncated"])
            self.assertEqual(2, scan["files_scanned"])
            self.assertEqual(["excluded"], scan["explicit_excludes"])
            self.assertTrue(
                any("Content inspection reached" in item for item in profile.warnings)
            )

            with self.assertRaises(ValueError):
                profile_repository.normalize_excludes(("../outside",))
            with self.assertRaises(ValueError):
                profile_repository.build_profile(root, max_files=0)
            with self.assertRaises(ValueError):
                profile_repository.build_profile(root, content_budget=-1)
            with self.assertRaises(FileNotFoundError):
                profile_repository.build_profile(root / "missing")
            with self.assertRaises(NotADirectoryError):
                profile_repository.build_profile(root / "file-0.py")

    def test_exclude_keeps_leading_dot(self) -> None:
        """Keep dot-prefixed exclusions such as `.github` intact."""

        self.assertEqual(
            (".github", "src"),
            profile_repository.normalize_excludes((".github", "./src/", ".")),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text("jobs: {}\n")
            profile = profile_repository.build_profile(root, excludes=(".github",))
            self.assertEqual([], profile.infrastructure)

    def test_exclusion_removes_matching_files(self) -> None:
        """Drop excluded files from the profile while keeping their siblings."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for directory in ("included", "excluded"):
                (root / directory).mkdir()
                (root / directory / "main.py").write_text("print('x')\n")

            data = profile_repository.build_profile(
                root, excludes=("excluded",)
            ).to_dict()
            self.assertEqual(["included/main.py"], data["entry_point_candidates"])
            self.assertEqual(1, data["scan"]["files_scanned"])
            self.assertIn("excluded", data["scan"]["ignored_directories"])

    def test_non_utf8_filename_renders(self) -> None:
        """Render undecodable filename bytes as escapes instead of crashing."""

        if sys.platform in ("darwin", "win32"):
            self.skipTest("filesystem rejects non-UTF-8 filenames")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            try:
                with open(os.path.join(os.fsencode(root), b"main.\xff.py"), "wb") as handle:
                    handle.write(b"import subprocess\n")
            except OSError:
                self.skipTest("filesystem rejects non-UTF-8 filenames")

            profile = profile_repository.build_profile(root)
            json.dumps(profile.to_dict(), ensure_ascii=False).encode("utf-8")
            profile_repository.render_markdown(profile).encode("utf-8")
            self.assertEqual(["main.\\xff.py"], profile.entry_point_candidates)

    def test_newline_filename_cannot_forge_markdown(self) -> None:
        """Escape control characters so filenames stay on one Markdown line."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "routes").mkdir()
            try:
                (root / "routes" / "a\n## Injected\n- evil.py").write_text("x")
            except OSError:
                self.skipTest("filesystem rejects newlines in filenames")

            markdown = profile_repository.render_markdown(
                profile_repository.build_profile(root)
            )
            self.assertNotIn("\n## Injected", markdown)
            self.assertIn("routes/a\\n## Injected\\n- evil.py", markdown)

    def test_surface_patterns_skip_ordinary_code(self) -> None:
        """Do not flag ordinary functions or window.open as dangerous surfaces."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "ui.js").write_text(
                "const show = function (url) { window.open(url); };\n"
            )
            surfaces = {
                item["name"]
                for item in profile_repository.build_profile(root).security_surfaces
            }
            self.assertNotIn("dynamic_execution_or_process", surfaces)
            self.assertNotIn("filesystem_or_upload", surfaces)

            (root / "ui.js").write_text("const f = new Function(body);\n")
            (root / "store.py").write_text("data = open(path).read()\n")
            surfaces = {
                item["name"]
                for item in profile_repository.build_profile(root).security_surfaces
            }
            self.assertIn("dynamic_execution_or_process", surfaces)
            self.assertIn("filesystem_or_upload", surfaces)

    def test_surface_patterns_cover_qualified_sinks(self) -> None:
        """Detect qualified file APIs and Function() without matching bare words."""

        def surfaces(text: str) -> set[str]:
            return {
                name
                for name, patterns in profile_repository.SURFACE_PATTERNS.items()
                if any(pattern.search(text) for pattern in patterns)
            }

        for text in (
            "os.open(p, os.O_RDONLY)",
            "io.open(p)",
            "codecs.open(p)",
            "aiofiles.open(p)",
            "fs.open(p)",
            "File.open(p)",
            "Path(user).read_text()",
            "pathlib.Path(p).open()",
            "fs.readFileSync(p)",
        ):
            with self.subTest(text=text):
                self.assertIn("filesystem_or_upload", surfaces(text))
        for text in ("window.open(url)", "xhr.open('GET', u)", "return Path"):
            with self.subTest(text=text):
                self.assertNotIn("filesystem_or_upload", surfaces(text))
        self.assertIn(
            "dynamic_execution_or_process", surfaces("Function('return this')()")
        )
        self.assertNotIn(
            "dynamic_execution_or_process", surfaces("const f = function (x) {};")
        )

    def test_language_heuristics(self) -> None:
        """Classify Rust, .m, Android, and capped/skipped paths precisely."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "safe.rs").write_text("fn main() {}\n")
            (root / "model.m").write_text("% MATLAB script\nx = 1;\n")
            (root / "AndroidManifest.xml").write_text("<manifest/>\n")
            (root / "build").mkdir()
            (root / "build" / "Dockerfile").write_text("FROM scratch\n")
            (root / "routes").mkdir()
            for index in range(profile_repository.MAX_ENTRY_POINTS + 2):
                (root / "routes" / f"r{index}.txt").write_text("")

            profile = profile_repository.build_profile(root)
            surfaces = {item["name"] for item in profile.security_surfaces}
            self.assertNotIn("native_or_unsafe_code", surfaces)
            self.assertIn("mobile_or_desktop", surfaces)
            self.assertNotIn("references/c-cpp.md", profile.recommended_reference_packs)
            warnings = " ".join(profile.warnings)
            self.assertIn("2 more entry-point candidates", warnings)
            self.assertIn("build-output directories named build", warnings)
            self.assertIn("1 .m files", warnings)

            (root / "ffi.rs").write_text("unsafe { ptr.read() }\n")
            (root / "view.m").write_text("#import <Foundation/Foundation.h>\n")
            profile = profile_repository.build_profile(root)
            surfaces = {item["name"] for item in profile.security_surfaces}
            self.assertIn("native_or_unsafe_code", surfaces)
            self.assertIn("references/c-cpp.md", profile.recommended_reference_packs)

    def test_helpers_and_cli(self) -> None:
        """Cover helper classification and both CLI output modes."""

        self.assertTrue(profile_repository.is_manifest(Path("service.csproj")))
        self.assertTrue(profile_repository.is_manifest(Path("requirements-dev.txt")))
        self.assertTrue(
            profile_repository.is_infrastructure(Path("deploy.tf"), "infra/deploy.tf")
        )
        self.assertTrue(
            profile_repository.is_infrastructure(
                Path("ci.yml"), ".github/workflows/ci.yml"
            )
        )
        self.assertTrue(
            profile_repository.is_entry_point(Path("main.go"), "cmd/main.go")
        )
        self.assertTrue(
            profile_repository.is_entry_point(
                Path("users.ts"), "src/controllers/users.ts"
            )
        )
        self.assertTrue(profile_repository.is_excluded("a/b/c", ("a/b",)))
        self.assertFalse(profile_repository.is_excluded("a/c", ("a/b",)))

        evidence: dict[str, list[str]] = {}
        from collections import defaultdict

        bounded_evidence: dict[str, list[str]] = defaultdict(list)
        for index in range(profile_repository.MAX_EVIDENCE_PATHS + 4):
            profile_repository.append_evidence(
                bounded_evidence, "surface", f"file-{index}"
            )
        self.assertEqual(
            profile_repository.MAX_EVIDENCE_PATHS,
            len(bounded_evidence["surface"]),
        )
        self.assertEqual([], profile_repository.sorted_evidence_items(evidence))

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "main.py").write_text("print('hello')\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = profile_repository.main([str(root), "--format", "json"])
            self.assertEqual(0, exit_code)
            profile_json = json.loads(stdout.getvalue())
            self.assertEqual(1, profile_json["schema_version"])
            self.assertEqual(
                ["references/python.md"],
                profile_json["recommended_reference_packs"],
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = profile_repository.main([str(root), "--format", "markdown"])
            self.assertEqual(0, exit_code)
            self.assertIn("# Repository Security Profile", stdout.getvalue())

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = profile_repository.main([str(root / "missing")])
            self.assertEqual(2, exit_code)
            self.assertIn("error:", stderr.getvalue())


class ValidateFindingsTests(unittest.TestCase):
    """Exercise report validation and CLI behavior."""

    def test_valid_report(self) -> None:
        """Accept a complete valid report."""

        self.assertEqual([], validate_findings.validate_report(valid_report()))

    def test_invalid_report_fields(self) -> None:
        """Return useful errors for malformed report fields."""

        report = valid_report()
        report["schema_version"] = 2
        report["scope"] = {
            "mode": "unknown",
            "root": "",
            "target": 7,
            "commit_range": [],
            "excluded": ["", 1],
        }
        report["summary"] = {
            "finding_count": 2,
            "reviewed": "routes",
            "taxonomy_families": [""],
            "limitations": None,
        }
        finding = report["findings"][0]
        finding.update(
            {
                "id": "bad",
                "title": "",
                "taxonomy_id": "CWE-79",
                "severity": "urgent",
                "confidence": "low",
                "locations": [
                    {"path": "", "line": 0, "symbol": 3},
                    "not-an-object",
                ],
                "claim": None,
                "evidence": [],
                "exploit_path": "",
                "preconditions": "",
                "impact": "",
                "remediation": "",
                "validation": "",
            }
        )
        errors = validate_findings.validate_report(report)
        self.assertGreater(len(errors), 15)
        self.assertTrue(any("schema_version" in error for error in errors))
        self.assertTrue(any("finding_count" in error for error in errors))
        self.assertTrue(any("taxonomy_id" in error for error in errors))

    def test_invalid_shapes_and_duplicate_ids(self) -> None:
        """Reject invalid object shapes and duplicate finding identifiers."""

        self.assertEqual(
            ["report must be a JSON object"],
            validate_findings.validate_report([]),
        )
        report = valid_report()
        report["scope"] = []
        report["summary"] = []
        report["findings"] = ["bad"]
        errors = validate_findings.validate_report(report)
        self.assertTrue(any("scope must" in error for error in errors))
        self.assertTrue(any("summary must" in error for error in errors))
        self.assertTrue(any("findings[0]" in error for error in errors))

        report = valid_report()
        duplicate = dict(report["findings"][0])
        report["findings"].append(duplicate)
        report["summary"]["finding_count"] = 2
        errors = validate_findings.validate_report(report)
        self.assertTrue(any("duplicates SEC-001" in error for error in errors))

        report = valid_report()
        report["findings"] = None
        self.assertIn(
            "findings must be an array",
            validate_findings.validate_report(report),
        )

        report = valid_report()
        report["summary"]["finding_count"] = -1
        report["findings"][0]["locations"] = []
        errors = validate_findings.validate_report(report)
        self.assertTrue(any("non-negative integer" in error for error in errors))
        self.assertTrue(any("locations must be" in error for error in errors))

    def test_rejects_contract_gaps(self) -> None:
        """Reject bool versions, unknown IDs, escaping paths, and missing keys."""

        report = valid_report()
        report["schema_version"] = True
        report["scope"].pop("target")
        report["summary"].pop("loaded_skills")
        report["summary"]["taxonomy_families"] = ["NOPE"]
        finding = report["findings"][0]
        finding["taxonomy_id"] = "AUTHN-99"
        finding.pop("vetting")
        finding["locations"] = [
            {"path": "../outside.py", "line": 1, "symbol": None},
            {"path": "/etc/passwd", "line": 1, "symbol": None},
            {"path": "C:/x.py", "line": 1, "symbol": None},
            {"path": "src/a.py", "line": 1},
        ]
        errors = validate_findings.validate_report(report)
        for expected in (
            "schema_version must equal 1",
            "scope.target is missing",
            "summary.loaded_skills must be an array",
            "summary.taxonomy_families[0] is not a known family",
            "taxonomy_id is not a recognized",
            "findings[0].vetting must be one of",
            "locations[0].path must be a repository-relative",
            "locations[1].path must be a repository-relative",
            "locations[2].path must be a repository-relative",
            "locations[3].symbol is missing",
        ):
            with self.subTest(expected=expected):
                self.assertTrue(
                    any(expected in error for error in errors), (expected, errors)
                )

        report = valid_report()
        report["summary"]["loaded_skills"] = ["blueprint", ""]
        self.assertEqual(
            ["summary.loaded_skills[1] must be a non-empty string"],
            validate_findings.validate_report(report),
        )

    def test_taxonomy_ids_match_reference(self) -> None:
        """Keep the validator's ID table in sync with the taxonomy reference."""

        taxonomy = (
            Path(__file__).resolve().parents[1]
            / "references"
            / "vulnerability-taxonomy.md"
        ).read_text(encoding="utf-8")
        ids = re.findall(r"^\| ([A-Z]+-[0-9]{2}) \|", taxonomy, re.M)
        ids = [value for value in ids if not value.startswith("CWE-")]
        self.assertEqual(len(ids), len(set(ids)), "duplicate taxonomy IDs")
        self.assertEqual(set(ids), validate_findings.TAXONOMY_IDS)

    def test_validator_reads_stdin(self) -> None:
        """Accept '-' so reports never need to be written into the audited repo."""

        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(valid_report()).encode()))
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(
            stdout
        ):
            exit_code = validate_findings.main(["-"])
        self.assertEqual(0, exit_code)
        self.assertEqual("valid\n", stdout.getvalue())

    def test_deeply_nested_json_is_unreadable(self) -> None:
        """Report pathological nesting as unreadable input (2), not invalid (1)."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "deep.json"
            path.write_text("[" * 200_000, encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = validate_findings.main([str(path)])
            self.assertEqual(2, exit_code)
            self.assertIn("cannot read report", stderr.getvalue())

    def test_non_string_enum_values_are_errors(self) -> None:
        """Report list/dict enum values as errors instead of raising TypeError."""

        report = valid_report()
        report["scope"]["mode"] = {}
        for key in ("taxonomy_id", "severity", "confidence", "vetting"):
            report["findings"][0][key] = []
        errors = validate_findings.validate_report(report)
        for expected in (
            "scope.mode",
            "taxonomy_id",
            "severity",
            "confidence",
            "vetting",
        ):
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_location_path_edge_cases(self) -> None:
        """Accept colon names; reject '.', home-relative, and URL paths."""

        for path, valid in (
            ("a:b.ts", True),
            ("src/a:b.ts", True),
            ("C:", False),
            ("C:\\x.py", False),
            (".", False),
            ("./", False),
            ("~", False),
            ("~/x.py", False),
            ("file:///etc/passwd", False),
            ("https://example.com/a.py", False),
        ):
            with self.subTest(path=path):
                report = valid_report()
                report["findings"][0]["locations"][0]["path"] = path
                errors = validate_findings.validate_report(report)
                self.assertEqual(valid, not errors, errors)

    def test_oversized_integer_is_unreadable(self) -> None:
        """Report integers beyond Python's digit limit as unparsable (exit 2)."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "big.json"
            path.write_text('{"schema_version": ' + "1" * 5000 + "}", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = validate_findings.main([str(path)])
            if exit_code == 1 and "cannot read report" not in stderr.getvalue():
                self.skipTest("this Python has no integer digit limit")
            self.assertEqual(2, exit_code)

    def test_validator_cli(self) -> None:
        """Return distinct CLI statuses for valid, invalid, and unreadable input."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            valid_path = root / "valid.json"
            valid_path.write_text(json.dumps(valid_report()), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = validate_findings.main([str(valid_path)])
            self.assertEqual(0, exit_code)
            self.assertEqual("valid\n", stdout.getvalue())

            invalid = valid_report()
            invalid["findings"][0]["confidence"] = "low"
            invalid_path = root / "invalid.json"
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = validate_findings.main([str(invalid_path)])
            self.assertEqual(1, exit_code)
            self.assertIn("confidence", stderr.getvalue())

            malformed_path = root / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = validate_findings.main([str(malformed_path)])
            self.assertEqual(2, exit_code)
            self.assertIn("cannot read report", stderr.getvalue())

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = validate_findings.main([str(root / "missing.json")])
            self.assertEqual(2, exit_code)


if __name__ == "__main__":
    unittest.main()
