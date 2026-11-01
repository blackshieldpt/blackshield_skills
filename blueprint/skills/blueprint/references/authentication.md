# Authentication & Credentials

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 8. Authentication & Credentials

### 8.1 GoAuth Integration

Initialize GoAuth with an ECDSA (ES256) key pair loaded from `secure.KeyConfig` (§8.9). GoAuth itself fails closed on missing keys: `goauth.NewAuthService` calls `cfg.Validate()`, and `JWTConfig.Validate` returns `model.ErrMissingJWTKey` when `JWT.PrivateKey` or `JWT.PublicKey` has no source (`SigningKey` for HS256/384/512). Do not re-implement that check; pass the `secure.KeyConfig` values straight into `cfg.JWT` and return GoAuth's error. `AuthConfig.Validate()` below only reports the same problem earlier, at config load, with a message naming the JSON keys. Imports: `github.com/blackshieldpt/goauth`, `github.com/blackshieldpt/goauth/store/pgstore`, `github.com/oddbit-project/blueprint/crypt/secure`.

The one project-level decision is the ephemeral-key path. It runs only when `allowEphemeralKeys` is explicitly set **and** both key sources are empty; it is **dev/test only**: every restart invalidates all tokens and replicas cannot verify each other's. A half-configured pair skips it and fails in GoAuth's validation.

```go
func NewGoAuthService(ctx context.Context, store *pgstore.Store, authCfg *AuthConfig) (*goauth.AuthService, error) {
    cfg := goauth.NewConfig()
    cfg.Local.Enabled = true
    cfg.JWT.SigningMethod = "ES256"
    cfg.JWT.PrivateKey = authCfg.JWTPrivateKey
    cfg.JWT.PublicKey = authCfg.JWTPublicKey
    if authCfg.AllowEphemeralKeys && authCfg.JWTPrivateKey.IsEmpty() && authCfg.JWTPublicKey.IsEmpty() {
        privPEM, pubPEM, err := generateES256Keys() // dev/test only
        if err != nil {
            return nil, fmt.Errorf("generating JWT keys: %w", err)
        }
        log.New("auth").Warn("using ephemeral JWT keys: tokens die on restart and replicas cannot verify each other; dev/test only")
        cfg.JWT.PrivateKey = secure.KeyConfig{Key: privPEM}
        cfg.JWT.PublicKey = secure.KeyConfig{Key: pubPEM}
    }
    // Validates first: a missing key source returns model.ErrMissingJWTKey, and a
    // source that resolves to an empty or unparsable PEM fails while the signer is built.
    return goauth.NewAuthService(ctx, cfg, store)
}

// generateES256Keys returns a PKCS#8 "PRIVATE KEY" and a PKIX "PUBLIC KEY" PEM.
func generateES256Keys() (privPEM, pubPEM string, err error) {
    priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
    if err != nil {
        return "", "", err
    }
    privDER, err := x509.MarshalPKCS8PrivateKey(priv)
    if err != nil {
        return "", "", err
    }
    pubDER, err := x509.MarshalPKIXPublicKey(&priv.PublicKey)
    if err != nil {
        return "", "", err
    }
    return string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privDER})),
        string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubDER})), nil
}
```

Key files follow the same format: Blueprint's `jwtprovider` accepts only a PKCS#8 `PRIVATE KEY` block for ES256 and rejects a SEC1 `EC PRIVATE KEY` block (`ErrInvalidPrivateKey`). Generate with `openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256`, or convert an existing key with `openssl pkcs8 -topk8 -nocrypt`.

**Package aliases.** `bpauth` is `github.com/oddbit-project/blueprint/provider/httpserver/auth`. GoAuth ships no HTTP layer: its `httpauth` package was removed in GoAuth v0.5.0, with no replacement. In these snippets `httpauth` is the project's JWT/claims helper package, which the service owns. It exports `JWTMiddleware` (reads the bearer token, calls `TokenService().ParseAccessToken`, stores the `*token.TokenClaims` in the Gin context), `GetClaimsFromContext`, and `GetUserIDFromContext`. GoAuth's `examples/` directory has a reference `jwtMiddleware` to copy.

Config carries the keys as `secure.KeyConfig` (§8.9):

