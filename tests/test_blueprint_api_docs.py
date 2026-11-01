"""Pin the Blueprint skill's API facts against the upstream versions it names.

The symbol list below was checked with `go doc` against blueprint core v0.16.0
(providers at their tags of the same commit) and goauth v0.6.1. The tests run
offline: they never fetch upstream. They fail when a doc names a symbol that is
not in the checked list (verify it upstream, then add it), when a known-wrong
symbol comes back, or when a corrected fact is reverted.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "blueprint" / "skills" / "blueprint"
SKILL = SKILL_DIR / "SKILL.md"
REFS = SKILL_DIR / "references"

CORE_VERSION = "v0.16.0"
GOAUTH_VERSION = "v0.6.1"

# Every package-qualified Blueprint/GoAuth symbol the docs use, verified upstream.
VERIFIED_SYMBOLS = frozenset(
    """
    batchwriter.NewBatchWriter batchwriter.WithClearBuffers batchwriter.WithLogger
    batchwriter.WithQueueCapacity blueprint.Container blueprint.NewContainer
    blueprint.RegisterDestructor blueprint.SetShutdownTimeout blueprint.ShutdownContext
    bpauth.AuthMiddleware bpauth.GetHMACDetails bpauth.GetHMACIdentity
    bpauth.NewAuthTokenList bpauth.NewHMACAuthProvider bptoken.GenerateSecureBase64Token
    config.ErrNoKey db.FV db.Grid db.NewGrid db.NewGridQuery db.NewRepository
    db.Repository db.SearchAny db.SearchEnd db.SearchNone db.SearchStart
    db.SortAscending db.SortDescending db.SqlClient goauth.AuthService
    goauth.NewAuthService goauth.NewConfig hashing.NewArgon2Hasher
    hashing.NewArgon2IdConfig hashing.PasswordHasher hmacprovider.NewHmacProvider
    hmacprovider.NewSingleKeyProvider hmacprovider.WithKeyInterval
    hmacprovider.WithMaxInputSize hmacprovider.WithNonceStore htpasswd.HashTypeBcrypt
    htpasswd.HashTypeArgon2 htpasswd.NewContainer htpasswd.NewFromFile
    httpserver.NewServer httpserver.Server httpserver.ServerConfig
    httpserver.ValidateJSON httpserver.ValidationError kv.KV kv.NewMemoryKV log.Config
    log.Configure log.ContextFields log.FromContext log.Info log.KV log.Logger log.New
    log.NewDefaultConfig migrations.DefaultProgressFn migrations.NewEmbedSource
    model.ErrMissingJWTKey
    model.TwoFactorRequiredError nats.AuthTypeBasic nats.AuthTypeNone
    nats.AuthTypeToken nats.ConsumerConfig nats.ConsumerOptions
    nats.JSConnectionConfig nats.JSConsumerConfig nats.JSMessage nats.JSProducerConfig
    nats.Message nats.NewConsumer nats.NewJSConsumer nats.NewJSProducer
    nats.NewProducer nats.ProducerConfig nats.StreamConfig pgsql.ClientConfig
    pgsql.NewClient pgsql.NewClientConfig pgsql.NewMigrationManager pgstore.New
    pgstore.Store pin.CompareAlphanumeric pin.GenerateAlphanumeric qb.Between
    qb.Compare qb.Eq qb.Gt qb.Gte qb.In qb.IsNotNull qb.IsNull qb.Like qb.Lt qb.Lte
    qb.Or qb.Raw qb.UpdateOptions response.ErrorDetail response.Http400
    response.Http401 response.Http403 response.Http404 response.Http429
    response.Http500 response.JSONResponse response.JSONResponseError
    response.Success response.ValidationError s3.BucketOptions
    s3.ErrBucketAlreadyExists s3.ErrBucketNotFound s3.ErrClientNotConnected
    s3.ErrObjectNotFound s3.ListOptions s3.NewClient s3.NewConfig secure.Credential
    secure.CredentialConfig secure.DefaultCredentialConfig secure.GenerateKey
    secure.KeyConfig secure.NewCredential security.CORSMiddleware
    security.CSRFProtection security.CorsConfig security.GenerateCSRFToken
    security.SecurityConfig session.Get session.ManagerWithLogger
    session.ManagerWithStore session.NewConfig session.NewJSONMarshaller
    session.NewManager session.NewStore session.Store smtp.ErrInvalidFrom
    smtp.ErrInvalidTo smtp.ErrMessage smtp.ErrSMTPServer smtp.NewConfig
    smtp.NewMailer smtp.WithAttachment smtp.WithBody smtp.WithFrom store.NewKvStore
    store.NewMemoryNonceStore store.NewRedisNonceStore store.WithCleanupInterval
    store.WithTTL threadpool.FuncRunner threadpool.NewThreadPool threadpool.ThreadPool
    threadpool.WithLogger tls.ClientConfig tls.ServerConfig token.NewSessionInfo
    token.TokenClaims utils.Error utils.PanicOnError
    request.ContentTypeJson request.IsJSONRequest etcd.Config etcd.NewClient
    redis.NewClient clickhouse.Client clickhouse.Repository msgpack.Marshal
    msgpack.Unmarshal msgpack.RawMessage provider.NewJsonProvider
    security.DefaultSecurityConfig model.ErrInvalidCredentials
    provider.Sign256 provider.Verify256 provider.SHA256Sign provider.SHA256Verify
    """.split()
)
# provider.Sign256 & co. are *hmacprovider.HMACProvider methods called on a local
# variable named `provider`; checked as hmacprovider.HMACProvider.<Method>.

# Names that look package-qualified but are project code or local variables.
NOT_UPSTREAM = frozenset(
    {"migrations.PgMigrate", "store.WithMarshaller", "auth.NewGoAuthService"}
)

# Known-wrong API, with the upstream fact. None of these may reappear.
STALE = {
    "PrivateKeyPEM": "goauth JWTConfig uses PrivateKey secure.KeyConfig since v0.1.0",
    "PublicKeyPEM": "goauth JWTConfig uses PublicKey secure.KeyConfig since v0.1.0",
    "BuildSQLBatchUpdate": "qb has no batch update",
    "request.CSRFMiddleware": "CSRF lives in httpserver/security: CSRFProtection",
    "request.GenerateCSRFToken": "it is security.GenerateCSRFToken",
    "GetKey(keyId)": "HMACKeyProvider has one method, FetchSecret",
    "ListKeyIds": "HMACKeyProvider has one method, FetchSecret",
    "NewRedisStore": "the Redis nonce store is store.NewRedisNonceStore",
    "sessionStore.WithMarshaller": "WithMarshaller is a *session.Store method",
    "panorama_": "project-specific metric prefix",
    "original guide": "refers to a document the agent cannot see",
    "errInternalError": "Http500 logs the error it gets; pass the real err",
    "response.Http500(g)": "Http500 takes (ctx, err)",
    "StopCtx": "the bounded stop is ThreadPool.StopWithContext",
    "IsPaginated": "a LIMIT always applies; page defaults to 1",
    "UseSecurityHeaders(cfg.": "an unfilled SecurityConfig drops headers",
    "model.ErrRateLimited": "LoginLocal never returns ErrRateLimited",
    'TLSMinVersion: "1.3"': 'only "TLS12" and "TLS13" are accepted',
    "r.repo.Count(r.repo.SqlSelect())": "Repository.Count() takes no arguments",
    'qb.Raw("login_count + 1")': "FieldsValues binds every value as a parameter",
    "return p.pool.Stop() }": "Stop() ignores the shutdown context",
}

SYMBOL_RE = re.compile(
    r"(?<![\w.])(batchwriter|blueprint|bpauth|bptoken|config|db|goauth|hashing|"
    r"hmacprovider|htpasswd|httpserver|kv|log|migrations|model|nats|pgsql|pgstore|"
    r"pin|qb|response|s3|secure|security|session|smtp|store|threadpool|tls|token|"
    r"utils|request|provider|etcd|redis|clickhouse|msgpack|jwtprovider|auth)\.([A-Z]\w*)"
)

# `config` is also the local variable in the SMTP/S3 snippets (config.Host = ...).
CONFIG_FIELD_RE = re.compile(r"^config\.[A-Z]\w*$")


def read(path: Path) -> str:
    """Return the file contents as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def ref(name: str) -> str:
    """Return a reference file's text."""

    return read(REFS / name)


