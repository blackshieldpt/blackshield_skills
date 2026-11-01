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
import urllib.parse
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

    # -- viewer and auth -------------------------------------------------

    def test_viewer_authenticated_gitea(self):
        def http(method, url, body):
            self.assertEqual(method, "GET")
            self.assertEqual(url, "https://git.company.tld/api/v1/user")
            return {"login": "dev"}

        result = self.invoke(["viewer"], env=TOKEN_ENV, http=http)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result), {"login": "dev"})

    def test_viewer_github(self):
        def gh(cmd):
            self.assertEqual(cmd, ["gh", "api", "user"])
            return {"login": "octocat"}

        result = self.invoke(["viewer"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result), {"login": "octocat"})

    def test_auth_commands(self):
        gitea = self.invoke(["auth"], env=TOKEN_ENV)
        self.assertEqual(gitea.code, 0, gitea.err)
        self.assertEqual(self.json(gitea), {"authenticated": True})

        anonymous = self.invoke(["auth"], env=GITEA_ENV)
        self.assertEqual(anonymous.code, 0, anonymous.err)
        self.assertEqual(self.json(anonymous), {"authenticated": False})

        github = self.invoke(["auth"], gh=lambda cmd: "")
        self.assertEqual(github.code, 0, github.err)
        self.assertEqual(self.json(github), {"authenticated": True})

        def no_auth(cmd):
            raise forge.ForgeError(3, "not logged in")

        github_anonymous = self.invoke(["auth"], gh=no_auth)
        self.assertEqual(github_anonymous.code, 0, github_anonymous.err)
        self.assertEqual(self.json(github_anonymous), {"authenticated": False})

    def test_anonymous_viewer_null(self):
        viewer = self.invoke(["viewer"], env=GITEA_ENV)
        self.assertEqual(viewer.code, 0, viewer.err)
        self.assertEqual(self.json(viewer), {"login": None})

        def http(method, url, body):
            if url.endswith("/issues/4/comments"):
                return []
            if url.endswith("/issues/4"):
                return {
                    "number": 4,
                    "title": "Crash",
                    "body": "trace",
                    "state": "open",
                    "labels": [],
                    "html_url": "https://git.company.tld/acme/widgets/issues/4",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            raise AssertionError(f"anonymous read made an unexpected call: {url}")

        viewed = self.invoke(["issue", "view", "4"], env=GITEA_ENV, http=http)
        self.assertEqual(viewed.code, 0, viewed.err)
        self.assertIsNone(self.json(viewed)["viewer"])

    def test_viewer_ghe_uses_hostname(self):
        def gh(cmd):
            if cmd[1:3] == ["api", "user"]:
                return {"login": "rev"}
            return {
                "number": 4,
                "title": "Crash",
                "body": "trace",
                "state": "OPEN",
                "labels": [{"name": "bug"}],
                "url": "https://github.company.tld/acme/widgets/issues/4",
                "author": {"login": "dev"},
                "createdAt": "2026-01-01T00:00:00Z",
                "comments": [],
            }

        remote = "git@github.company.tld:acme/widgets.git"
        user_call = ["gh", "api", "user", "--hostname", "github.company.tld"]
        result = self.invoke(["viewer"], git_url=remote, gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.gh, [user_call])

        viewed = self.invoke(["issue", "view", "4"], git_url=remote, gh=gh)
        self.assertEqual(viewed.code, 0, viewed.err)
        self.assertEqual([c for c in viewed.gh if c[1:3] == ["api", "user"]], [user_call])
        self.assertEqual(self.json(viewed)["viewer"], "rev")

    def test_auth_passes_hostname(self):
        result = self.invoke(["auth"], gh=lambda cmd: "")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(
            result.gh, [["gh", "auth", "status", "--hostname", "github.com"]]
        )
        ghe = self.invoke(
            ["auth"], git_url="git@github.company.tld:acme/widgets.git", gh=lambda cmd: ""
        )
        self.assertEqual(ghe.code, 0, ghe.err)
        self.assertEqual(
            ghe.gh, [["gh", "auth", "status", "--hostname", "github.company.tld"]]
        )

    def test_gitea_viewer_null_on_auth_error(self):
        def http(method, url, body):
            if url == "https://git.company.tld/api/v1/user":
                return ({"message": "token does not have at least one of required scope(s)"}, 403)
            if url.endswith("/issues/4/comments"):
                return []
            if url.endswith("/issues/4"):
                return {
                    "number": 4,
                    "title": "Crash",
                    "body": "trace",
                    "state": "open",
                    "labels": [],
                    "html_url": "https://git.company.tld/acme/widgets/issues/4",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            raise AssertionError(f"unexpected call: {url}")

        viewer = self.invoke(["viewer"], env=TOKEN_ENV, http=http)
        self.assertEqual(viewer.code, 0, viewer.err)
        self.assertEqual(self.json(viewer), {"login": None})
        viewed = self.invoke(["issue", "view", "4"], env=TOKEN_ENV, http=http)
        self.assertEqual(viewed.code, 0, viewed.err)
        self.assertIsNone(self.json(viewed)["viewer"])

        broken = self.invoke(
            ["viewer"], env=TOKEN_ENV, http=lambda method, url, body: ({}, 500)
        )
        self.assertEqual(broken.code, 1, broken.err)

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

    def test_repo_github_passes_repository_argument(self):
        def gh(cmd):
            return {
                "nameWithOwner": "acme/widgets",
                "owner": {"login": "acme"},
                "name": "widgets",
                "visibility": "PUBLIC",
                "isPrivate": False,
                "isFork": False,
                "parent": None,
                "hasIssuesEnabled": True,
                "defaultBranchRef": {"name": "main"},
                "url": "https://github.com/acme/widgets",
            }

        self.assertEqual(
            forge.GH_REPO_FIELDS,
            "nameWithOwner,owner,name,visibility,isPrivate,isFork,parent,"
            "hasIssuesEnabled,defaultBranchRef,url",
        )
        result = self.invoke(["repo"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(
            result.gh,
            [["gh", "repo", "view", "acme/widgets", "--json", forge.GH_REPO_FIELDS]],
        )
        ghe = self.invoke(["repo", "--repo", "github.company.tld/acme/widgets"], gh=gh)
        self.assertEqual(ghe.code, 0, ghe.err)
        self.assertEqual(
            ghe.gh,
            [
                [
                    "gh",
                    "repo",
                    "view",
                    "github.company.tld/acme/widgets",
                    "--json",
                    forge.GH_REPO_FIELDS,
                ]
            ],
        )

    def test_repo_host_override_uses_ghe_selector(self):
        calls = []

        def gh(cmd):
            calls.append(cmd)
            return {
                "nameWithOwner": "acme/widgets",
                "owner": {"login": "acme"},
                "name": "widgets",
                "visibility": "PRIVATE",
                "isPrivate": True,
                "isFork": False,
                "parent": None,
                "hasIssuesEnabled": True,
                "defaultBranchRef": {"name": "main"},
                "url": "https://ghe.company.com/acme/widgets",
            }

        result = self.invoke(
            ["repo", "--repo", "ghe.company.com/acme/widgets"], gh=gh
        )
        self.assertEqual(result.code, 0, result.err)
        repo_calls = [cmd for cmd in calls if cmd[:3] == ["gh", "repo", "view"]]
        self.assertTrue(repo_calls, calls)
        self.assertIn("ghe.company.com/acme/widgets", repo_calls[0])
        self.assertEqual(self.json(result)["host"], "ghe.company.com")


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

    def test_issue_list_gitea_all_states(self):
        result = self.invoke(
            ["issue", "list", "--state", "all", "--search", "crash", "--limit", "5"],
            env=GITEA_ENV,
            http=lambda method, url, body: [],
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(len(result.http), 1)
        self.assertEqual(
            urllib.parse.parse_qs(urllib.parse.urlsplit(result.http[0][1]).query),
            {
                "type": ["issues"],
                "state": ["all"],
                "q": ["crash"],
                "limit": ["5"],
                "page": ["1"],
            },
        )

    def test_issue_view_github_comments(self):
        def gh(cmd):
            if cmd[1:3] == ["api", "user"]:
                return {"login": "rev"}
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
                    {
                        "author": {"login": "rev"},
                        "body": "seen",
                        "createdAt": "2026-01-02T00:00:00Z",
                        "includesCreatedEdit": True,
                    }
                ],
            }

        result = self.invoke(["issue", "view", "4"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["state"], "open")
        self.assertEqual(data["viewer"], "rev")
        self.assertEqual(
            data["comments"],
            [
                {
                    "author": "rev",
                    "body": "seen",
                    "created_at": "2026-01-02T00:00:00Z",
                    "edited": True,
                }
            ],
        )

    def test_issue_view_gitea_comments_and_viewer(self):
        def http(method, url, body):
            self.assertEqual(method, "GET")
            if url == "https://git.company.tld/api/v1/user":
                return {"login": "rev"}
            if url.endswith("/issues/4/comments"):
                return [
                    {
                        "body": "seen",
                        "user": {"login": "dev"},
                        "created_at": "2026-01-02T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                    }
                ]
            if url.endswith("/issues/4"):
                return {
                    "number": 4,
                    "title": "Crash",
                    "body": "trace",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "html_url": "https://git.company.tld/acme/widgets/issues/4",
                    "user": {"login": "dev"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(
            ["issue", "view", "4"], env=TOKEN_ENV, http=http
        )
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["viewer"], "rev")
        self.assertEqual(
            data["comments"],
            [
                {
                    "author": "dev",
                    "body": "seen",
                    "created_at": "2026-01-02T00:00:00Z",
                    "edited": False,
                }
            ],
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

    def test_pr_list_head_scans_beyond_limit(self):
        def pr(number, head):
            return {
                "number": number,
                "title": f"PR {number}",
                "body": "",
                "state": "open",
                "merged": False,
                "merge_commit_sha": "",
                "html_url": f"https://git.company.tld/acme/widgets/pulls/{number}",
                "base": {"ref": "main"},
                "head": {"ref": head, "sha": f"sha{number}"},
                "user": {"login": "dev"},
            }

        requested_pages = []

        def http(method, url, body):
            self.assertNotIn("head=", url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            page = int(query.get("page", ["1"])[0])
            requested_pages.append(page)
            if page == 1:
                return [pr(number, "other") for number in range(1, 51)]
            if page == 2:
                return [pr(99, "feature")]
            return []

        result = self.invoke(
            ["pr", "list", "--head", "feature", "--limit", "1"],
            env=GITEA_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual([item["number"] for item in data], [99])
        self.assertEqual([item["head"] for item in data], ["feature"])
        self.assertIn(2, requested_pages)

    def test_pr_view_github(self):
        def gh(cmd):
            fields = cmd[cmd.index("--json") + 1].split(",")
            self.assertNotIn("merged", fields)
            return {
                "number": 6,
                "title": "Change",
                "body": "Fixes #2",
                "state": "MERGED",
                "mergeCommit": {"oid": "deadbeef"},
                "url": "https://github.com/acme/widgets/pull/6",
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "cafe",
                "author": {"login": "dev"},
                "closingIssuesReferences": [{"number": 2}],
                "commits": [
                    {
                        "oid": "cafe",
                        "messageHeadline": "Fix crash",
                        "messageBody": "Closes #2",
                    }
                ],
            }

        result = self.invoke(["pr", "view", "6"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["state"], "merged")
        self.assertTrue(data["merged"])
        self.assertEqual(data["merge_commit"], "deadbeef")
        self.assertEqual(data["closing_issues"], [2])
        self.assertEqual(data["commits"], ["cafe"])
        self.assertEqual(data["commit_messages"][0]["sha"], "cafe")
        self.assertIn("Fix crash", data["commit_messages"][0]["message"])

    def test_pr_list_github_omits_commits(self):
        sent = {}

        def gh(cmd):
            sent[cmd[2]] = cmd[cmd.index("--json") + 1].split(",")
            if cmd[2] == "view":
                return {"number": 5, "state": "MERGED", "headRefOid": "cafe",
                        "commits": [{"oid": "cafe"}]}
            self.assertEqual(cmd[cmd.index("--head") + 1], "feature")
            return [{"number": 5, "state": "MERGED", "mergeCommit": {"oid": "deadbeef"},
                     "headRefName": "feature", "headRefOid": "cafe"}]

        result = self.invoke(["pr", "list", "--head", "feature", "--state", "merged"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertNotIn("commits", sent["list"])
        for field in ("number", "state", "mergeCommit", "headRefName", "headRefOid",
                      "body", "closingIssuesReferences"):
            with self.subTest(field=field):
                self.assertIn(field, sent["list"])
        data = self.json(result)
        self.assertEqual([pr["number"] for pr in data], [5])
        self.assertEqual(data[0]["head_sha"], "cafe")
        self.assertEqual(data[0]["merge_commit"], "deadbeef")
        self.assertEqual(data[0]["commits"], [])
        self.assertEqual(data[0]["commit_messages"], [])
        view = self.invoke(["pr", "view", "5"], gh=gh)
        self.assertEqual(view.code, 0, view.err)
        self.assertIn("commits", sent["view"])
        self.assertEqual(sent["list"], [f for f in sent["view"] if f != "commits"])

    def test_pr_view_gitea_reads_commits(self):
        def http(method, url, body):
            if "/pulls/6/commits" in url:
                return [
                    {"sha": "cafe", "commit": {"message": "Fix crash\n\nCloses #12"}},
                    {"sha": "beef", "commit": {"message": "Add test"}},
                ]
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
        self.assertEqual(data["closing_issues_external"], [])
        self.assertIn("Fix crash", data["commit_messages"][0]["message"])

    def test_repo_override_skips_origin_detection(self):
        def gh(cmd):
            if cmd[:2] == ["gh", "auth"]:
                raise forge.ForgeError(2, "no gh")
            return {
                "number": 6,
                "title": "Change",
                "body": "",
                "state": "OPEN",
                "url": "https://github.com/acme/widgets/pull/6",
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "cafe",
                "author": {"login": "dev"},
                "closingIssuesReferences": [],
                "commits": [],
            }

        with mock.patch.object(
            forge, "git_remote_url", wraps=forge.git_remote_url
        ) as origin:
            result = self.invoke(
                ["pr", "view", "6", "--repo", "github.com/acme/widgets"],
                git_url="git@git.unsupported.tld:acme/widgets.git",
                gh=gh,
            )
        self.assertEqual(result.code, 0, result.err)
        self.assertFalse([c for c in result.gh if c[:2] == ["gh", "auth"]])
        self.assertEqual(origin.call_count, 0)

    def test_pr_view_gitea_paginates_commits(self):
        commits = [
            {"sha": f"sha{i}", "commit": {"message": f"msg {i}"}} for i in range(53)
        ]

        def http(method, url, body):
            if "/pulls/6/commits" in url:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                page = int(query.get("page", ["1"])[0])
                size = min(int(query.get("limit", ["30"])[0]), 50)
                return commits[(page - 1) * size : page * size]
            if url.endswith("/pulls/6"):
                return {
                    "number": 6,
                    "title": "Change",
                    "body": "",
                    "state": "open",
                    "merged": False,
                    "html_url": "https://git.company.tld/acme/widgets/pulls/6",
                    "base": {"ref": "main"},
                    "head": {"ref": "feature", "sha": "sha0"},
                    "user": {"login": "dev"},
                }
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(["pr", "view", "6"], env=GITEA_ENV, http=http)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(len(data["commits"]), 53)
        self.assertEqual(len(data["commit_messages"]), 53)
        self.assertEqual(forge.PR_COMMIT_LIMIT, 100)

    def test_pr_view_github_splits_cross_repo_closing_issues(self):
        def ref(number, repo=None):
            item = {"number": number}
            if repo:
                owner, name = repo.split("/")
                item["repository"] = {"name": name, "owner": {"login": owner}}
            return item

        def gh(cmd):
            return {
                "number": 6,
                "title": "Change",
                "body": "",
                "state": "OPEN",
                "url": "https://github.com/acme/widgets/pull/6",
                "baseRefName": "main",
                "headRefName": "feature",
                "headRefOid": "cafe",
                "author": {"login": "dev"},
                "closingIssuesReferences": [
                    ref(2, "acme/widgets"),
                    ref(3, "ACME/Widgets"),
                    ref(4),
                    ref(7, "acme/core"),
                    ref(9, "other/core"),
                ],
                "commits": [],
            }

        result = self.invoke(["pr", "view", "6"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["closing_issues"], [2, 3, 4])
        self.assertEqual(
            data["closing_issues_external"],
            [{"repo": "acme/core", "number": 7}, {"repo": "other/core", "number": 9}],
        )

    def test_repo_github_parent_from_owner_and_name(self):
        def gh(cmd):
            return {
                "nameWithOwner": "acme/widgets",
                "owner": {"login": "acme"},
                "name": "widgets",
                "visibility": "PUBLIC",
                "isFork": True,
                "parent": {
                    "id": "R_1",
                    "name": "widgets",
                    "owner": {"id": "U_1", "login": "upstream"},
                },
                "defaultBranchRef": {"name": "main"},
            }

        result = self.invoke(["repo"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result)["parent"], "upstream/widgets")

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

    def test_pr_create_fork_head_is_passed_through(self):
        captured = {}

        def http(method, url, body):
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/pulls"), url)
            captured.update(body)
            return {
                "number": 9,
                "title": body["title"],
                "body": body["body"],
                "state": "open",
                "merged": False,
                "merge_commit_sha": "",
                "html_url": "https://git.company.tld/acme/widgets/pulls/9",
                "base": {"ref": body["base"]},
                "head": {"ref": body["head"], "sha": "cafe"},
                "user": {"login": "dev"},
            }

        result = self.invoke(
            [
                "pr",
                "create",
                "--title",
                "Fork change",
                "--body-file",
                str(self.body_file),
                "--head",
                "forkowner:feature",
                "--base",
                "main",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(captured["head"], "forkowner:feature")
        self.assertEqual(captured["base"], "main")
        data = self.json(result)
        for key in (
            "number",
            "title",
            "body",
            "state",
            "merged",
            "merge_commit",
            "url",
            "base",
            "head",
            "head_sha",
            "author",
            "closing_issues",
            "commits",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["head"], "forkowner:feature")
        self.assertEqual(data["base"], "main")
        self.assertEqual(data["commits"], [])

    # -- checks ----------------------------------------------------------

    def test_checks_require_green_exit(self):
        cases = {
            "failure": (
                [
                    {"__typename": "CheckRun", "name": "test", "conclusion": "SUCCESS"},
                    {"__typename": "StatusContext", "context": "ci", "state": "FAILURE"},
                ],
                1,
            ),
            "pending": (
                [{"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS"}],
                1,
            ),
            "unknown": (
                [
                    {"__typename": "CheckRun", "name": "test", "conclusion": "NEUTRAL"},
                ],
                1,
            ),
            "success": (
                [
                    {"__typename": "CheckRun", "name": "test", "conclusion": "SUCCESS"},
                ],
                0,
            ),
            "no_checks": ([], 0),
        }
        for expected, (rollup, exit_code) in cases.items():
            with self.subTest(state=expected):
                result = self.invoke(
                    ["pr", "checks", "6", "--require-green"],
                    gh=lambda cmd, rollup=rollup: {"statusCheckRollup": rollup},
                )
                self.assertEqual(result.code, exit_code, result.err)
                self.assertEqual(self.json(result)["state"], expected)

    def test_checks_no_checks_exit_zero(self):
        def http(method, url, body):
            if url.endswith("/pulls/6"):
                return {"head": {"sha": "cafe"}}
            if url.endswith("/commits/cafe/status"):
                return {"state": "", "statuses": None, "total_count": 0}
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(
            ["pr", "checks", "6", "--require-green"], env=GITEA_ENV, http=http
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.json(result)["state"], "no_checks")

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
            if method == "POST":
                self.assertTrue(url.endswith("/pulls/6/merge"), url)
                captured.update(body)
                return b"", 200
            self.assertEqual(method, "GET")
            self.assertTrue(url.endswith("/pulls/6"), url)
            return {"number": 6, "merged": True, "merge_commit_sha": "merged456"}

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
        self.assertEqual(data["merge_commit"], "merged456")
        self.assertTrue(data["merge_commit_known"])

    def test_merge_commit_from_followup_get(self):
        calls = []

        def http(method, url, body):
            calls.append(method)
            if method == "POST":
                self.assertTrue(url.endswith("/pulls/6/merge"), url)
                return b"", 200
            self.assertTrue(url.endswith("/pulls/6"), url)
            return {"number": 6, "merged": True, "merge_commit_sha": "merged999"}

        result = self.invoke(
            ["pr", "merge", "6", "--method", "merge", "--head-sha", "cafe123"],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(calls, ["POST", "GET"])
        data = self.json(result)
        self.assertEqual(data["merge_commit"], "merged999")
        self.assertNotEqual(data["merge_commit"], "cafe123")
        self.assertTrue(data["merge_commit_known"])

    def test_merge_commit_equal_head_allowed(self):
        def http(method, url, body):
            if method == "POST":
                return b"", 200
            return {"number": 6, "merged": True, "merge_commit_sha": "cafe123"}

        result = self.invoke(
            ["pr", "merge", "6", "--method", "rebase", "--head-sha", "cafe123"],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        data = self.json(result)
        self.assertEqual(data["merge_commit"], "cafe123")
        self.assertTrue(data["merge_commit_known"])

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

    def test_pr_merge_ghe_pins_head_with_hostname(self):
        def gh(cmd):
            self.assertEqual(cmd[:4], ["gh", "api", "-X", "PUT"])
            self.assertEqual(cmd[4], "repos/acme/widgets/pulls/6/merge")
            self.assertIn("sha=cafe123", cmd)
            self.assertIn("merge_method=squash", cmd)
            self.assertEqual(cmd[-2:], ["--hostname", "github.company.tld"])
            return {"merged": True, "sha": "m1"}

        result = self.invoke(
            ["pr", "merge", "6", "--method", "squash", "--head-sha", "cafe123"],
            git_url="git@github.company.tld:acme/widgets.git",
            gh=gh,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(len(result.gh), 1)
        self.assertEqual(self.json(result)["merge_commit"], "m1")

    def test_pr_merge_github_refused_exits_conflict(self):
        def gh(cmd):
            return {"merged": False, "message": "Head branch was modified"}

        result = self.invoke(
            ["pr", "merge", "6", "--method", "merge", "--head-sha", "cafe123"],
            gh=gh,
        )
        self.assertEqual(result.code, 5)
        self.assertIn("merge refused", result.err)
        self.assertEqual(result.out, "")

    def test_pr_view_github_review_decision(self):
        sent = []
        decision = {"value": ""}

        def gh(cmd):
            sent.append(cmd[cmd.index("--json") + 1].split(","))
            return {"number": 6, "state": "OPEN", "reviewDecision": decision["value"]}

        for raw, want in (
            ("CHANGES_REQUESTED", "changes_requested"),
            ("REVIEW_REQUIRED", "review_required"),
            ("APPROVED", "approved"),
            ("", None),
        ):
            with self.subTest(raw=raw):
                decision["value"] = raw
                result = self.invoke(["pr", "view", "6", "--reviews"], gh=gh)
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(sent[-1], forge.GH_PR_FIELDS.split(",") + ["reviewDecision"])
                self.assertEqual(self.json(result)["review_decision"], want)
        result = self.invoke(["pr", "view", "6"], gh=gh)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(sent[-1], forge.GH_PR_FIELDS.split(","))
        self.assertNotIn("review_decision", self.json(result))
        self.assertNotIn("reviewDecision", forge.GH_PR_FIELDS.split(","))
        self.assertNotIn("reviewDecision", forge.GH_PR_LIST_FIELDS.split(","))

    def test_pr_view_gitea_review_decision(self):
        ids = iter(range(100, 200))

        def review(login, state, **extra):
            item = {"id": next(ids), "user": {"login": login}, "state": state, "official": True,
                    "dismissed": False, "stale": False}
            item.update(extra)
            return item

        def undated(item, when):
            item = dict(item, submitted_at=when)
            del item["id"]
            return item

        cases = (
            ("superseded rejection, then approval",
             [review("alice", "REQUEST_CHANGES", official=False), review("alice", "APPROVED")], "approved"),
            ("rejection, review re-requested, other approves",
             [review("bob", "REQUEST_CHANGES", official=False), review("bob", "REQUEST_REVIEW"),
              review("alice", "APPROVED")], "changes_requested"),
            ("rejection beside approval",
             [review("bob", "REQUEST_CHANGES"), review("alice", "APPROVED")], "changes_requested"),
            ("dismissed rejection only", [review("bob", "REQUEST_CHANGES", dismissed=True)], None),
            ("dismissed rejection beside approval",
             [review("bob", "REQUEST_CHANGES", dismissed=True), review("alice", "APPROVED")], "approved"),
            ("stale approval", [review("alice", "APPROVED", stale=True)], None),
            ("stale rejection", [review("bob", "REQUEST_CHANGES", stale=True)], "changes_requested"),
            ("comment and pending request",
             [review("alice", "COMMENT"), review("bob", "REQUEST_REVIEW")], None),
            ("non-official approval only", [review("eve", "APPROVED", official=False)], None),
            ("pending request beside approval",
             [review("carol", "REQUEST_REVIEW"), review("alice", "APPROVED")], "approved"),
            ("superseded rejection, listed newest first",
             [review("alice", "REQUEST_CHANGES", official=False), review("alice", "APPROVED")][::-1], "approved"),
            ("re-requested rejection, listed newest first",
             [review("bob", "APPROVED", official=False), review("bob", "REQUEST_CHANGES")][::-1],
             "changes_requested"),
            ("no ids: submitted_at decides, newest first",
             [undated(review("alice", "APPROVED"), "2026-10-02T00:00:00Z"),
              undated(review("alice", "REQUEST_CHANGES"), "2026-10-01T00:00:00Z")], "approved"),
        )
        for label, reviews, want in cases:
            def http(method, url, body, reviews=reviews):
                path = url.split("?")[0]
                if path.endswith("/pulls/6/reviews"):
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    return reviews if query.get("page") == ["1"] else []
                if path.endswith("/pulls/6/commits"):
                    return []
                if path.endswith("/pulls/6"):
                    return {"number": 6, "state": "open", "merged": False,
                            "head": {"ref": "feature", "sha": "cafe"},
                            "requested_reviewers": [{"login": "carol"}]}
                raise AssertionError(f"unexpected {method} {url}")

            with self.subTest(case=label):
                result = self.invoke(["pr", "view", "6", "--reviews"], env=GITEA_ENV, http=http)
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.json(result)["review_decision"], want)

    def test_pr_view_gitea_reviews_read_every_page(self):
        def pages(items, size):
            def http(method, url, body):
                path = url.split("?")[0]
                if path.endswith("/pulls/6/reviews"):
                    page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["page"][0])
                    chunk = items[(page - 1) * size : page * size]
                    return {"message": "not a list"} if None in chunk else chunk
                if path.endswith("/pulls/6/commits"):
                    return []
                if path.endswith("/pulls/6"):
                    return {"number": 6, "state": "open", "merged": False,
                            "head": {"ref": "feature", "sha": "cafe"}}
                raise AssertionError(f"unexpected {method} {url}")
            return http

        approval = {"user": {"login": "alice"}, "state": "APPROVED", "official": True}
        rejection = {"user": {"login": "bob"}, "state": "REQUEST_CHANGES", "official": True}
        for label, items, size, want in (
            ("server caps pages at 30", [approval] * 30 + [rejection], 30, "changes_requested"),
            ("201 reviews", [approval] * 201, 50, None),
            ("200 reviews", [approval] * 200, 50, "approved"),
            ("non-list page after approvals", [approval] * 50 + [None], 50, None),
        ):
            with self.subTest(case=label):
                result = self.invoke(
                    ["pr", "view", "6", "--reviews"], env=GITEA_ENV, http=pages(items, size)
                )
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.json(result)["review_decision"], want)

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

    def test_label_create_if_not_exists(self):
        posted = []

        def http(method, url, body):
            if method == "GET" and url.split("?")[0].endswith("/labels"):
                return [
                    {"name": "bug", "description": "broken", "color": "#d73a4a"}
                ]
            if method == "POST":
                posted.append((url, body))
                return {
                    "name": body["name"],
                    "description": body["description"],
                    "color": body["color"],
                }
            raise AssertionError(f"unexpected {method} {url}")

        result = self.invoke(
            [
                "label",
                "create",
                "bug",
                "--description",
                "ignored",
                "--color",
                "#000000",
                "--if-not-exists",
            ],
            env=TOKEN_ENV,
            http=http,
        )
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(posted, [])
        self.assertEqual(
            self.json(result),
            {"name": "bug", "description": "broken", "color": "d73a4a"},
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