```go
type AuthConfig struct {
    JWTPrivateKey      secure.KeyConfig `json:"jwtPrivateKey"`   // {"jwtPrivateKey": {"keyFile": "/run/secrets/jwt.key"}}
    JWTPublicKey       secure.KeyConfig `json:"jwtPublicKey"`
    AllowEphemeralKeys bool             `json:"allowEphemeralKeys"` // dev/test only
}

func (c *AuthConfig) Validate() error {
    if c.AllowEphemeralKeys {
        return nil
    }
    if c.JWTPrivateKey.IsEmpty() || c.JWTPublicKey.IsEmpty() {
        return fmt.Errorf("auth: jwtPrivateKey and jwtPublicKey are required; set allowEphemeralKeys only for local development")
    }
    return nil
}
// in Config: Auth *AuthConfig `json:"auth"`
```

`AllowEphemeralKeys` is for local development and tests only. A production deployment must load the keys from `KeyEnvVar` or `KeyFile`; the flag must not be set in a shipped configuration.

Note the limit of this check: `secure.KeyConfig`'s IsEmpty() reports whether a **source field** (`Key`, `KeyEnvVar`, `KeyFile`) is blank; it does not resolve the value. A configured `KeyEnvVar` whose variable is unset still passes `Validate()` (both this one and GoAuth's) and resolves to an empty PEM. GoAuth then fails while building the signer, because `jwtprovider` cannot parse an empty PEM, so startup still stops.

Wire into `AppContext` in `Build()`. GoAuth's tables (`auth_users`, `auth_api_keys`, and the rest) come from its own migrations, so run `Store.Migrate` under the same `-migrate` flag and **before** the service's `PgMigrate`: service tables that reference `auth_users` need it to exist. Place this block ahead of the `PgMigrate` line in `Build()` (SKILL.md §2):

```go
if cfg.Auth == nil {
    m.AbortFatal(fmt.Errorf("auth config is required"))
}
m.AbortFatal(cfg.Auth.Validate())
authStore := pgstore.New(m.Context, ctx.PgDb)
if m.args.Migrate != nil && *m.args.Migrate {
    m.AbortFatal(authStore.Migrate(m.Context))
}
authSvc, err := auth.NewGoAuthService(m.Context, authStore, cfg.Auth)
m.AbortFatal(err)
ctx.Auth = authSvc; ctx.AuthStore = authStore
```

### 8.2 JWT Middleware

Apply to protected route groups. Public endpoints must be registered **before** the middleware:

```go
v1 := srv.Group("/v1")
v1.POST("/login", api.login); v1.POST("/refresh", api.refreshToken)   // public (no auth)
v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))            // auth barrier — all routes below require a valid JWT
v1.GET("/product", api.listProducts)                                  // protected
```

### 8.3 API Key Authentication

Internal / machine-to-machine services without user sessions: Blueprint token list auth instead of GoAuth. Static key list, `X-API-Key` header. All routes registered after `UseAuth()` require a valid key; no login/refresh flow.

Keys are secrets: declare each as a `secure.KeyConfig` (§8.9), never inline in the JSON. Reject an empty resolved key at startup: `NewAuthTokenList` compares the header with each key, and a missing header reads as `""`, so an empty key admits every request that sends no `X-API-Key`.

```go
type ApiConfig struct {
    Server   *httpserver.ServerConfig `json:"server"`
    AuthKeys []secure.KeyConfig       `json:"authKeys"`
}
// config: {"api": {"server": {"host": "0.0.0.0", "port": 8080},
//          "authKeys": [{"keyEnvVar": "API_KEY_BILLING"}, {"keyFile": "/run/secrets/api_key_reports"}]}}

keys := make([]string, 0, len(cfg.AuthKeys))
for i, kc := range cfg.AuthKeys {
    key, err := kc.Fetch()
    if err != nil { return nil, err }
    if strings.TrimSpace(key) == "" { return nil, fmt.Errorf("api: authKeys[%d] resolves to an empty key", i) }
    keys = append(keys, key)
}
if len(keys) == 0 { return nil, fmt.Errorf("api: at least one API key is required") }
srv, err := httpserver.NewServer(cfg.Server, logger)   // err → return nil, err
srv.UseAuth(bpauth.NewAuthTokenList("X-API-Key", keys))
```

### 8.4 Login / Token Flow

Call GoAuth's `LoginLocal` and `RefreshToken` directly. They carry the failed-login throttling and lockout, user-enumeration protection (`ProtectEnumeration`), 2FA challenge and refresh-token fingerprint checks; a project `Authenticate` that looks the user up and verifies the hash itself skips all of them. A locked account returns `model.ErrInvalidCredentials`, the same as a wrong password, so as not to reveal that it exists. Request-rate limiting (429) is the HTTP layer's job (`srv.UseRateLimiting`, or a per-route limiter as in §7.3); `LoginLocal` does not return `ErrRateLimited`. Imports: `github.com/blackshieldpt/goauth/model`, `github.com/blackshieldpt/goauth/token`.