def normalise(text: str) -> str:
    """Collapse whitespace so line wrapping does not decide a check."""

    return " ".join(text.split())


def all_docs() -> dict[str, str]:
    """Map each skill doc's name to its text."""

    docs = {"SKILL.md": read(SKILL)}
    for path in sorted(REFS.glob("*.md")):
        docs[path.name] = read(path)
    return docs


class SymbolTests(unittest.TestCase):
    """Every upstream symbol the docs name was verified at the pinned versions."""

    def test_docs_use_only_verified_symbols(self) -> None:
        for name, text in all_docs().items():
            for match in SYMBOL_RE.finditer(text):
                symbol = f"{match.group(1)}.{match.group(2)}"
                if symbol in NOT_UPSTREAM:
                    continue
                if CONFIG_FIELD_RE.match(symbol) and symbol != "config.ErrNoKey":
                    if f"{symbol} =" in text or f"{symbol}=" in text:
                        continue
                with self.subTest(doc=name, symbol=symbol):
                    self.assertIn(
                        symbol,
                        VERIFIED_SYMBOLS,
                        "unverified symbol: check it upstream at the pinned versions, "
                        "then add it to VERIFIED_SYMBOLS",
                    )

    def test_request_logger_never_from_gin_context(self) -> None:
        pattern = re.compile(r"log\.FromContext\(\s*g\s*\)\s*\.")
        for name, text in all_docs().items():
            with self.subTest(doc=name):
                self.assertIsNone(
                    pattern.search(text), "the request logger is in g.Request.Context()"
                )

    def test_no_stale_api(self) -> None:
        for name, text in all_docs().items():
            for stale, reason in STALE.items():
                with self.subTest(doc=name, stale=stale):
                    self.assertNotIn(stale, text, reason)


