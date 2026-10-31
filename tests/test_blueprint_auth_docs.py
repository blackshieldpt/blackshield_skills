"""Tests for the Blueprint authentication reference content."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH_DOC = REPO_ROOT / "blueprint/skills/blueprint/references/authentication.md"
CONFIG_DOC = REPO_ROOT / "blueprint/skills/blueprint/references/configuration-and-di.md"

COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def read(path: Path) -> str:
    """Return the file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def strip_go_comments(text: str) -> str:
    """Remove Go line and block comments so commented-out code cannot pass."""

    return COMMENT_RE.sub("", text)


def function_block(text: str, signature: str) -> str:
    """Return a top-level Go function, ending at the next bare closing brace."""

    start = text.find(signature)
    if start == -1:
        raise AssertionError(f"missing function signature: {signature}")

    lines = text[start:].splitlines()
    block = [lines[0]]
    for line in lines[1:]:
        block.append(line)
        if line == "}":
            break
    return "\n".join(block)


def normalise(text: str) -> str:
    """Collapse whitespace so line wrapping does not decide a check."""

    return " ".join(text.split())


class AuthReferenceTests(unittest.TestCase):
    """Pin the fail-closed guidance and the corrected reference details."""

    def test_fail_closed_guard(self) -> None:
        block = function_block(strip_go_comments(read(AUTH_DOC)), "func NewGoAuthService")
        plain = normalise(block)
        self.assertIn('if privPEM == "" || pubPEM == ""', plain)
        self.assertIn("if !allowEphemeral {", plain)
        self.assertIn(
            'return nil, nil, fmt.Errorf("JWT signing keys are required")', plain
        )
        # The guard must sit inside the key-emptiness check, not ahead of it.
        self.assertLess(
            plain.index('if privPEM == "" || pubPEM == ""'),
            plain.index("if !allowEphemeral {"),
            "the allowEphemeral guard must be nested under the empty-key check",
        )
        self.assertNotIn("\n    if !allowEphemeral {", block)

    def test_validate_chain(self) -> None:
        auth_doc = read(AUTH_DOC)
        config_doc = read(CONFIG_DOC)

        self.assertIn("if cfg.Auth == nil", auth_doc)
        self.assertIn("m.AbortFatal(cfg.Auth.Validate())", auth_doc)

        self.assertIn("if c.Auth != nil", config_doc)
        self.assertIn("if err := c.Auth.Validate(); err != nil", config_doc)

        new_config = function_block(config_doc, "func NewConfig()")
        self.assertNotIn("Auth: &AuthConfig{}", new_config)

    def test_env_claim_is_accurate(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertNotIn("clears the env var after reading", auth_doc)
        self.assertNotIn("clears it, returns value", auth_doc)
        self.assertIn("does not unset it", auth_doc)

    def test_token_and_pin_lengths(self) -> None:
        auth_doc = read(AUTH_DOC)
        for phrase in (
            "32 bytes of entropy",
            "RawURLEncoding produces 43 URL-safe characters",
            "dashed seven-character",
            "case-insensitively",
        ):
            self.assertIn(phrase, auth_doc)
        self.assertNotIn("(32-char)", auth_doc)

    def test_alias_note(self) -> None:
        auth_doc = normalise(read(AUTH_DOC))
        self.assertIn("Package aliases", auth_doc)
        self.assertIn(
            "`bpauth` is `github.com/oddbit-project/blueprint/provider/httpserver/auth`",
            auth_doc,
        )
        self.assertIn("project's JWT/claims helper package", auth_doc)

    def test_isempty_limitation(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertIn("IsEmpty()", auth_doc)
        self.assertIn("IsEmpty() reports whether", auth_doc)
        self.assertIn("does not resolve", auth_doc)

    def test_partial_key_pair(self) -> None:
        block = function_block(strip_go_comments(read(AUTH_DOC)), "func NewGoAuthService")
        plain = normalise(block)
        self.assertIn('if privPEM != "" || pubPEM != ""', plain)
        self.assertIn("both JWT signing keys are required", plain)

    def test_auth_config_validate_is_fail_closed(self) -> None:
        block = function_block(
            strip_go_comments(read(AUTH_DOC)),
            "func (c *AuthConfig) Validate() error",
        )
        plain = normalise(block)
        self.assertIn("if c.AllowEphemeralKeys { return nil", plain)
        self.assertIn("c.JWTPrivateKey.IsEmpty()", plain)
        self.assertIn("c.JWTPublicKey.IsEmpty()", plain)
        self.assertIn(
            "auth: jwtPrivateKey and jwtPublicKey are required", plain
        )

    def test_validate_uses_or_not_and(self) -> None:
        block = function_block(
            strip_go_comments(read(AUTH_DOC)),
            "func (c *AuthConfig) Validate() error",
        )
        plain = normalise(block)
        self.assertIn("c.JWTPrivateKey.IsEmpty() || c.JWTPublicKey.IsEmpty()", plain)
        self.assertNotIn("c.JWTPrivateKey.IsEmpty() && c.JWTPublicKey.IsEmpty()", plain)

    def test_ephemeral_warning(self) -> None:
        auth_doc = normalise(read(AUTH_DOC))
        self.assertIn(
            "must not be set in a shipped configuration", auth_doc
        )


if __name__ == "__main__":
    unittest.main()