```go
func (s *ApiServer) login(g *gin.Context) {
    var req LoginRequest
    if !httpserver.ValidateJSON(g, &req) { return }
    info := token.NewSessionInfo(g.ClientIP(), g.Request.UserAgent())
    tokens, err := s.appCtx.Auth.LoginLocal(g.Request.Context(), tenantID, req.Email, req.Password, info)
    var twoFA *model.TwoFactorRequiredError
    switch {
    case err == nil:
        response.Success(g, tokens)
    case errors.As(err, &twoFA):
        response.Success(g, gin.H{"twoFactorRequired": true, "challengeToken": twoFA.ChallengeToken})
    case errors.Is(err, model.ErrInvalidCredentials):
        response.Http401(g)   // GoAuth already logs the failure at Warn
    default:
        log.FromContext(g.Request.Context()).Error(err, "login failed")
        response.Http401(g)
    }
}

func (s *ApiServer) refreshToken(g *gin.Context) {
    var req RefreshTokenRequest
    if !httpserver.ValidateJSON(g, &req) { return }
    info := token.NewSessionInfo(g.ClientIP(), g.Request.UserAgent())   // same fingerprint inputs as login
    tokens, err := s.appCtx.Auth.RefreshToken(g.Request.Context(), req.RefreshToken, info)
    if err != nil { response.Http401(g); return }
    response.Success(g, tokens)
}
```

`tenantID` is the service's tenant (`uuid.UUID`; a fixed value in a single-tenant service).

### 8.5 Extracting Current User

Read the user from the JWT context; cache in `gin.Context` to avoid repeated DB lookups per request:

```go
func (s *ApiServer) CurrentUser(g *gin.Context) *entity.UserInfo {
    if v, exists := g.Get("currentUser"); exists { return v.(*entity.UserInfo) }
    userID, err := httpauth.GetUserIDFromContext(g)   // err → return nil
    info, err := s.svc.User.GetUserInfo(userID)       // err → return nil
    g.Set("currentUser", info)
    return info
}
```

### 8.6 Password Hashing (Argon2id)

GoAuth hashes its own users' passwords; this is for passwords the service stores outside GoAuth. Use Blueprint's `hashing.PasswordHasher` (`github.com/oddbit-project/blueprint/crypt/hashing`); it supports transparent rehashing when parameters change. **Argument order: `Verify(password, hash)`**, plaintext first.

```go
type PasswordHasher interface {
    Generate(password string) (string, error)
    Verify(password, hash string) (bool, RehashFn, error)   // RehashFn = func() (string, error)
}

hasher, err := hashing.NewArgon2Hasher(nil)   // nil → hashing.NewArgon2IdConfig()
hash, err := hasher.Generate("password")
match, rehashFn, err := hasher.Verify("password", hash)
if match && rehashFn != nil { newHash, _ := rehashFn() }   // re-hash with current parameters
```

- `Generate()` returns `$argon2id$v=19$m=65536,t=4,p=<cpus>$salt$hash`.
- `Verify()` returns match bool + optional rehash func (non-nil when the stored hash uses outdated parameters).
- Constant-time comparison (`crypto/subtle.ConstantTimeCompare`).
- Defaults (`NewArgon2IdConfig`): 64 MB memory, 4 iterations, parallelism `runtime.NumCPU()`, 16-byte salt, 32-byte key.

### 8.7 Blueprint Crypt Utilities

`crypt/` subpackages for secure token and PIN generation:

| Need | Import | Call |
|---|---|---|
| Secure tokens (invite codes, password reset links, API keys) — URL-safe base64 | `bptoken "github.com/oddbit-project/blueprint/crypt/token"` | `code, err := bptoken.GenerateSecureBase64Token(32)` (32 bytes of entropy; RawURLEncoding produces 43 URL-safe characters) |
| PINs (invite PINs, 2FA) — short alphanumeric | `github.com/oddbit-project/blueprint/crypt/pin` | `pinCode, err := pin.GenerateAlphanumeric(6)`; `match := pin.CompareAlphanumeric(userInput, storedPin)` (constant-time) |
| Random alphanumeric IDs (source tokens, unpredictable identifiers) via `crypto/rand` | `crypto/rand` | `GenerateRandomAlphanumericID() string`: `rand.Read` 48 bytes → base64 encode, filter to alphanumeric, return 32+ chars |

