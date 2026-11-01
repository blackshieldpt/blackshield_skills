"""Contract tests for ``coding/skills/_shared/forge.py``.

The tests import the adapter as a module and mock both transports: CLI calls
(``git``/``gh``) through ``forge.run_command`` and HTTP through
``urllib.request.urlopen``. Nothing here touches the network or a real forge.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
FORGE_PATH = REPO_ROOT / "coding" / "skills" / "_shared" / "forge.py"

_SPEC = importlib.util.spec_from_file_location("forge_adapter", FORGE_PATH)
forge = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(forge)

GITHUB_REMOTE = "git@github.com:acme/widgets.git"
GITEA_ENV = {
    "FORGE_TYPE": "gitea",
    "FORGE_URL": "https://git.company.tld",
}
TOKEN_ENV = dict(GITEA_ENV, FORGE_TOKEN="s3cr3t-token")


class FakeResponse:
    """Minimal context-manager response for ``urlopen``."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class ForgeAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body_file = Path(self.tmp.name) / "body.md"
        self.body_file.write_text("Body text\n", encoding="utf-8")

    # -- harness ---------------------------------------------------------

    def invoke(
        self,
        argv,
        *,
        env=None,
        git_url=GITHUB_REMOTE,
        gh=None,
        http=None,
    ):
        environ = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": self.tmp.name}
        if env:
            environ.update(env)
        gh_calls = []
        http_calls = []

        def fake_run(cmd, timeout=None):
            if cmd[:2] == ["git", "remote"]:
                if git_url is None:
                    raise forge.ForgeError(2, "no origin remote")
                return git_url + "\n"
            gh_calls.append(cmd)
            if gh is None:
                raise AssertionError(f"unexpected gh call: {cmd!r}")
            result = gh(cmd)
            if isinstance(result, str):
                return result
            return json.dumps(result)

        def fake_urlopen(request, timeout=None):
            payload = json.loads(request.data) if request.data else None
            http_calls.append((request.get_method(), request.full_url, payload))
            if http is None:
                raise AssertionError(f"unexpected HTTP call: {request.full_url}")
            result = http(request.get_method(), request.full_url, payload)
            if isinstance(result, tuple):
                body, status = result
            else:
                body, status = result, 200
            if isinstance(body, bytes):
                raw = body
            elif isinstance(body, str):
                raw = body.encode("utf-8")
            else:
                raw = json.dumps(body).encode("utf-8")
            if status >= 400:
                raise urllib.error.HTTPError(
                    request.full_url, status, "error", {}, io.BytesIO(raw)
                )
            return FakeResponse(raw)

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), mock.patch.object(
            forge, "run_command", side_effect=fake_run
        ), mock.patch.object(
            forge.urllib.request, "urlopen", side_effect=fake_urlopen
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = forge.main(argv)
        return SimpleNamespace(
            code=code,
            out=stdout.getvalue(),
            err=stderr.getvalue(),
            gh=gh_calls,
            http=http_calls,
        )

    def json(self, result):
        return json.loads(result.out)

    # -- detection -------------------------------------------------------

    def test_forge_detect_github_com(self):
        result = self.invoke(
            ["detect"],
            gh=lambda cmd: (_ for _ in ()).throw(forge.ForgeError(2, "no gh")),
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result), {"forge": "github"})

    def test_forge_detect_ghe(self):
        result = self.invoke(
            ["detect"],
            git_url="git@github.company.tld:acme/widgets.git",
            gh=lambda cmd: (_ for _ in ()).throw(forge.ForgeError(2, "no gh")),
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result), {"forge": "github"})

    def test_forge_detect_gitea(self):
        forced = self.invoke(
            ["detect"],
            env={"FORGE_TYPE": "gitea"},
            git_url="git@git.company.tld:acme/widgets.git",
        )
        self.assertEqual(forced.code, 0, forced.err)
        self.assertEqual(self.json(forced), {"forge": "gitea"})

        codeberg = self.invoke(
            ["detect"], git_url="https://codeberg.org/acme/widgets.git"
        )
        self.assertEqual(codeberg.code, 0, codeberg.err)
        self.assertEqual(self.json(codeberg), {"forge": "forgejo"})

    def test_forge_detect_unknown(self):
        def no_gh(cmd):
            raise forge.ForgeError(2, "gh failed")

        result = self.invoke(
            ["detect"],
            git_url="git@git.company.tld:acme/widgets.git",
            gh=no_gh,
        )
        self.assertEqual(result.code, 2)
        self.assertIn("unsupported forge", result.err)
        self.assertEqual(result.out, "")

    def test_forge_type_wins_over_hostname(self):
        result = self.invoke(["detect"], env={"FORGE_TYPE": "forgejo"})
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result), {"forge": "forgejo"})

    # -- repository ------------------------------------------------------

    def test_repo_github_fields(self):
        def gh(cmd):
            return {
                "nameWithOwner": "acme/widgets",
                "owner": {"login": "acme"},
                "name": "widgets",
                "visibility": "INTERNAL",
                "isPrivate": True,
                "isFork": True,
                "parent": {"nameWithOwner": "upstream/widgets"},
                "hasIssuesEnabled": True,
                "defaultBranchRef": {"name": "trunk"},
                "url": "https://github.com/acme/widgets",
            }

        result = self.invoke(["repo"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(
            data,
            {
                "forge": "github",
                "host": "github.com",
                "owner": "acme",
                "name": "widgets",
                "visibility": "internal",
                "private": True,
                "fork": True,
                "parent": "upstream/widgets",
                "has_issues": True,
                "default_branch": "trunk",
            },
        )

    def test_repo_gitea_fields(self):
        def http(method, url, body):
            self.assertTrue(url.endswith("/repos/acme/widgets"), url)
            return {
                "owner": {"login": "acme"},
                "name": "widgets",
                "private": False,
                "internal": False,
                "fork": True,
                "parent": {"full_name": "upstream/widgets"},
                "has_issues": True,
                "default_branch": "main",
            }

        result = self.invoke(["repo"], env=GITEA_ENV, http=http)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["forge"], "gitea")
        self.assertEqual(data["host"], "git.company.tld")
        self.assertEqual(data["visibility"], "public")
        self.assertTrue(data["fork"])
        self.assertEqual(data["parent"], "upstream/widgets")

    # -- issues ----------------------------------------------------------

    def test_issue_list_gitea(self):
        def http(method, url, body):
            self.assertIn("type=issues", url)
            self.assertIn("state=open", url)
            return [
                {
                    "number": 3,
                    "title": "Crash",
                    "body": "trace",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "html_url": "https://git.company.tld/acme/widgets/issues/3",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]

        result = self.invoke(["issue", "list"], env=GITEA_ENV, http=http)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data[0]["state"], "open")
        self.assertEqual(data[0]["labels"], ["bug"])
        self.assertEqual(data[0]["author"], "dev")

    def test_issue_view_github_comments(self):
        def gh(cmd):
            self.assertIn("--json", cmd)
            return {
                "number": 4,
                "title": "Crash",
                "body": "trace",
                "state": "OPEN",
                "labels": [{"name": "bug"}],
                "url": "https://github.com/acme/widgets/issues/4",
                "author": {"login": "dev"},
                "createdAt": "2026-01-01T00:00:00Z",
                "comments": [
                    {"author": {"login": "rev"}, "body": "seen", "includesCreatedEdit": True}
                ],
            }

        result = self.invoke(["issue", "view", "4"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["state"], "open")
        self.assertEqual(
            data["comments"],
            [{"author": "rev", "body": "seen", "edited": True}],
        )

    def test_issue_create_resolves_labels(self):
        created = {}
        labels = [{"id": 7, "name": "bug"}, {"id": 9, "name": "triage"}]

        def http(method, url, body):
            if method == "GET" and url.split("?")[0].endswith("/labels"):
                return labels
            if method == "POST" and url.endswith("/issues"):
                created.update(body)
                return {
                    "number": 11,
                    "title": body["title"],
                    "body": body["body"],
                    "state": "open",
                    "labels": labels,
                    "html_url": "https://git.company.tld/acme/widgets/issues/11",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(
            [
                "issue",
                "create",
                "--title",
                "Crash",
                "--body-file",
                str(self.body_file),
                "--label",
                "bug",
                "--label",
                "triage",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(created["labels"], [7, 9])
        self.assertEqual(self.json(result)["number"], 11)

    def test_issue_create_unknown_label_requires_flag(self):
        posted = []

        def http(method, url, body):
            if method == "GET" and url.split("?")[0].endswith("/labels"):
                return [{"id": 7, "name": "bug"}]
            if method == "POST":
                posted.append((method, url, body))
            return {}

        result = self.invoke(
            [
                "issue",
                "create",
                "--title",
                "Crash",
                "--body-file",
                str(self.body_file),
                "--label",
                "security",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 1)
        self.assertIn("label not found", result.err)
        self.assertEqual(posted, [])

    def test_issue_create_creates_missing_labels_when_allowed(self):
        def http(method, url, body):
            if method == "GET" and url.split("?")[0].endswith("/labels"):
                return []
            if method == "POST" and url.split("?")[0].endswith("/labels"):
                self.assertEqual(body["name"], "security")
                return {"id": 42, "name": "security", "color": "ededed", "description": ""}
            if method == "POST" and url.endswith("/issues"):
                self.assertEqual(body["labels"], [42])
                return {
                    "number": 5,
                    "title": body["title"],
                    "body": body["body"],
                    "state": "open",
                    "labels": [{"name": "security"}],
                    "html_url": "https://git.company.tld/acme/widgets/issues/5",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(
            [
                "issue",
                "create",
                "--title",
                "Crash",
                "--body-file",
                str(self.body_file),
                "--label",
                "security",
                "--create-labels",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result)["labels"], ["security"])

    def test_issue_comment_and_close_gitea(self):
        def http(method, url, body):
            if method == "POST" and url.endswith("/issues/8/comments"):
                return {
                    "body": body["body"],
                    "user": {"login": "dev"},
                    "created_at": "2026-02-01T00:00:00Z",
                    "updated_at": "2026-02-01T00:00:00Z",
                }
            if method == "PATCH" and url.endswith("/issues/8"):
                self.assertEqual(body, {"state": "closed"})
                return {
                    "number": 8,
                    "title": "Crash",
                    "body": "trace",
                    "state": "closed",
                    "labels": [],
                    "html_url": "https://git.company.tld/acme/widgets/issues/8",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            raise AssertionError(f"unexpected {method} {url}")

        comment = self.invoke(
            ["issue", "comment", "8", "--body-file", str(self.body_file)],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(comment.code, 0, comment.err)
        self.assertTrue(self.json(comment)["commented"])

        closed = self.invoke(
            [
                "issue",
                "close",
                "8",
                "--comment-file",
                str(self.body_file),
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(closed.code, 0, closed.err)
        self.assertEqual(self.json(closed)["state"], "closed")

    # -- pull requests ---------------------------------------------------

    def test_pr_list_gitea_filters_merged(self):
        def http(method, url, body):
            self.assertIn("state=closed", url)
            return [
                {
                    "number": 1,
                    "title": "Open change",
                    "body": "",
                    "state": "closed",
                    "merged": False,
                    "merge_commit_sha": "",
                    "html_url": "https://git.company.tld/acme/widgets/pulls/1",
                    "base": {"ref": "main"},
                    "head": {"ref": "feature", "sha": "aaa"},
                    "user": {"login": "dev"},
                },
                {
                    "number": 2,
                    "title": "Landed change",
                    "body": "Fixes #9",
                    "state": "closed",
                    "merged": True,
                    "merge_commit_sha": "bbb",
                    "html_url": "https://git.company.tld/acme/widgets/pulls/2",
                    "base": {"ref": "main"},
                    "head": {"ref": "feature", "sha": "ccc"},
                    "user": {"login": "dev"},
                },
            ]

        result = self.invoke(
            ["pr", "list", "--state", "merged"], env=GITEA_ENV, http=http
        )
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual([pr["number"] for pr in data], [2])
        self.assertEqual(data[0]["state"], "merged")

    def test_pr_view_github(self):
        def gh(cmd):
            return {
                "number": 6,
                "title": "Change",
                "body": "Fixes #2",
                "state": "MERGED",
                "merged": True,
                "mergeCommit": {"oid": "deadbeef"},
                "url": "https://github.com/acme/widgets/pull/6",
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "cafe",
                "author": {"login": "dev"},
                "closingIssuesReferences": [{"number": 2}],
                "commits": [{"oid": "cafe"}],
            }

        result = self.invoke(["pr", "view", "6"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["state"], "merged")
        self.assertEqual(data["merge_commit"], "deadbeef")
        self.assertEqual(data["closing_issues"], [2])
        self.assertEqual(data["commits"], ["cafe"])

    def test_pr_view_gitea_reads_commits(self):
        def http(method, url, body):
            if url.endswith("/pulls/6/commits"):
                return [{"sha": "cafe"}, {"sha": "beef"}]
            if url.endswith("/pulls/6"):
                return {
                    "number": 6,
                    "title": "Change",
                    "body": "Closes #12",
                    "state": "open",
                    "merged": False,
                    "merge_commit_sha": "",
                    "html_url": "https://git.company.tld/acme/widgets/pulls/6",
                    "base": {"ref": "main"},
                    "head": {"ref": "feature", "sha": "cafe"},
                    "user": {"login": "dev"},
                }
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(["pr", "view", "6"], env=GITEA_ENV, http=http)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["commits"], ["cafe", "beef"])
        self.assertEqual(data["closing_issues"], [12])
        self.assertEqual(data["head_sha"], "cafe")

    def test_pr_diff_github_is_raw(self):
        diff = "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n"

        def gh(cmd):
            self.assertEqual(cmd, ["gh", "pr", "diff", "6", "-R", "acme/widgets"])
            return diff

        result = self.invoke(["pr", "diff", "6"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out, diff)

    def test_pr_create_edit_comment_gitea(self):
        created = {}
        edited = {}
        commented = {}

        def pr(number, title, body):
            return {
                "number": number,
                "title": title,
                "body": body,
                "state": "open",
                "merged": False,
                "merge_commit_sha": "",
                "html_url": f"https://git.company.tld/acme/widgets/pulls/{number}",
                "base": {"ref": "main"},
                "head": {"ref": "feature", "sha": "cafe"},
                "user": {"login": "dev"},
            }

        def http(method, url, body):
            if method == "POST" and url.endswith("/pulls"):
                created.update(body)
                return pr(7, body["title"], body["body"])
            if method == "PATCH" and url.endswith("/pulls/7"):
                edited.update(body)
                return pr(7, "Change", body["body"])
            if method == "POST" and url.endswith("/issues/7/comments"):
                commented.update(body)
                return {
                    "body": body["body"],
                    "user": {"login": "dev"},
                    "created_at": "2026-02-01T00:00:00Z",
                    "updated_at": "2026-02-01T00:00:00Z",
                }
            raise AssertionError(f"unexpected {method} {url}")

        create = self.invoke(
            [
                "pr",
                "create",
                "--title",
                "Change",
                "--body-file",
                str(self.body_file),
                "--head",
                "feature",
                "--base",
                "main",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(create.code, 0, create.err)
        self.assertEqual(created["head"], "feature")
        self.assertEqual(created["base"], "main")

        edit = self.invoke(
            ["pr", "edit", "7", "--body-file", str(self.body_file)],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(edit.code, 0, edit.err)
        self.assertEqual(edited["body"], "Body text\n")

        comment = self.invoke(
            ["pr", "comment", "7", "--body-file", str(self.body_file)],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(comment.code, 0, comment.err)
        self.assertTrue(self.json(comment)["commented"])

    # -- checks ----------------------------------------------------------

    def test_pr_checks_states(self):
        cases = {
            "success": [
                {"__typename": "CheckRun", "name": "test", "conclusion": "SUCCESS"},
                {"__typename": "StatusContext", "context": "ci", "state": "SUCCESS"},
            ],
            "failure": [
                {"__typename": "CheckRun", "name": "test", "conclusion": "SUCCESS"},
                {"__typename": "StatusContext", "context": "ci", "state": "FAILURE"},
            ],
            "pending": [
                {"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS"},
            ],
            "unknown": [
                {"__typename": "CheckRun", "name": "test", "conclusion": "NEUTRAL"},
                {"__typename": "CheckRun", "name": "lint", "conclusion": "SKIPPED"},
            ],
        }
        for expected, rollup in cases.items():
            with self.subTest(state=expected):
                result = self.invoke(
                    ["pr", "checks", "6"],
                    gh=lambda cmd, rollup=rollup: {"statusCheckRollup": rollup},
                )
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.json(result)["state"], expected)

    def test_pr_checks_no_checks(self):
        github = self.invoke(
            ["pr", "checks", "6"],
            gh=lambda cmd: {"statusCheckRollup": []},
        )
        self.assertEqual(github.code, 0, github.err)
        self.assertEqual(self.json(github)["state"], "no_checks")
        self.assertNotEqual(self.json(github)["state"], "unknown")

        def http(method, url, body):
            if url.endswith("/pulls/6"):
                return {"head": {"sha": "cafe"}}
            if url.endswith("/commits/cafe/status"):
                return {"state": "", "statuses": [], "total_count": 0}
            raise AssertionError(f"unexpected {method} {url}")

        gitea = self.invoke(["pr", "checks", "6"], env=GITEA_ENV, http=http)
        self.assertEqual(gitea.code, 0, gitea.err)
        self.assertEqual(self.json(gitea)["state"], "no_checks")

    def test_pr_checks_gitea_mapping(self):
        cases = {
            "success": ("success", [{"context": "ci", "status": "success"}]),
            "failure": ("failure", [{"context": "ci", "status": "error"}]),
            "unknown": ("warning", [{"context": "ci", "status": "warning"}]),
            "pending": ("pending", [{"context": "ci", "status": "pending"}]),
        }
        for expected, (combined, statuses) in cases.items():
            with self.subTest(state=expected):

                def http(method, url, body):
                    if url.endswith("/pulls/6"):
                        return {"head": {"sha": "cafe"}}
                    if url.endswith("/commits/cafe/status"):
                        return {
                            "state": combined,
                            "statuses": statuses,
                            "total_count": len(statuses),
                        }
                    raise AssertionError(f"unexpected {method} {url}")

                result = self.invoke(
                    ["pr", "checks", "6"], env=GITEA_ENV, http=http
                )
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.json(result)["state"], expected)

    # -- merge -----------------------------------------------------------

    def test_pr_merge_pins_head(self):
        captured = {}

        def http(method, url, body):
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/pulls/6/merge"), url)
            captured.update(body)
            return b"", 200

        result = self.invoke(
            [
                "pr",
                "merge",
                "6",
                "--method",
                "squash",
                "--head-sha",
                "cafe123",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(captured["Do"], "squash")
        self.assertEqual(captured["head_commit_id"], "cafe123")
        data = self.json(result)
        self.assertTrue(data["merged"])
        self.assertEqual(data["merge_commit"], "cafe123")

    def test_pr_merge_github_pins_head_through_api(self):
        def gh(cmd):
            self.assertEqual(cmd[0], "gh")
            self.assertEqual(cmd[1], "api")
            self.assertIn("sha=cafe123", cmd)
            return {"merged": True, "sha": "merged456"}

        result = self.invoke(
            ["pr", "merge", "6", "--method", "merge", "--head-sha", "cafe123"],
            gh=gh,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result)["merge_commit"], "merged456")

    # -- labels ----------------------------------------------------------

    def test_label_list_and_create_gitea(self):
        def http(method, url, body):
            if method == "GET" and url.split("?")[0].endswith("/labels"):
                return [
                    {
                        "name": "bug",
                        "description": "broken",
                        "color": "#d73a4a",
                    }
                ]
            if method == "POST" and url.split("?")[0].endswith("/labels"):
                return {
                    "name": body["name"],
                    "description": body["description"],
                    "color": body["color"],
                }
            raise AssertionError(f"unexpected {method} {url}")

        listed = self.invoke(["label", "list"], env=GITEA_ENV, http=http)
        self.assertEqual(listed.code, 0, listed.err)
        self.assertEqual(
            self.json(listed),
            [{"name": "bug", "description": "broken", "color": "d73a4a"}],
        )

        created = self.invoke(
            [
                "label",
                "create",
                "security",
                "--description",
                "vulnerability",
                "--color",
                "#ff0000",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(created.code, 0, created.err)
        self.assertEqual(
            self.json(created),
            {"name": "security", "description": "vulnerability", "color": "ff0000"},
        )

    # -- errors ----------------------------------------------------------

    def test_missing_token_exits_3(self):
        result = self.invoke(
            [
                "issue",
                "create",
                "--title",
                "Crash",
                "--body-file",
                str(self.body_file),
            ],
            env=GITEA_ENV,
            http=lambda *args: self.fail("no HTTP call without a token"),
        )
        self.assertEqual(result.code, 3)
        self.assertIn("FORGE_TOKEN", result.err)
        self.assertEqual(result.out, "")

    def test_http_status_mapping(self):
        for status, expected in ((401, 3), (403, 3), (404, 4), (409, 5)):
            with self.subTest(status=status):
                result = self.invoke(
                    ["repo"],
                    env=GITEA_ENV,
                    http=lambda method, url, body, status=status: (
                        {"message": "nope"},
                        status,
                    ),
                )
                self.assertEqual(result.code, expected, result.err)

    def test_no_secret_in_output(self):
        token = TOKEN_ENV["FORGE_TOKEN"]

        def http(method, url, body):
            return {"message": f"bad token {token}"}, 401

        result = self.invoke(["repo"], env=TOKEN_ENV, http=http)
        self.assertEqual(result.code, 3)
        self.assertNotIn(token, result.out)
        self.assertNotIn(token, result.err)
        self.assertIn("HTTP 401", result.err)


if __name__ == "__main__":
    unittest.main()
