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

    def test_fail_closed_delegates_to_goauth(self) -> None:
        block = function_block(strip_go_comments(read(AUTH_DOC)), "func NewGoAuthService")
        plain = normalise(block)
        # The keys go to GoAuth as secure.KeyConfig; GoAuth's own Validate fails closed.
        self.assertIn("cfg.JWT.PrivateKey = authCfg.JWTPrivateKey", plain)
        self.assertIn("cfg.JWT.PublicKey = authCfg.JWTPublicKey", plain)
        self.assertIn("return goauth.NewAuthService(ctx, cfg, store)", plain)
        prose = normalise(read(AUTH_DOC))
        self.assertIn("`JWTConfig.Validate` returns `model.ErrMissingJWTKey`", prose)
        self.assertIn("calls `cfg.Validate()`", prose)

    def test_no_removed_goauth_fields(self) -> None:
        auth_doc = read(AUTH_DOC)
        # PrivateKeyPEM/PublicKeyPEM were replaced by secure.KeyConfig in GoAuth v0.1.0.
        for stale in ("PrivateKeyPEM", "PublicKeyPEM", "privPEM, pubPEM string, allowEphemeral bool"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, auth_doc)

    def test_ephemeral_path_is_explicit_and_dev_only(self) -> None:
        block = function_block(strip_go_comments(read(AUTH_DOC)), "func NewGoAuthService")
        plain = normalise(block)
        self.assertIn(
            "if authCfg.AllowEphemeralKeys && authCfg.JWTPrivateKey.IsEmpty() "
            "&& authCfg.JWTPublicKey.IsEmpty() {",
            plain,
        )
        self.assertIn("cfg.JWT.PrivateKey = secure.KeyConfig{Key: privPEM}", plain)
        prose = normalise(read(AUTH_DOC))
        self.assertIn("**dev/test only**", prose)
        self.assertIn("A half-configured pair skips it", prose)

    def test_ephemeral_keys_are_pkcs8(self) -> None:
        block = normalise(
            function_block(strip_go_comments(read(AUTH_DOC)), "func generateES256Keys")
        )
        self.assertIn("x509.MarshalPKCS8PrivateKey(priv)", block)
        self.assertIn('Type: "PRIVATE KEY"', block)
        self.assertIn('Type: "PUBLIC KEY"', block)
        self.assertNotIn("EC PRIVATE KEY", block)
        self.assertIn("rejects a SEC1 `EC PRIVATE KEY` block", normalise(read(AUTH_DOC)))

    def test_goauth_tables_are_migrated(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertIn("authStore := pgstore.New(m.Context, ctx.PgDb)", auth_doc)
        self.assertIn("m.AbortFatal(authStore.Migrate(m.Context))", auth_doc)
        self.assertLess(
            auth_doc.index("authStore.Migrate(m.Context)"),
            auth_doc.index("auth.NewGoAuthService(m.Context, authStore, cfg.Auth)"),
        )
        self.assertIn("**before** the service's `PgMigrate`", normalise(auth_doc))

    def test_ephemeral_branch_warns(self) -> None:
        block = normalise(function_block(strip_go_comments(read(AUTH_DOC)), "func NewGoAuthService"))
        self.assertIn('log.New("auth").Warn("using ephemeral JWT keys', block)

    def test_login_uses_goauth_flows(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertIn("s.appCtx.Auth.LoginLocal(g.Request.Context(), tenantID,", auth_doc)
        self.assertIn("s.appCtx.Auth.RefreshToken(g.Request.Context(), req.RefreshToken, info)", auth_doc)
        self.assertNotIn("s.svc.User.Authenticate", auth_doc)
        self.assertNotIn("s.svc.User.RefreshToken", auth_doc)
        # LoginLocal never returns ErrRateLimited/ErrAccountLocked; lockout is ErrInvalidCredentials.
        self.assertNotIn("model.ErrRateLimited), errors.Is(err, model.ErrAccountLocked)", auth_doc)
        self.assertIn("case errors.Is(err, model.ErrInvalidCredentials):", auth_doc)
        self.assertIn("A locked account returns `model.ErrInvalidCredentials`", normalise(auth_doc))

    def test_hasher_matches_blueprint(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertIn("Verify(password, hash string) (bool, RehashFn, error)", auth_doc)
        self.assertIn('hasher.Verify("password", hash)', auth_doc)
        self.assertIn("hashing.NewArgon2Hasher(nil)", auth_doc)
        self.assertNotIn("Verify(hashedPassword, password string)", auth_doc)
        self.assertNotIn("12 parallelism", auth_doc)

    def test_api_keys_are_secrets_and_never_empty(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertIn("AuthKeys []secure.KeyConfig", auth_doc)
        self.assertNotIn('"authKeys": ["key1", "key2"]', auth_doc)
        self.assertIn('if strings.TrimSpace(key) == "" {', auth_doc)

    def test_auditor_log_after_close_does_not_panic(self) -> None:
        log_fn = normalise(function_block(read(AUTH_DOC), "func (a *PgAuditor) Log("))
        self.assertIn("if a.closed {", log_fn)
        self.assertLess(log_fn.index("if a.closed {"), log_fn.index("case a.ch <- event"))
        close_fn = normalise(function_block(read(AUTH_DOC), "func (a *PgAuditor) Close()"))
        self.assertIn("a.closed = true", close_fn)

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
        self.assertIn("removed in GoAuth v0.5.0", auth_doc)
        # Plan-executor instructions must not leak into the skill.
        self.assertNotIn("exemplar project", auth_doc)
        self.assertNotIn("leave the snippets illustrative", auth_doc)

    def test_hmac_snippet_uses_bpauth_alias(self) -> None:
        auth_doc = read(AUTH_DOC)
        for call in ("AuthMiddleware(hmacAuth)", "GetHMACIdentity(g)", "GetHMACDetails(g)"):
            with self.subTest(call=call):
                self.assertIn(f"bpauth.{call}", auth_doc)
                self.assertNotIn(f" auth.{call}", auth_doc)
                self.assertNotIn(f"(auth.{call}", auth_doc)

    def test_isempty_limitation(self) -> None:
        auth_doc = read(AUTH_DOC)
        self.assertIn("IsEmpty()", auth_doc)
        self.assertIn("IsEmpty() reports whether", auth_doc)
        self.assertIn("does not resolve", auth_doc)
        self.assertNotIn("The helper guard below", auth_doc)

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