`GenerateAlphanumeric(6)` returns a dashed seven-character value (`ABC-123`); `CompareAlphanumeric` strips dashes and compares case-insensitively.

### 8.8 Constant-Time Comparison

Always use `crypto/subtle.ConstantTimeCompare` when validating tokens or secrets (timing attacks): `subtle.ConstantTimeCompare([]byte(stored), []byte(provided)) == 1` → valid.
Applies to: source token validation, PIN comparison, API key checks, any secret comparison at a system boundary.

### 8.9 Secrets in Configuration

`crypt/secure` (`github.com/oddbit-project/blueprint/crypt/secure`) config structs declare secrets from three sources, in priority order: plaintext value, environment variable, file. `Fetch()` resolves from whichever source is set. When `KeyEnvVar` / `PasswordEnvVar` is used it calls `os.Getenv`; it does not unset it. The process environment is outside this struct's control — clear or scope it in the supervisor if that matters. Both structs implement `secure.CredentialConfig` (`Fetch() (string, error)` + `IsEmpty() bool`). Never inline secrets in config JSON — always use `*EnvVar` or `*File` fields for production.

| Struct | For | Embeds | JSON | Resolve |
|---|---|---|---|---|
| `secure.DefaultCredentialConfig` | passwords | `Password`, `PasswordEnvVar`, `PasswordFile` | `{"smtp": {"host": "smtp.example.com", "port": 587, "username": "user@example.com", "passwordEnvVar": "SMTP_PASSWORD"}}` | `password, err := cfg.Smtp.Fetch()` (reads env var with os.Getenv; does not unset it) |
| `secure.KeyConfig` | encryption keys, API keys, other non-password secrets | `Key`, `KeyEnvVar`, `KeyFile` | `{"encryption": {"algorithm": "aes256", "keyEnvVar": "ENCRYPTION_KEY"}}` | `key, err := cfg.Encryption.Fetch()` (same resolution); `cfg.Encryption.IsEmpty()` → no key source configured |

```go
type SmtpConfig struct {
    Host     string `json:"host"`
    Port     int    `json:"port"`
    Username string `json:"username"`
    secure.DefaultCredentialConfig   // embeds Password, PasswordEnvVar, PasswordFile
}
type EncryptionConfig struct {
    Algorithm string `json:"algorithm"`
    secure.KeyConfig                 // embeds Key, KeyEnvVar, KeyFile
}
```

### 8.10 Audit Trail

Security-sensitive operations emit structured audit events via an `Auditor` interface. Audit failures must never break application flows.

```go
type AuditEvent struct {
    Time      time.Time
    Event     string            // e.g. "login.local", "user.create", "entity.export"
    ActorID   uuid.UUID         // who performed the action (Nil if unknown)
    UserID    uuid.UUID         // user affected (Nil for pre-auth failures)
    TenantID  uuid.UUID
    IP        string
    UserAgent string
    Success   bool
    Error     string            // failure reason (empty on success)
    Metadata  map[string]string // extra context (provider, target ID, etc.)
}

type Auditor interface { Log(ctx context.Context, event AuditEvent) }
```

**PostgreSQL-backed auditor** — writes asynchronously via a buffered channel so callers never block:

```go
type PgAuditor struct {
    repo   db.Repository
    logger *log.Logger
    ch     chan AuditEvent
    done   chan struct{}
    mu     sync.RWMutex
    closed bool
}

func NewPgAuditor(ctx context.Context, client *db.SqlClient, logger *log.Logger) *PgAuditor {
    a := &PgAuditor{repo: db.NewRepository(ctx, client, "audit_events"), logger: logger, ch: make(chan AuditEvent, 1024), done: make(chan struct{})}
    go a.loop()
    return a
}
func (a *PgAuditor) Log(_ context.Context, event AuditEvent) {
    a.mu.RLock()
    defer a.mu.RUnlock()
    if a.closed {   // a send on the closed channel would panic the caller
        a.logger.Warn("auditor closed, dropping event: " + event.Event)
        return
    }
    select {
    case a.ch <- event:
    default: a.logger.Warn("audit buffer full, dropping event: " + event.Event)
    }
}
func (a *PgAuditor) Close() {
    a.mu.Lock()
    if a.closed { a.mu.Unlock(); return }
    a.closed = true
    close(a.ch)
    a.mu.Unlock()
    <-a.done
}
func (a *PgAuditor) loop()  { defer close(a.done); for evt := range a.ch { a.persist(evt) } }
```

**Gin handler helper** — actor from JWT claims:

