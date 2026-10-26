#!/usr/bin/env python3
"""Validate machine-readable security audit findings without dependencies."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

FINDING_ID_RE = re.compile(r"^SEC-[0-9]{3,}$")
TAXONOMY_ID_RE = re.compile(
    r"^(?:AUTHN|AUTHZ|CLIENT|CONC|CRYPTO|DATA|DOS|FILE|INJ|LOGIC|MEM|OPS|PARSE|SESS|VAL|WEB)-[0-9]{2}$"
)
SEVERITIES = frozenset({"critical", "high", "medium", "low"})
CONFIDENCES = frozenset({"high", "medium"})
MODES = frozenset({"full", "targeted", "diff"})


def is_non_empty_string(value: object) -> bool:
    """Return whether a value is a non-empty string."""

    return isinstance(value, str) and bool(value.strip())


def require_string(
    value: object,
    path: str,
    errors: list[str],
    *,
    nullable: bool = False,
) -> None:
    """Validate a required string field."""

    if nullable and value is None:
        return
    if not is_non_empty_string(value):
        errors.append(
            f"{path} must be a non-empty string" + (" or null" if nullable else "")
        )


def require_string_list(value: object, path: str, errors: list[str]) -> None:
    """Validate an array of strings."""

    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return
    for index, item in enumerate(value):
        if not is_non_empty_string(item):
            errors.append(f"{path}[{index}] must be a non-empty string")


def validate_location(
    value: object,
    path: str,
    errors: list[str],
) -> None:
    """Validate one finding location."""

    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    require_string(value.get("path"), f"{path}.path", errors)
    line = value.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        errors.append(f"{path}.line must be a positive integer")
    require_string(value.get("symbol"), f"{path}.symbol", errors, nullable=True)


def validate_finding(
    value: object,
    index: int,
    seen_ids: set[str],
    errors: list[str],
) -> None:
    """Validate one security finding."""

    path = f"findings[{index}]"
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return

    finding_id = value.get("id")
    if not isinstance(finding_id, str) or not FINDING_ID_RE.fullmatch(finding_id):
        errors.append(f"{path}.id must match SEC-NNN")
    elif finding_id in seen_ids:
        errors.append(f"{path}.id duplicates {finding_id}")
    else:
        seen_ids.add(finding_id)

    require_string(value.get("title"), f"{path}.title", errors)
    taxonomy_id = value.get("taxonomy_id")
    if not isinstance(taxonomy_id, str) or not TAXONOMY_ID_RE.fullmatch(taxonomy_id):
        errors.append(f"{path}.taxonomy_id is not a recognized stable taxonomy ID")

    severity = value.get("severity")
    if severity not in SEVERITIES:
        errors.append(f"{path}.severity must be one of {sorted(SEVERITIES)}")
    confidence = value.get("confidence")
    if confidence not in CONFIDENCES:
        errors.append(f"{path}.confidence must be one of {sorted(CONFIDENCES)}")

    locations = value.get("locations")
    if not isinstance(locations, list) or not locations:
        errors.append(f"{path}.locations must be a non-empty array")
    else:
        for location_index, location in enumerate(locations):
            validate_location(location, f"{path}.locations[{location_index}]", errors)

    for field_name in (
        "claim",
        "exploit_path",
        "preconditions",
        "impact",
        "remediation",
        "validation",
    ):
        require_string(value.get(field_name), f"{path}.{field_name}", errors)
    evidence = value.get("evidence")
    require_string_list(evidence, f"{path}.evidence", errors)
    if isinstance(evidence, list) and not evidence:
        errors.append(f"{path}.evidence must contain at least one item")


def validate_report(value: object) -> list[str]:
    """Return all validation errors for an audit report."""

    errors: list[str] = []
    if not isinstance(value, dict):
        return ["report must be a JSON object"]

    if value.get("schema_version") != 1:
        errors.append("schema_version must equal 1")

    scope = value.get("scope")
    if not isinstance(scope, dict):
        errors.append("scope must be an object")
    else:
        if scope.get("mode") not in MODES:
            errors.append(f"scope.mode must be one of {sorted(MODES)}")
        require_string(scope.get("root"), "scope.root", errors)
        require_string(scope.get("target"), "scope.target", errors, nullable=True)
        require_string(
            scope.get("commit_range"),
            "scope.commit_range",
            errors,
            nullable=True,
        )
        require_string_list(scope.get("excluded"), "scope.excluded", errors)

    summary = value.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
    else:
        finding_count = summary.get("finding_count")
        if (
            not isinstance(finding_count, int)
            or isinstance(finding_count, bool)
            or finding_count < 0
        ):
            errors.append("summary.finding_count must be a non-negative integer")
        require_string_list(summary.get("reviewed"), "summary.reviewed", errors)
        require_string_list(
            summary.get("taxonomy_families"),
            "summary.taxonomy_families",
            errors,
        )
        require_string_list(summary.get("limitations"), "summary.limitations", errors)

    findings = value.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be an array")
        return errors

    if isinstance(summary, dict):
        finding_count = summary.get("finding_count")
        if isinstance(finding_count, int) and not isinstance(finding_count, bool):
            if finding_count != len(findings):
                errors.append("summary.finding_count must equal the number of findings")

    seen_ids: set[str] = set()
    for index, finding in enumerate(findings):
        validate_finding(finding, index, seen_ids, errors)
    return errors


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Validate an audit-codebase-security JSON report."
    )
    parser.add_argument("report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the findings validator CLI."""

    args = parse_args(argv)
    try:
        with args.report.open(encoding="utf-8") as handle:
            report: Any = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"error: cannot read report: {error}", file=sys.stderr)
        return 2

    errors = validate_report(report)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
