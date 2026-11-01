#!/usr/bin/env python3
"""Forge adapter: one CLI contract over GitHub, Gitea and Forgejo.

Read commands print one JSON object to stdout; ``pr diff`` prints raw text.
Errors go to stderr with a non-zero exit status:

    0 success
    1 operation failed (non-auth API/CLI failure)
    2 usage, unsupported forge, or missing configuration
    3 authentication/authorization
    4 not found
    5 conflict

Configuration:

    FORGE_TYPE=github|gitea|forgejo  wins over hostname detection
    FORGE_URL                        base URL, may include a subpath (Gitea/Forgejo)
    FORGE_TOKEN, GITEA_TOKEN, FORGEJO_TOKEN
                                     API token; anonymous reads are allowed,
                                     write commands exit 3 without one

Token values are never printed; messages name the environment variable only.

Standard library only (Python 3.9+).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5

DEFAULT_LIMIT = 50
DEFAULT_TIMEOUT = 30
PR_HEAD_SCAN_LIMIT = 1000
PR_COMMIT_LIMIT = 100  # gh pr view returns at most 100 commits; cap Gitea/Forgejo the same
MERGE_SHA_ATTEMPTS = 3
MERGE_SHA_DELAY = 0.5
SERVER_FORGE_TYPES = ("github", "gitea", "forgejo")
TOKEN_ENV_VARS = ("FORGE_TOKEN", "GITEA_TOKEN", "FORGEJO_TOKEN")

STATE_GH_SUCCESS = {"SUCCESS"}
STATE_GH_PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
STATE_GH_FAILURE = {
    "FAILURE",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
STATE_GH_UNKNOWN = {"NEUTRAL", "SKIPPED", "STALE"}

GITEA_STATEMAP = {
    "success": "success",
    "pending": "pending",
    "failure": "failure",
    "error": "failure",
    "warning": "unknown",
    "neutral": "unknown",
    "skipped": "unknown",
}

CLOSING_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)"
)


class ForgeError(Exception):
    """An adapter failure carrying the contract exit status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Context:
    """Resolved forge configuration for one command."""

    def __init__(
        self,
        forge: str,
        host: str,
        owner: str,
        name: str,
        base_url: str = "",
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.forge = forge
        self.host = host
        self.owner = owner
        self.name = name
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


# ---------------------------------------------------------------------------
# Process and HTTP transports
# ---------------------------------------------------------------------------


def run_command(cmd: list[str], timeout: Optional[float] = None) -> str:
    """Run a CLI command and return stdout, mapping failures to exit codes."""

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
    except FileNotFoundError:
        raise ForgeError(EXIT_USAGE, f"{cmd[0]} is not installed")
    except subprocess.TimeoutExpired:
        raise ForgeError(EXIT_FAILED, f"{cmd[0]} timed out")
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or f"{cmd[0]} failed"
        raise ForgeError(_command_exit_code(stderr), _redact(stderr, None))
    return proc.stdout


def _command_exit_code(stderr: str) -> int:
    text = stderr.lower()
    if "authentication" in text or "not logged in" in text or "bad credentials" in text:
        return EXIT_AUTH
    if "could not resolve" in text or "not found" in text:
        return EXIT_NOT_FOUND
    if "merge conflict" in text or "conflict" in text:
        return EXIT_CONFLICT
    return EXIT_FAILED


def _http_status_code(status: int) -> int:
    if status in (401, 403):
        return EXIT_AUTH
    if status == 404:
        return EXIT_NOT_FOUND
    if status == 409:
        return EXIT_CONFLICT
    return EXIT_FAILED


def _redact(text: str, token: Optional[str]) -> str:
    """Remove any token value from a message before it is printed."""

    if token:
        text = text.replace(token, "***")
    for env_name in TOKEN_ENV_VARS:
        value = os.environ.get(env_name)
        if value:
            text = text.replace(value, "***")
    return text


def _http_request(
    ctx: Context,
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    accept: str = "application/json",
) -> bytes:
    url = ctx.base_url + path
    if params:
        clean = {key: value for key, value in params.items() if value is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
    data = None
    headers = {"Accept": accept, "User-Agent": "blackshield-forge-adapter"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if ctx.token:
        headers["Authorization"] = f"token {ctx.token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=ctx.timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        detail = _http_detail(body)
        message = f"HTTP {exc.code}"
        if detail:
            message += f": {detail}"
        raise ForgeError(_http_status_code(exc.code), _redact(message, ctx.token))
    except urllib.error.URLError as exc:
        raise ForgeError(
            EXIT_FAILED,
            _redact(f"forge request failed: {exc.reason}", ctx.token),
        )


def _http_detail(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return body.decode("utf-8", "replace").strip()[:500]
    if isinstance(data, dict):
        for key in ("message", "error", "url"):
            if data.get(key):
                return str(data[key])
    return ""


def _http_json(
    ctx: Context,
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> Any:
    raw = _http_request(ctx, method, path, payload=payload, params=params)
    if not raw.strip():
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ForgeError(EXIT_FAILED, "forge returned invalid JSON")


def _http_text(ctx: Context, path: str) -> str:
    raw = _http_request(ctx, "GET", path, accept="text/plain")
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Detection and configuration
# ---------------------------------------------------------------------------


def git_remote_url() -> Optional[str]:
    """Return ``origin``'s URL, or ``upstream`` when no origin is configured."""

    for remote in ("origin", "upstream"):
        try:
            return run_command(["git", "remote", "get-url", remote]).strip()
        except ForgeError:
            continue
    return None


def parse_remote(url: str) -> Optional[dict[str, str]]:
    """Parse ssh/https/scp-like remote URLs into host, owner and name."""

    url = url.strip()
    if not url:
        return None
    host = ""
    path = ""
    scp = re.match(r"^[^/@]+@([^:/]+):(.+)$", url)
    if scp:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme and parsed.netloc:
            host = parsed.hostname or ""
            path = parsed.path
        elif "://" not in url and "/" in url:
            # Bare host/owner/name.
            host, _, path = url.partition("/")
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    owner = parts[-2]
    if not host or not owner or not name:
        return None
    return {"host": host.lower(), "owner": owner, "name": name}


def _gh_available(host: str = "") -> bool:
    args = ["gh", "auth", "status"]
    if host:
        args += ["--hostname", host]
    try:
        run_command(args)
        return True
    except ForgeError:
        return False


def detect_forge(remote: Optional[dict[str, str]]) -> str:
    """Return github, gitea or forgejo, or raise exit 2 when unsupported."""

    forced = os.environ.get("FORGE_TYPE", "").strip().lower()
    if forced:
        if forced not in SERVER_FORGE_TYPES:
            raise ForgeError(EXIT_USAGE, f"unsupported forge: {forced}")
        return forced

    host = (remote or {}).get("host", "").lower()
    if host == "github.com" or host.startswith("github."):
        return "github"
    if host == "codeberg.org":
        return "forgejo"
    labels = host.split(".")
    if "forgejo" in labels:
        return "forgejo"
    if "gitea" in labels:
        return "gitea"
    if os.environ.get("FORGE_URL"):
        return "gitea"
    if _gh_available(host):
        return "github"
    raise ForgeError(
        EXIT_USAGE,
        f"unsupported forge: {host or 'no origin remote'}; set FORGE_TYPE",
    )


def _token() -> str:
    for env_name in TOKEN_ENV_VARS:
        value = os.environ.get(env_name)
        if value:
            return value
    return ""


def _require_token(ctx: Context) -> None:
    if not ctx.token:
        raise ForgeError(
            EXIT_AUTH,
            "authentication required: set FORGE_TOKEN, GITEA_TOKEN or FORGEJO_TOKEN",
        )


def build_context(repo_override: Optional[str] = None) -> Context:
    parts = repo_override.split("/") if repo_override else []
    if repo_override and (len(parts) not in (2, 3) or not all(parts)):
        raise ForgeError(EXIT_USAGE, "repo must be OWNER/NAME or HOST/OWNER/NAME")
    if len(parts) == 3:
        host, owner, name = parts
        forge = detect_forge({"host": host, "owner": owner, "name": name})
    else:
        remote = parse_remote(git_remote_url() or "")
        forge = detect_forge(remote)
        owner = remote["owner"] if remote else ""
        name = remote["name"] if remote else ""
        host = remote["host"] if remote else ""
        if len(parts) == 2:
            owner, name = parts
    if not owner or not name:
        raise ForgeError(
            EXIT_USAGE,
            "missing repository: no origin/upstream remote and no --repo",
        )

    if forge == "github":
        host = host or "github.com"
        return Context(forge=forge, host=host, owner=owner, name=name)

    forge_url = os.environ.get("FORGE_URL", "").strip()
    if forge_url:
        base = forge_url.rstrip("/")
    elif host:
        base = f"https://{host}"
    else:
        raise ForgeError(
            EXIT_USAGE,
            "missing configuration: set FORGE_URL or configure an origin remote",
        )
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    effective_host = urllib.parse.urlparse(base).hostname or host
    return Context(
        forge=forge,
        host=effective_host,
        owner=owner,
        name=name,
        base_url=base,
        token=_token(),
    )


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def _lower(value: Any) -> str:
    return str(value or "").lower()


def _norm_issue_gh(data: dict[str, Any], with_comments: bool = False) -> dict[str, Any]:
    issue = {
        "number": data.get("number"),
        "title": data.get("title", ""),
        "body": data.get("body") or "",
        "state": _lower(data.get("state")),
        "labels": [label.get("name", "") for label in data.get("labels") or []],
        "url": data.get("url", ""),
        "author": (data.get("author") or {}).get("login") or "",
        "created_at": data.get("createdAt") or data.get("created_at") or "",
    }
    if with_comments:
        issue["comments"] = [
            _norm_comment_gh(comment) for comment in data.get("comments") or []
        ]
    return issue


def _norm_issue_gitea(data: dict[str, Any], with_comments: Optional[list] = None) -> dict[str, Any]:
    issue = {
        "number": data.get("number"),
        "title": data.get("title", ""),
        "body": data.get("body") or "",
        "state": _lower(data.get("state")),
        "labels": [label.get("name", "") for label in data.get("labels") or []],
        "url": data.get("html_url", ""),
        "author": (data.get("user") or {}).get("login") or "",
        "created_at": data.get("created_at") or "",
    }
    if with_comments is not None:
        issue["comments"] = [_norm_comment_gitea(comment) for comment in with_comments]
    return issue


def _norm_comment_gitea(data: dict[str, Any]) -> dict[str, Any]:
    created = data.get("created_at") or ""
    updated = data.get("updated_at") or ""
    return {
        "author": (data.get("user") or {}).get("login") or "",
        "body": data.get("body") or "",
        "created_at": created,
        "edited": bool(created and updated and created != updated),
    }


def _norm_comment_gh(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "author": (data.get("author") or {}).get("login") or "",
        "body": data.get("body") or "",
        "created_at": data.get("createdAt") or data.get("created_at") or "",
        "edited": bool(data.get("includesCreatedEdit")),
    }


def _closing_issues(body: str) -> list[int]:
    seen: list[int] = []
    for match in CLOSING_RE.findall(body or ""):
        number = int(match)
        if number not in seen:
            seen.append(number)
    return seen


def _split_closing_refs(
    data: dict[str, Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    segments = [p for p in urllib.parse.urlparse(data.get("url") or "").path.split("/") if p]
    pr_repo = "/".join(segments[:2]).lower() if len(segments) >= 2 else ""
    same: list[Any] = []
    external: list[dict[str, Any]] = []
    for item in data.get("closingIssuesReferences") or []:
        repository = item.get("repository") or {}
        owner = (repository.get("owner") or {}).get("login")
        name = repository.get("name")
        if not pr_repo or not owner or not name or f"{owner}/{name}".lower() == pr_repo:
            same.append(item.get("number"))
        else:
            external.append({"repo": f"{owner}/{name}", "number": item.get("number")})
    return same, external


def _norm_pr_gh(data: dict[str, Any]) -> dict[str, Any]:
    closing, closing_external = _split_closing_refs(data)
    merged = _lower(data.get("state")) == "merged"
    commits = data.get("commits") or []
    return {
        "number": data.get("number"),
        "title": data.get("title", ""),
        "body": data.get("body") or "",
        "state": "merged" if merged else _lower(data.get("state")),
        "merged": merged,
        "merge_commit": (data.get("mergeCommit") or {}).get("oid"),
        "url": data.get("url", ""),
        "base": data.get("baseRefName", ""),
        "head": data.get("headRefName", ""),
        "head_sha": data.get("headRefOid", ""),
        "author": (data.get("author") or {}).get("login") or "",
        "closing_issues": closing,
        "closing_issues_external": closing_external,
        "commits": [item.get("oid", "") for item in commits],
        "commit_messages": [
            {
                "sha": item.get("oid", ""),
                "message": (
                    (item.get("messageHeadline") or "").strip()
                    + ("\n\n" + item["messageBody"].strip() if item.get("messageBody") else "")
                ),
            }
            for item in commits
        ],
    }


def _norm_pr_gitea(
    data: dict[str, Any], commits: Optional[list] = None
) -> dict[str, Any]:
    merged = bool(data.get("merged"))
    base = data.get("base") or {}
    head = data.get("head") or {}
    return {
        "number": data.get("number"),
        "title": data.get("title", ""),
        "body": data.get("body") or "",
        "state": "merged" if merged else _lower(data.get("state")),
        "merged": merged,
        "merge_commit": data.get("merge_commit_sha") or None,
        "url": data.get("html_url", ""),
        "base": base.get("ref", ""),
        "head": head.get("ref", ""),
        "head_sha": head.get("sha", ""),
        "author": (data.get("user") or {}).get("login") or "",
        "closing_issues": _closing_issues(data.get("body") or ""),
        "closing_issues_external": [],
        "commits": [item.get("sha", "") for item in commits or []],
        "commit_messages": [
            {
                "sha": item.get("sha", ""),
                "message": ((item.get("commit") or {}).get("message") or "").strip(),
            }
            for item in commits or []
        ],
        "head_label": head.get("label", ""),
    }


def _norm_label(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": data.get("name", ""),
        "description": data.get("description") or "",
        "color": (data.get("color") or "").lstrip("#"),
    }


def _norm_label_gh(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": data.get("name", ""),
        "description": data.get("description") or "",
        "color": (data.get("color") or "").lstrip("#"),
    }


# ---------------------------------------------------------------------------
# Checks normalization
# ---------------------------------------------------------------------------


def _map_check_state(state: str) -> str:
    value = (state or "").upper()
    if value in STATE_GH_SUCCESS:
        return "success"
    if value in STATE_GH_PENDING:
        return "pending"
    if value in STATE_GH_FAILURE:
        return "failure"
    if value in STATE_GH_UNKNOWN:
        return "unknown"
    return "unknown"


def _overall(state_values: list[str], empty: bool) -> str:
    if empty:
        return "no_checks"
    if "failure" in state_values:
        return "failure"
    if "pending" in state_values:
        return "pending"
    if state_values and all(value == "success" for value in state_values):
        return "success"
    return "unknown"


def _checks_gh(data: dict[str, Any]) -> dict[str, Any]:
    rollup = data.get("statusCheckRollup") or []
    statuses = []
    for item in rollup:
        typename = item.get("__typename", "")
        if typename == "CheckRun":
            name = item.get("name", "")
            mapped = _map_check_state(item.get("conclusion") or item.get("status", ""))
        else:
            name = item.get("context") or item.get("name", "")
            mapped = _map_check_state(item.get("state", ""))
        statuses.append(
            {
                "name": name,
                "state": mapped,
                "target_url": item.get("targetUrl") or item.get("detailsUrl") or "",
            }
        )
    values = [status["state"] for status in statuses]
    return {"state": _overall(values, not statuses), "statuses": statuses}


def _checks_gitea(data: dict[str, Any]) -> dict[str, Any]:
    raw_statuses = data.get("statuses") or []
    statuses = []
    for item in raw_statuses:
        statuses.append(
            {
                "name": item.get("context", ""),
                "state": GITEA_STATEMAP.get(_lower(item.get("status")), "unknown"),
                "target_url": item.get("target_url") or "",
            }
        )
    values = [status["state"] for status in statuses]
    total = data.get("total_count")
    empty = not statuses and (not data.get("state") or total == 0)
    if empty:
        return {"state": "no_checks", "statuses": []}
    if values:
        return {"state": _overall(values, False), "statuses": statuses}
    mapped = GITEA_STATEMAP.get(_lower(data.get("state")), "unknown")
    return {"state": mapped, "statuses": []}


# ---------------------------------------------------------------------------
# GitHub backend
# ---------------------------------------------------------------------------


def _gh_json(args: list[str]) -> Any:
    output = run_command(["gh"] + args)
    try:
        return json.loads(output)
    except ValueError:
        raise ForgeError(EXIT_FAILED, "gh returned invalid JSON")


def _gh_selector(ctx: Context) -> list[str]:
    if ctx.host and ctx.host != "github.com":
        return ["-R", f"{ctx.host}/{ctx.slug}"]
    return ["-R", ctx.slug]


GH_REPO_FIELDS = (
    "nameWithOwner,owner,name,visibility,isPrivate,isFork,parent,"
    "hasIssuesEnabled,defaultBranchRef,url"
)
GH_ISSUE_FIELDS = "number,title,body,state,labels,url,author,createdAt"
GH_PR_FIELDS = (
    "number,title,body,state,mergeCommit,url,baseRefName,headRefName,"
    "headRefOid,author,closingIssuesReferences,commits"
)
# pr list omits commits: 50 PRs x 100 commits exceeds GitHub's 500,000-node GraphQL
# limit. List items carry commits: [] (as on Gitea/Forgejo); pr view carries them.
GH_PR_LIST_FIELDS = (
    "number,title,body,state,mergeCommit,url,baseRefName,headRefName,"
    "headRefOid,author,closingIssuesReferences"
)


def _gh_repo(ctx: Context) -> dict[str, Any]:
    data = _gh_json(["repo", "view"] + _gh_selector(ctx) + ["--json", GH_REPO_FIELDS])
    owner = (data.get("owner") or {}).get("login") or ""
    name = data.get("name") or ""
    if not owner or not name:
        owner, _, name = (data.get("nameWithOwner") or "/").partition("/")
    parent = data.get("parent")
    return {
        "forge": "github",
        "host": ctx.host,
        "owner": owner,
        "name": name,
        "visibility": _lower(data.get("visibility")) or (
            "private" if data.get("isPrivate") else "public"
        ),
        "private": bool(data.get("isPrivate")),
        "fork": bool(data.get("isFork")),
        "parent": _gh_parent(parent),
        "has_issues": bool(data.get("hasIssuesEnabled")),
        "default_branch": (data.get("defaultBranchRef") or {}).get("name"),
    }


def _gh_parent(parent: Optional[dict[str, Any]]) -> Optional[str]:
    if not parent:
        return None
    if parent.get("nameWithOwner"):
        return parent["nameWithOwner"]
    login = (parent.get("owner") or {}).get("login")
    name = parent.get("name")
    return f"{login}/{name}" if login and name else None


def _gh_issue_list(ctx: Context, state: str, search: Optional[str], limit: int) -> list[dict[str, Any]]:
    args = ["issue", "list"] + _gh_selector(ctx) + [
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        GH_ISSUE_FIELDS,
    ]
    if search:
        args += ["--search", search]
    return [_norm_issue_gh(item) for item in _gh_json(args)]


def _gh_issue_view(ctx: Context, number: int, with_comments: bool = True) -> dict[str, Any]:
    fields = GH_ISSUE_FIELDS + (",comments" if with_comments else "")
    data = _gh_json(
        ["issue", "view", str(number)] + _gh_selector(ctx) + ["--json", fields]
    )
    return _norm_issue_gh(data, with_comments=with_comments)


def _gh_issue_create(ctx: Context, title: str, body: str, labels: list[str]) -> dict[str, Any]:
    args = ["issue", "create"] + _gh_selector(ctx) + [
        "--title",
        title,
        "--body",
        body,
    ]
    for label in labels:
        args += ["--label", label]
    output = run_command(["gh"] + args).strip()
    match = re.search(r"/issues/(\d+)", output)
    if not match:
        raise ForgeError(EXIT_FAILED, "gh issue create did not return an issue URL")
    return _gh_issue_view(ctx, int(match.group(1)), with_comments=False)


def _gh_issue_comment(ctx: Context, number: int, body: str) -> dict[str, Any]:
    run_command(
        ["gh", "issue", "comment", str(number)]
        + _gh_selector(ctx)
        + ["--body", body]
    )
    return {"number": number, "commented": True}


def _gh_issue_close(ctx: Context, number: int, comment: Optional[str]) -> dict[str, Any]:
    args = ["issue", "close", str(number)] + _gh_selector(ctx)
    if comment:
        args += ["--comment", comment]
    run_command(["gh"] + args)
    return _gh_issue_view(ctx, number, with_comments=False)


def _gh_pr_list(ctx: Context, head: Optional[str], state: str, limit: int) -> list[dict[str, Any]]:
    args = ["pr", "list"] + _gh_selector(ctx) + [
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        GH_PR_LIST_FIELDS,
    ]
    if head:
        args += ["--head", head]
    return [_norm_pr_gh(item) for item in _gh_json(args)]


def _gh_pr_view(ctx: Context, number: int) -> dict[str, Any]:
    data = _gh_json(
        ["pr", "view", str(number)] + _gh_selector(ctx) + ["--json", GH_PR_FIELDS]
    )
    return _norm_pr_gh(data)


def _gh_pr_create(
    ctx: Context, title: str, body: str, head: str, base: str
) -> dict[str, Any]:
    args = ["pr", "create"] + _gh_selector(ctx) + [
        "--title",
        title,
        "--body",
        body,
        "--head",
        head,
        "--base",
        base,
    ]
    output = run_command(["gh"] + args).strip()
    match = re.search(r"/pull/(\d+)", output)
    if not match:
        raise ForgeError(EXIT_FAILED, "gh pr create did not return a pull URL")
    return _gh_pr_view(ctx, int(match.group(1)))


def _gh_pr_edit(ctx: Context, number: int, body: str) -> dict[str, Any]:
    run_command(
        ["gh", "pr", "edit", str(number)] + _gh_selector(ctx) + ["--body", body]
    )
    return _gh_pr_view(ctx, number)


def _gh_pr_comment(ctx: Context, number: int, body: str) -> dict[str, Any]:
    run_command(
        ["gh", "pr", "comment", str(number)]
        + _gh_selector(ctx)
        + ["--body", body]
    )
    return {"number": number, "commented": True}


def _gh_pr_checks(ctx: Context, number: int) -> dict[str, Any]:
    data = _gh_json(
        ["pr", "view", str(number)]
        + _gh_selector(ctx)
        + ["--json", "statusCheckRollup"]
    )
    return _checks_gh(data)


def _gh_pr_merge(
    ctx: Context, number: int, method: str, head_sha: Optional[str]
) -> dict[str, Any]:
    if head_sha:
        data = _gh_json(
            [
                "api",
                "-X",
                "PUT",
                f"repos/{ctx.slug}/pulls/{number}/merge",
                "-f",
                f"merge_method={method}",
                "-f",
                f"sha={head_sha}",
            ]
        )
        merged = bool(data.get("merged"))
        if not merged:
            raise ForgeError(
                EXIT_CONFLICT,
                _redact(f"merge refused: {data.get('message', 'not merged')}", ctx.token),
            )
        return {
            "number": number,
            "merged": True,
            "merge_commit": data.get("sha"),
        }
    run_command(
        ["gh", "pr", "merge", str(number)]
        + _gh_selector(ctx)
        + ["--" + method]
    )
    view = _gh_pr_view(ctx, number)
    return {
        "number": number,
        "merged": bool(view["merged"]),
        "merge_commit": view["merge_commit"],
    }


def _gh_label_list(ctx: Context, limit: int) -> list[dict[str, Any]]:
    data = _gh_json(
        ["label", "list"] + _gh_selector(ctx) + [
            "--limit",
            str(limit),
            "--json",
            "name,description,color",
        ]
    )
    return [_norm_label_gh(item) for item in data]


def _gh_label_create(
    ctx: Context, name: str, description: str, color: str
) -> dict[str, Any]:
    run_command(
        ["gh", "label", "create", name]
        + _gh_selector(ctx)
        + ["--description", description, "--color", color.lstrip("#")]
    )
    return {
        "name": name,
        "description": description,
        "color": color.lstrip("#"),
    }


# ---------------------------------------------------------------------------
# Gitea/Forgejo backend
# ---------------------------------------------------------------------------


def _repo_path(ctx: Context, suffix: str = "") -> str:
    owner = urllib.parse.quote(ctx.owner, safe="")
    name = urllib.parse.quote(ctx.name, safe="")
    return f"/repos/{owner}/{name}{suffix}"


def _paginate(
    ctx: Context, path: str, params: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_size = min(limit, DEFAULT_LIMIT)
    page = 1
    while len(items) < limit:
        query = dict(params)
        query["limit"] = page_size
        query["page"] = page
        batch = _http_json(ctx, "GET", path, params=query)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return items[:limit]


def _scan_pages(
    ctx: Context,
    path: str,
    params: dict[str, Any],
    scan_limit: int,
    matches: Callable[[dict[str, Any]], bool],
    limit: int,
) -> list[dict[str, Any]]:
    """Walk pages up to ``scan_limit`` items, keeping up to ``limit`` matches."""

    found: list[dict[str, Any]] = []
    page_size = DEFAULT_LIMIT
    page = 1
    scanned = 0
    while scanned < scan_limit and len(found) < limit:
        query = dict(params)
        query["limit"] = page_size
        query["page"] = page
        batch = _http_json(ctx, "GET", path, params=query)
        if not isinstance(batch, list) or not batch:
            break
        scanned += len(batch)
        for item in batch:
            if matches(item):
                found.append(item)
                if len(found) >= limit:
                    break
        if len(batch) < page_size:
            break
        page += 1
    return found


def _gitea_repo(ctx: Context) -> dict[str, Any]:
    data = _http_json(ctx, "GET", _repo_path(ctx))
    parent = data.get("parent") or {}
    parent_name = parent.get("full_name") or ""
    if not parent_name and parent:
        parent_owner = (parent.get("owner") or {}).get("login") or ""
        parent_name = f"{parent_owner}/{parent.get('name', '')}".strip("/")
    if data.get("private"):
        visibility = "private"
    elif data.get("internal"):
        visibility = "internal"
    else:
        visibility = "public"
    return {
        "forge": ctx.forge,
        "host": ctx.host,
        "owner": (data.get("owner") or {}).get("login") or ctx.owner,
        "name": data.get("name") or ctx.name,
        "visibility": visibility,
        "private": bool(data.get("private")),
        "fork": bool(data.get("fork")),
        "parent": parent_name or None,
        "has_issues": bool(data.get("has_issues")),
        "default_branch": data.get("default_branch"),
    }


def _gitea_issue_list(
    ctx: Context, state: str, search: Optional[str], limit: int
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"type": "issues"}
    if state != "all":
        params["state"] = state
    if search:
        params["q"] = search
    items = _paginate(ctx, _repo_path(ctx, "/issues"), params, limit)
    return [_norm_issue_gitea(item) for item in items]


def _gitea_issue_view(ctx: Context, number: int) -> dict[str, Any]:
    data = _http_json(ctx, "GET", _repo_path(ctx, f"/issues/{number}"))
    comments = _http_json(
        ctx, "GET", _repo_path(ctx, f"/issues/{number}/comments")
    )
    issue = _norm_issue_gitea(data, with_comments=comments or [])
    issue["viewer"] = _gitea_viewer_login(ctx)
    return issue


def _gitea_labels(ctx: Context, limit: int) -> list[dict[str, Any]]:
    return _paginate(ctx, _repo_path(ctx, "/labels"), {}, limit)


def _gitea_resolve_labels(
    ctx: Context, names: list[str], create_missing: bool
) -> list[int]:
    if not names:
        return []
    existing = {label.get("name"): label.get("id") for label in _gitea_labels(ctx, 1000)}
    ids: list[int] = []
    for name in names:
        if name in existing:
            ids.append(existing[name])
            continue
        if not create_missing:
            raise ForgeError(
                EXIT_FAILED,
                f"label not found: {name} (pass --create-labels to create it)",
            )
        created = _http_json(
            ctx,
            "POST",
            _repo_path(ctx, "/labels"),
            payload={"name": name, "color": "ededed", "description": ""},
        )
        existing[name] = created.get("id")
        ids.append(created.get("id"))
    return ids


def _gitea_issue_create(
    ctx: Context,
    title: str,
    body: str,
    labels: list[str],
    create_missing: bool,
) -> dict[str, Any]:
    _require_token(ctx)
    payload: dict[str, Any] = {"title": title, "body": body}
    label_ids = _gitea_resolve_labels(ctx, labels, create_missing)
    if label_ids:
        payload["labels"] = label_ids
    data = _http_json(ctx, "POST", _repo_path(ctx, "/issues"), payload=payload)
    return _norm_issue_gitea(data)


def _gitea_issue_comment(ctx: Context, number: int, body: str) -> dict[str, Any]:
    _require_token(ctx)
    data = _http_json(
        ctx,
        "POST",
        _repo_path(ctx, f"/issues/{number}/comments"),
        payload={"body": body},
    )
    comment = _norm_comment_gitea(data)
    return {"number": number, "commented": True, "comment": comment}


def _gitea_issue_close(
    ctx: Context, number: int, comment: Optional[str]
) -> dict[str, Any]:
    _require_token(ctx)
    if comment:
        _gitea_issue_comment(ctx, number, comment)
    data = _http_json(
        ctx,
        "PATCH",
        _repo_path(ctx, f"/issues/{number}"),
        payload={"state": "closed"},
    )
    return _norm_issue_gitea(data)


def _gitea_pr_list(
    ctx: Context, head: Optional[str], state: str, limit: int
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    query_state = "closed" if state == "merged" else state
    if query_state != "all":
        params["state"] = query_state
    path = _repo_path(ctx, "/pulls")

    def matches(item: dict[str, Any]) -> bool:
        if state == "merged" and not item.get("merged"):
            return False
        if not head:
            return True
        head_data = item.get("head") or {}
        ref = head_data.get("ref") or ""
        label = head_data.get("label") or ""
        return head in (ref, label)

    if not head and state != "merged":
        items = _paginate(ctx, path, params, limit)
        return [_norm_pr_gitea(item) for item in items][:limit]

    # Forgejo ignores the `head` query parameter and filters merged state after
    # a page of closed PRs; scan pages so a match is not lost behind `limit`.
    found = _scan_pages(ctx, path, params, PR_HEAD_SCAN_LIMIT, matches, limit)
    return [_norm_pr_gitea(item) for item in found]


def _gitea_pr_view(ctx: Context, number: int) -> dict[str, Any]:
    data = _http_json(ctx, "GET", _repo_path(ctx, f"/pulls/{number}"))
    commits = _paginate(ctx, _repo_path(ctx, f"/pulls/{number}/commits"), {}, PR_COMMIT_LIMIT)
    return _norm_pr_gitea(data, commits=commits)


def _gitea_pr_create(
    ctx: Context, title: str, body: str, head: str, base: str
) -> dict[str, Any]:
    _require_token(ctx)
    data = _http_json(
        ctx,
        "POST",
        _repo_path(ctx, "/pulls"),
        payload={"title": title, "body": body, "head": head, "base": base},
    )
    return _norm_pr_gitea(data)


def _gitea_pr_edit(ctx: Context, number: int, body: str) -> dict[str, Any]:
    _require_token(ctx)
    data = _http_json(
        ctx,
        "PATCH",
        _repo_path(ctx, f"/pulls/{number}"),
        payload={"body": body},
    )
    return _norm_pr_gitea(data)


def _gitea_pr_comment(ctx: Context, number: int, body: str) -> dict[str, Any]:
    _require_token(ctx)
    data = _http_json(
        ctx,
        "POST",
        _repo_path(ctx, f"/issues/{number}/comments"),
        payload={"body": body},
    )
    return {"number": number, "commented": True, "comment": _norm_comment_gitea(data)}


def _gitea_pr_checks(ctx: Context, number: int) -> dict[str, Any]:
    pull = _http_json(ctx, "GET", _repo_path(ctx, f"/pulls/{number}"))
    head_sha = (pull.get("head") or {}).get("sha") or ""
    if not head_sha:
        raise ForgeError(EXIT_FAILED, f"pull {number} has no head SHA")
    data = _http_json(
        ctx, "GET", _repo_path(ctx, f"/commits/{head_sha}/status")
    )
    return _checks_gitea(data)


def _gitea_pr_merge(
    ctx: Context, number: int, method: str, head_sha: Optional[str]
) -> dict[str, Any]:
    _require_token(ctx)
    payload: dict[str, Any] = {"Do": method}
    if head_sha:
        payload["head_commit_id"] = head_sha
    try:
        _http_json(
            ctx, "POST", _repo_path(ctx, f"/pulls/{number}/merge"), payload=payload
        )
    except ForgeError as exc:
        if head_sha and exc.code in (EXIT_USAGE, EXIT_FAILED) and "head_commit_id" in exc.message:
            raise ForgeError(
                EXIT_USAGE,
                "forge cannot pin the merged head SHA; refusing to merge",
            )
        raise
    # The merge response carries no commit SHA on Forgejo; read it back from the
    # pull request. A fast-forward or rebase merge legitimately equals the head
    # SHA, so equality is allowed, but the head SHA is never substituted when
    # the server does not report a merge commit.
    merge_commit: Optional[str] = None
    for attempt in range(MERGE_SHA_ATTEMPTS):
        pull = _http_json(ctx, "GET", _repo_path(ctx, f"/pulls/{number}"))
        if isinstance(pull, dict):
            merge_commit = pull.get("merge_commit_sha") or None
        if merge_commit:
            break
        if attempt + 1 < MERGE_SHA_ATTEMPTS:
            time.sleep(MERGE_SHA_DELAY)
    return {
        "number": number,
        "merged": True,
        "merge_commit": merge_commit,
        "merge_commit_known": bool(merge_commit),
    }


def _gitea_label_list(ctx: Context, limit: int) -> list[dict[str, Any]]:
    return [_norm_label(item) for item in _gitea_labels(ctx, limit)]


def _gitea_label_create(
    ctx: Context, name: str, description: str, color: str
) -> dict[str, Any]:
    _require_token(ctx)
    data = _http_json(
        ctx,
        "POST",
        _repo_path(ctx, "/labels"),
        payload={
            "name": name,
            "description": description,
            "color": color.lstrip("#"),
        },
    )
    return _norm_label(data)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _read_body(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ForgeError(EXIT_USAGE, f"cannot read body file: {path}")


def cmd_detect(args: argparse.Namespace) -> Any:
    remote = parse_remote(git_remote_url() or "")
    return {"forge": detect_forge(remote)}


def _gh_viewer_login(ctx: Context) -> Optional[str]:
    try:
        data = _gh_json(["api", "user"])
    except ForgeError as exc:
        if exc.code == EXIT_AUTH:
            return None
        raise
    if not isinstance(data, dict):
        return None
    return data.get("login") or None


def _gitea_viewer_login(ctx: Context) -> Optional[str]:
    if not ctx.token:
        return None
    data = _http_json(ctx, "GET", "/user")
    if not isinstance(data, dict):
        return None
    return data.get("login") or None


def cmd_viewer(ctx: Context) -> Any:
    if ctx.forge == "github":
        return {"login": _gh_viewer_login(ctx)}
    return {"login": _gitea_viewer_login(ctx)}


def cmd_auth(ctx: Context) -> Any:
    if ctx.forge == "github":
        try:
            run_command(["gh", "auth", "status"])
        except ForgeError:
            return {"authenticated": False}
        return {"authenticated": True}
    return {"authenticated": bool(ctx.token)}


def cmd_repo(ctx: Context) -> Any:
    if ctx.forge == "github":
        return _gh_repo(ctx)
    return _gitea_repo(ctx)


def cmd_issue_list(ctx: Context, args: argparse.Namespace) -> Any:
    if ctx.forge == "github":
        return _gh_issue_list(ctx, args.state, args.search, args.limit)
    return _gitea_issue_list(ctx, args.state, args.search, args.limit)


def cmd_issue_view(ctx: Context, args: argparse.Namespace) -> Any:
    if ctx.forge == "github":
        issue = _gh_issue_view(ctx, args.number)
        issue["viewer"] = _gh_viewer_login(ctx)
        return issue
    return _gitea_issue_view(ctx, args.number)


def cmd_issue_create(ctx: Context, args: argparse.Namespace) -> Any:
    body = _read_body(args.body_file)
    if ctx.forge == "github":
        return _gh_issue_create(ctx, args.title, body, args.label)
    return _gitea_issue_create(
        ctx, args.title, body, args.label, args.create_labels
    )


def cmd_issue_comment(ctx: Context, args: argparse.Namespace) -> Any:
    body = _read_body(args.body_file)
    if ctx.forge == "github":
        return _gh_issue_comment(ctx, args.number, body)
    return _gitea_issue_comment(ctx, args.number, body)


def cmd_issue_close(ctx: Context, args: argparse.Namespace) -> Any:
    comment = _read_body(args.comment_file) if args.comment_file else None
    if ctx.forge == "github":
        return _gh_issue_close(ctx, args.number, comment)
    return _gitea_issue_close(ctx, args.number, comment)


def cmd_pr_list(ctx: Context, args: argparse.Namespace) -> Any:
    if ctx.forge == "github":
        return _gh_pr_list(ctx, args.head, args.state, args.limit)
    return _gitea_pr_list(ctx, args.head, args.state, args.limit)


def cmd_pr_view(ctx: Context, args: argparse.Namespace) -> Any:
    if ctx.forge == "github":
        return _gh_pr_view(ctx, args.number)
    return _gitea_pr_view(ctx, args.number)


def cmd_pr_diff(ctx: Context, args: argparse.Namespace) -> str:
    if ctx.forge == "github":
        return run_command(
            ["gh", "pr", "diff", str(args.number)] + _gh_selector(ctx)
        )
    return _http_text(ctx, _repo_path(ctx, f"/pulls/{args.number}.diff"))


def cmd_pr_create(ctx: Context, args: argparse.Namespace) -> Any:
    body = _read_body(args.body_file)
    if ctx.forge == "github":
        return _gh_pr_create(ctx, args.title, body, args.head, args.base)
    return _gitea_pr_create(ctx, args.title, body, args.head, args.base)


def cmd_pr_edit(ctx: Context, args: argparse.Namespace) -> Any:
    body = _read_body(args.body_file)
    if ctx.forge == "github":
        return _gh_pr_edit(ctx, args.number, body)
    return _gitea_pr_edit(ctx, args.number, body)


def cmd_pr_comment(ctx: Context, args: argparse.Namespace) -> Any:
    body = _read_body(args.body_file)
    if ctx.forge == "github":
        return _gh_pr_comment(ctx, args.number, body)
    return _gitea_pr_comment(ctx, args.number, body)


def cmd_pr_checks(ctx: Context, args: argparse.Namespace) -> Any:
    deadline = time.monotonic() + max(0, args.wait)
    while True:
        if ctx.forge == "github":
            result = _gh_pr_checks(ctx, args.number)
        else:
            result = _gitea_pr_checks(ctx, args.number)
        if result["state"] != "pending" or args.wait <= 0:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result
        time.sleep(min(5, remaining))


def cmd_pr_merge(ctx: Context, args: argparse.Namespace) -> Any:
    if ctx.forge == "github":
        return _gh_pr_merge(ctx, args.number, args.method, args.head_sha)
    return _gitea_pr_merge(ctx, args.number, args.method, args.head_sha)


def cmd_label_list(ctx: Context, args: argparse.Namespace) -> Any:
    if ctx.forge == "github":
        return _gh_label_list(ctx, args.limit)
    return _gitea_label_list(ctx, args.limit)


def cmd_label_create(ctx: Context, args: argparse.Namespace) -> Any:
    if args.if_not_exists:
        if ctx.forge == "github":
            labels = _gh_label_list(ctx, 1000)
        else:
            labels = _gitea_label_list(ctx, 1000)
        for label in labels:
            if label["name"] == args.name:
                return label
    if ctx.forge == "github":
        return _gh_label_create(ctx, args.name, args.description, args.color)
    return _gitea_label_create(ctx, args.name, args.description, args.color)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_repo(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=None, metavar="OWNER/NAME")


def _add_limit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="detect the forge type")

    viewer = sub.add_parser("viewer", help="show the authenticated user")
    _add_repo(viewer)

    auth = sub.add_parser("auth", help="show whether the forge is authenticated")
    _add_repo(auth)

    repo = sub.add_parser("repo", help="show the repository")
    _add_repo(repo)

    issue = sub.add_parser("issue", help="issue commands")
    issue_sub = issue.add_subparsers(dest="issue_command", required=True)

    issue_list = issue_sub.add_parser("list")
    _add_repo(issue_list)
    _add_limit(issue_list)
    issue_list.add_argument(
        "--state", choices=("all", "open", "closed"), default="open"
    )
    issue_list.add_argument("--search", default=None)

    issue_view = issue_sub.add_parser("view")
    _add_repo(issue_view)
    issue_view.add_argument("number", type=int)

    issue_create = issue_sub.add_parser("create")
    _add_repo(issue_create)
    issue_create.add_argument("--title", required=True)
    issue_create.add_argument("--body-file", required=True)
    issue_create.add_argument("--label", action="append", default=[])
    issue_create.add_argument("--create-labels", action="store_true")

    issue_comment = issue_sub.add_parser("comment")
    _add_repo(issue_comment)
    issue_comment.add_argument("number", type=int)
    issue_comment.add_argument("--body-file", required=True)

    issue_close = issue_sub.add_parser("close")
    _add_repo(issue_close)
    issue_close.add_argument("number", type=int)
    issue_close.add_argument("--comment-file", default=None)

    pr = sub.add_parser("pr", help="pull request commands")
    pr_sub = pr.add_subparsers(dest="pr_command", required=True)

    pr_list = pr_sub.add_parser("list")
    _add_repo(pr_list)
    _add_limit(pr_list)
    pr_list.add_argument("--head", default=None)
    pr_list.add_argument(
        "--state", choices=("open", "closed", "merged", "all"), default="open"
    )

    pr_view = pr_sub.add_parser("view")
    _add_repo(pr_view)
    pr_view.add_argument("number", type=int)

    pr_diff = pr_sub.add_parser("diff")
    _add_repo(pr_diff)
    pr_diff.add_argument("number", type=int)

    pr_create = pr_sub.add_parser("create")
    _add_repo(pr_create)
    pr_create.add_argument("--title", required=True)
    pr_create.add_argument("--body-file", required=True)
    pr_create.add_argument("--head", required=True)
    pr_create.add_argument("--base", required=True)

    pr_edit = pr_sub.add_parser("edit")
    _add_repo(pr_edit)
    pr_edit.add_argument("number", type=int)
    pr_edit.add_argument("--body-file", required=True)

    pr_comment = pr_sub.add_parser("comment")
    _add_repo(pr_comment)
    pr_comment.add_argument("number", type=int)
    pr_comment.add_argument("--body-file", required=True)

    pr_checks = pr_sub.add_parser("checks")
    _add_repo(pr_checks)
    pr_checks.add_argument("number", type=int)
    pr_checks.add_argument("--wait", type=int, default=0)
    pr_checks.add_argument("--require-green", action="store_true")

    pr_merge = pr_sub.add_parser("merge")
    _add_repo(pr_merge)
    pr_merge.add_argument("number", type=int)
    pr_merge.add_argument(
        "--method", choices=("merge", "squash", "rebase"), required=True
    )
    pr_merge.add_argument("--head-sha", default=None)

    label = sub.add_parser("label", help="label commands")
    label_sub = label.add_subparsers(dest="label_command", required=True)

    label_list = label_sub.add_parser("list")
    _add_repo(label_list)
    _add_limit(label_list)

    label_create = label_sub.add_parser("create")
    _add_repo(label_create)
    label_create.add_argument("name")
    label_create.add_argument("--description", required=True)
    label_create.add_argument("--color", required=True)
    label_create.add_argument("--if-not-exists", action="store_true")

    return parser


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "detect":
        return cmd_detect(args)

    ctx = build_context(args.repo)
    if args.command == "viewer":
        return cmd_viewer(ctx)
    if args.command == "auth":
        return cmd_auth(ctx)
    if args.command == "repo":
        return cmd_repo(ctx)

    if args.command == "issue":
        handler = {
            "list": cmd_issue_list,
            "view": cmd_issue_view,
            "create": cmd_issue_create,
            "comment": cmd_issue_comment,
            "close": cmd_issue_close,
        }[args.issue_command]
        return handler(ctx, args)

    if args.command == "pr":
        handler = {
            "list": cmd_pr_list,
            "view": cmd_pr_view,
            "diff": cmd_pr_diff,
            "create": cmd_pr_create,
            "edit": cmd_pr_edit,
            "comment": cmd_pr_comment,
            "checks": cmd_pr_checks,
            "merge": cmd_pr_merge,
        }[args.pr_command]
        return handler(ctx, args)

    handler = {
        "list": cmd_label_list,
        "create": cmd_label_create,
    }[args.label_command]
    return handler(ctx, args)


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        result = _dispatch(args)
    except ForgeError as exc:
        print(_redact(exc.message, None), file=sys.stderr)
        return exc.code

    if isinstance(result, str):
        sys.stdout.write(result)
        if result and not result.endswith("\n"):
            sys.stdout.write("\n")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    if getattr(args, "require_green", False) and isinstance(result, dict):
        if result.get("state") not in ("success", "no_checks"):
            return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