```go
func emitAudit(g *gin.Context, auditor Auditor, event string, success bool, errMsg string, meta map[string]string) {
    var actorID, tenantID uuid.UUID
    if claims, err := httpauth.GetClaimsFromContext(g); err == nil { actorID = claims.UserID; tenantID = claims.TenantID }
    auditor.Log(g.Request.Context(), AuditEvent{
        Time: time.Now().UTC(), Event: event, ActorID: actorID, UserID: actorID, TenantID: tenantID,
        IP: g.ClientIP(), UserAgent: g.Request.UserAgent(), Success: success, Error: errMsg, Metadata: meta,
    })
}
```

Event type constants per domain: `AuditLoginLocal = "login.local"`, `AuditLoginOIDC = "login.oidc"`, `AuditUserCreate = "user.create"`, `AuditUserUpdate = "user.update"`, `AuditEntityExport = "entity.export"`.

### 8.11 HMAC Authentication

HMAC-SHA256 signatures with optional replay protection (nonces + timestamps). Modes: **simple** (`SHA256Sign`/`SHA256Verify`) and **secure** (`Sign256`/`Verify256`, nonce-based replay prevention). Imports: `.../crypt/secure`, `.../provider/hmacprovider`, `.../provider/hmacprovider/store`, `.../provider/httpserver/auth` as `bpauth` (all under `github.com/oddbit-project/blueprint`).

```go
key, _ := secure.GenerateKey()
secret, _ := secure.NewCredential([]byte("my-secret"), key, false)
keyProvider := hmacprovider.NewSingleKeyProvider("client-key", secret)
memoryStore := store.NewMemoryNonceStore(store.WithTTL(1*time.Hour), store.WithCleanupInterval(15*time.Minute))
provider := hmacprovider.NewHmacProvider(keyProvider,
    hmacprovider.WithNonceStore(memoryStore),    // replay protection backend
    hmacprovider.WithKeyInterval(5*time.Minute), // timestamp tolerance
    hmacprovider.WithMaxInputSize(1024*1024),    // DoS protection
)
// secure mode
hash, timestamp, nonce, _ := provider.Sign256("client-key", strings.NewReader(data))
keyId, valid, _ := provider.Verify256(strings.NewReader(data), hash, timestamp, nonce)
// simple mode (no replay protection)
hash, _ := provider.SHA256Sign("client-key", strings.NewReader(data))
keyId, valid, _ := provider.SHA256Verify(strings.NewReader(data), hash)

// HTTP middleware
hmacAuth := bpauth.NewHMACAuthProvider(provider)
router.Use(bpauth.AuthMiddleware(hmacAuth))
keyId, ok := bpauth.GetHMACIdentity(g)                     // in handler
keyId, timestamp, nonce, ok := bpauth.GetHMACDetails(g)
```

| Item | Detail |
|---|---|
| Headers | `X-HMAC-Hash`, `X-HMAC-Timestamp`, `X-HMAC-Nonce` |
| Key providers | `NewSingleKeyProvider` (single key); multi-tenant implements `HMACKeyProvider` (one method: `FetchSecret(keyId string) (*secure.Credential, error)`) |
| Nonce stores | Memory `store.NewMemoryNonceStore` (TTL/eviction), Redis `store.NewRedisNonceStore(client, ttl, opts...)`, KV `store.NewKvStore(kv, ttl)`; eviction policies `EvictNone()`, `EvictAll()`, `EvictHalfLife()` |
| keyId | Cannot contain dots (`.`) — used internally for concatenation |

### 8.12 HTPasswd

Apache-style password files, multiple hash algorithms, thread-safe, file I/O. Import `github.com/oddbit-project/blueprint/provider/htpasswd`.

```go
container := htpasswd.NewContainer()
container.AddUserPassword("alice", "secret123")                   // bcrypt by default
container.AddUserWithHash("bob", "pass", htpasswd.HashTypeBcrypt) // specific algorithm
valid, err := container.VerifyUser("alice", "secret123")
container.UserExists("alice"); container.ListUsers(); container.Count(); container.DeleteUser("alice")
container, err := htpasswd.NewFromFile("/path/to/.htpasswd")
container.Write(file) // io.Writer
```

Hash algorithms: bcrypt (default, recommended), Argon2 (`HashTypeArgon2`; writes `$argon2id$`, verifies `$argon2i$` and `$argon2id$`), Apache MD5 (`$apr1$`), SHA256 (`{SHA256}`), SHA512 (`{SHA512}`), SHA1 (`{SHA}`, deprecated), crypt (deprecated), plain (dev only).

---