class VersionTests(unittest.TestCase):
    """SKILL.md names the versions it was checked against and the module split."""

    def test_versions_are_pinned(self) -> None:
        text = normalise(read(SKILL))
        self.assertIn(f"core `github.com/oddbit-project/blueprint` {CORE_VERSION}", text)
        self.assertIn(f"`github.com/blackshieldpt/goauth` {GOAUTH_VERSION}", text)
        self.assertIn("httpserver v0.11.0", text)
        self.assertIn("jwtprovider v0.9.0", text)
        self.assertIn("kafka v0.11.0", text)
        self.assertIn("pgsql v0.10.0", text)
        self.assertIn("only requires core v0.8.7, jwtprovider v0.8.2, pgsql v0.8.1", text)
        self.assertIn("require core and each provider the service uses explicitly", text)

    def test_providers_are_separate_modules(self) -> None:
        text = normalise(read(SKILL))
        self.assertIn("is a **separate Go module** with its own tags", text)
        self.assertIn("`provider/tls` and `provider/kv` ship inside core", text)

    def test_description_allows_greenfield_and_excludes_homonyms(self) -> None:
        text = read(SKILL)
        description = re.search(r'^description: "(.*)"$', text, re.M).group(1)
        self.assertLess(len(description), 1024)
        self.assertIn("scaffolding a new Blueprint service", description)
        self.assertIn("Skip for non-Go work", description)
        self.assertNotIn("Skip when the project has no Go files", description)
        for homonym in (
            "Flask Blueprints",
            "Unreal Engine Blueprints",
            "Laravel Schema Blueprint",
            "Blueprint.js",
            "API Blueprint",
        ):
            self.assertIn(homonym, description)

    def test_style_rule_matches_snippets(self) -> None:
        text = normalise(read(SKILL))
        self.assertNotIn("only when they do I/O that outlives the call", text)
        self.assertIn("a request ctx passed into a service method does not cancel repository queries", text)
        http = ref("http-api.md")
        self.assertIn("s.svc.Product.Update(id.String(), req)", http)
        self.assertNotIn("s.svc.Product.Update(g.Request.Context()", http)

    def test_unreferenced_providers_are_named(self) -> None:
        text = normalise(ref("providers.md"))
        self.assertIn("Kafka (`provider/franz`)", text)
        self.assertIn("Redis (`provider/redis`) have no reference", text)


