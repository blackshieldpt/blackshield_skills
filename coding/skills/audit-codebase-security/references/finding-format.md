# Security Finding Format

## Contents

- [Finding quality rules](#finding-quality-rules)
- [Markdown contract](#markdown-contract)
- [JSON contract](#json-contract)
- [JSON example](#json-example)

## Finding quality rules

- Lead with the concrete root cause and affected boundary.
- Use a stable taxonomy ID from `vulnerability-taxonomy.md`.
- Cite exact repository-relative paths and one-based line numbers.
- Show the relevant source-to-sink or invariant path.
- State attacker prerequisites and impact without exaggeration.
- Explain why visible defenses do not stop the path.
- Recommend the control location and security property, not just a library swap.
- Give a safe validation step.
- Redact credentials, tokens, private keys, and sensitive personal data.
- Merge duplicate manifestations that share one root cause and remediation.

## Markdown contract

Order findings by severity, then confidence. Put confirmed findings before coverage
notes.

```markdown
## [SEC-001] Short root-cause title

- Severity: High
- Confidence: High
- Taxonomy: AUTHZ-02 — IDOR / user-controlled object key
- Location: `src/routes/documents.ts:84` (`downloadDocument`)

Claim: An authenticated user can download another tenant's document because the
lookup uses a request-controlled document ID without tenant or ownership scoping.

Evidence:
- `documentId` comes from the route at `src/routes/documents.ts:78`.
- `documents.findById(documentId)` performs a global lookup at line 84.
- The handler checks only that a user is authenticated; no caller or database policy
  constrains the document's tenant.

Exploit path: A low-privilege user supplies another tenant's document ID to
`GET /documents/:documentId/download`; the handler returns that document.

Preconditions: The attacker needs any valid account and a target document ID.

Impact: Cross-tenant disclosure of stored documents.

Remediation: Scope the lookup by both `documentId` and the authenticated tenant (or
enforce an equivalent database policy), and return not-found for unauthorized
objects. Apply the same policy to preview, export, and batch handlers.

Validation: Add an integration test in which tenant A requests tenant B's document
ID and assert that no document bytes or metadata are returned.
```

After findings, include:

```markdown
## Coverage

- Mode and scope:
- Components and entry points reviewed:
- Taxonomy families reviewed:
- Tools/checks run:
- Exclusions and limitations:
```

If no finding survives validation, write `No confirmed security vulnerabilities
were found in the reviewed scope` and still include coverage.

## JSON contract

Use JSON only when requested. Validate it with:

```text
python3 <skill-directory>/scripts/validate_findings.py report.json
```

Top-level object:

| Field | Type | Requirements |
|---|---|---|
| `schema_version` | integer | Must be `1` |
| `scope` | object | Required |
| `summary` | object | Required |
| `findings` | array | Required; may be empty |

`scope`:

| Field | Type | Requirements |
|---|---|---|
| `mode` | string | `full`, `targeted`, or `diff` |
| `root` | string | Non-empty audit root |
| `target` | string or null | Named target/family/component |
| `commit_range` | string or null | Diff range when applicable |
| `excluded` | array of strings | Explicit exclusions |

`summary`:

| Field | Type | Requirements |
|---|---|---|
| `finding_count` | integer | Must equal `findings.length` |
| `reviewed` | array of strings | Components/entry points reviewed |
| `taxonomy_families` | array of strings | Stable family prefixes |
| `limitations` | array of strings | Missing evidence and exclusions |

Each finding:

| Field | Type | Requirements |
|---|---|---|
| `id` | string | Unique `SEC-NNN` identifier |
| `title` | string | Non-empty root-cause title |
| `taxonomy_id` | string | Stable ID such as `AUTHZ-02` |
| `severity` | string | `critical`, `high`, `medium`, or `low` |
| `confidence` | string | `high` or `medium` |
| `locations` | array | At least one location |
| `claim` | string | Non-empty |
| `evidence` | array of strings | At least one evidence item |
| `exploit_path` | string | Non-empty |
| `preconditions` | string | Non-empty; use `"None"` when truly none |
| `impact` | string | Non-empty |
| `remediation` | string | Non-empty |
| `validation` | string | Non-empty safe validation step |

Each location:

| Field | Type | Requirements |
|---|---|---|
| `path` | string | Non-empty repository-relative path |
| `line` | integer | One-based positive line |
| `symbol` | string or null | Function/class/resource name |

Unknown fields are allowed for forward compatibility, but required fields and enum
values must remain valid.

## JSON example

```json
{
  "schema_version": 1,
  "scope": {
    "mode": "targeted",
    "root": ".",
    "target": "document download authorization",
    "commit_range": null,
    "excluded": []
  },
  "summary": {
    "finding_count": 1,
    "reviewed": ["HTTP document routes", "document repository"],
    "taxonomy_families": ["AUTHZ"],
    "limitations": ["Database row-level security was not available"]
  },
  "findings": [
    {
      "id": "SEC-001",
      "title": "Document lookup is not tenant scoped",
      "taxonomy_id": "AUTHZ-02",
      "severity": "high",
      "confidence": "high",
      "locations": [
        {
          "path": "src/routes/documents.ts",
          "line": 84,
          "symbol": "downloadDocument"
        }
      ],
      "claim": "An authenticated user can retrieve another tenant's document.",
      "evidence": [
        "The request path controls documentId.",
        "The repository performs a global ID lookup without a tenant predicate."
      ],
      "exploit_path": "Request another tenant's document ID through the download route.",
      "preconditions": "Any authenticated account and a target document ID.",
      "impact": "Cross-tenant disclosure of stored documents.",
      "remediation": "Scope document lookup and equivalent handlers to the authenticated tenant.",
      "validation": "Assert that tenant A receives no bytes or metadata for tenant B's document ID."
    }
  ]
}
```