class FactTests(unittest.TestCase):
    """Corrected behaviour facts, one per review finding."""

    def test_grid_search_types(self) -> None:
        text = ref("service-layer.md")
        self.assertIn("`db.SearchStart` (1 — `term%`, starts with)", text)
        self.assertIn("`db.SearchEnd` (2 — `%term`, ends with)", text)

    def test_fieldsvalues_binds_parameters(self) -> None:
        text = normalise(ref("service-layer.md"))
        self.assertIn("every value is bound as a $N parameter", text)
        self.assertIn('goqu.Record{"login_count": goqu.L("login_count + 1")', text)

    def test_buffered_writer_honours_batch_size(self) -> None:
        text = ref("service-layer.md")
        self.assertIn("len(batch) >= w.config.BatchSize { batch = w.flush(batch) }", text)
        self.assertIn("`app_writer_records_dropped_total`", text)

    def test_pgsql_config_uses_dsn(self) -> None:
        text = ref("configuration-and-di.md")
        self.assertIn('"pgsql": { "dsn": "postgres://', text)
        self.assertNotIn('"pgsql": { "host"', text)
        self.assertIn("`ErrEmptyDSN`", text)

    def test_buildlog_only_defaults_on_missing_key(self) -> None:
        text = normalise(ref("configuration-and-di.md"))
        self.assertIn("if errors.Is(err, config.ErrNoKey) { return logger", text)
        self.assertIn("app.AbortFatal(err) // a \"log\" section that does not parse", text)

    def test_pagination_is_bounded(self) -> None:
        text = ref("http-api.md")
        self.assertIn("const defaultPageSize, maxPageSize int64 = 100, 1000", text)
        self.assertIn("params.PageSize = min(v, maxPageSize)", text)
        self.assertIn("params := &pagination.PaginationParams{Page: 1, PageSize: defaultPageSize}", text)
        self.assertIn("v <= math.MaxInt64/maxPageSize { params.Page = v }", text)
        self.assertIn("totalCount, err := r.repo.Count()", text)
        self.assertNotIn("return all rows", text)

    def test_csv_cells_are_neutralised(self) -> None:
        text = ref("http-api.md")
        self.assertIn('strings.TrimLeft(s, " \\t\\r\\n")', text)
        self.assertIn('strings.ContainsRune("=+-@＝＋－＠", r)', text)
        self.assertIn("utf8.DecodeRuneInString", text)
        self.assertIn("csvCell(e.Name), csvCell(e.Email)", text)

    def test_get_record_maps_errors(self) -> None:
        text = ref("http-api.md")
        self.assertIn("if err != nil { productError(g, err) }", text)
        self.assertNotIn("if err != nil { response.Http404(g) }", text)
        self.assertIn("response.Http500(g, err)", text)
        self.assertNotIn('response.Http400(g, "operation failed")', text)
        helper = normalise(text[text.index("func productError"):text.index("### 7.9")])
        self.assertTrue(helper.rstrip("` ").endswith("response.Http500(g, err) // unrecognised (driver, connection) errors: logged, generic 500 }"))

    def test_csrf_and_sessions(self) -> None:
        text = normalise(ref("http-api.md"))
        self.assertIn("router.Use(security.CSRFProtection())", text)
        self.assertIn("Needs the session middleware (§7.13) first", text)
        self.assertIn("`SetTTL`/`Set`/`Get`/`Delete`/`Prune`", text)
        self.assertIn("`WithMarshaller` is a `*session.Store` method", text)

    def test_security_headers_use_defaults(self) -> None:
        text = ref("http-api.md")
        self.assertNotIn("*security.SecurityConfig `json:", text)
        self.assertIn("srv.UseDefaultSecurityHeaders()", text)
        self.assertIn("the struct has no JSON tags", text)
        self.assertNotIn('"security": {}', ref("configuration-and-di.md"))

    def test_request_logger_comes_from_request_context(self) -> None:
        for name in ("logging.md", "http-api.md"):
            with self.subTest(doc=name):
                self.assertIn("log.FromContext(g.Request.Context())", ref(name))

    def test_nats_snippet_logs_through_logger(self) -> None:
        text = ref("providers.md")
        self.assertIn('logger.Info("received", log.KV{"data": string(msg.Data)})', text)
        self.assertNotIn('    log.Info("received", log.KV', text)

    def test_tls_versions_and_cipher_scope(self) -> None:
        text = normalise(ref("providers.md"))
        self.assertIn('TLSMinVersion: "TLS13", TLSMaxVersion: "TLS13"', text)
        self.assertNotIn("TLSCipherSuites:    []string{", text)
        self.assertIn("`TLSCipherSuites` only affects TLS 1.2 handshakes", text)

    def test_threadpool_stop_and_dispatch(self) -> None:
        text = ref("components.md")
        self.assertIn("return p.pool.StopWithContext(ctx)", text)
        self.assertIn("**panics** if the pool is stopped or never started", text)

    def test_msgpack_tags(self) -> None:
        text = ref("rpc.md")
        self.assertIn('`json:"messageType" msgpack:"messageType"`', text)
        self.assertIn("ignores `json:` tags", text)

    def test_short_flag_needs_testing_short(self) -> None:
        text = ref("testing.md")
        self.assertIn("`-short` skips nothing by itself", text)
        self.assertIn("if testing.Short()", text)

    def test_htpasswd_lists_argon2(self) -> None:
        self.assertIn("Argon2 (`HashTypeArgon2`", ref("authentication.md"))

    def test_hmac_names(self) -> None:
        text = ref("authentication.md")
        self.assertIn("`FetchSecret(keyId string) (*secure.Credential, error)`", text)
        self.assertIn("`store.NewRedisNonceStore(client, ttl, opts...)`", text)

    def test_msgpack_tag_switch_is_coordinated(self) -> None:
        self.assertIn("every producer and consumer of a topic must switch in the same release", normalise(ref("rpc.md")))

    def test_buffered_writer_config_and_idle_flush(self) -> None:
        text = ref("service-layer.md")
        self.assertIn("if len(batch) == 0 { return batch }", text)
        self.assertIn("c.QueueSize < 1 || c.BatchSize < 1 || c.FlushInterval <= 0", text)


if __name__ == "__main__":
    unittest.main()
