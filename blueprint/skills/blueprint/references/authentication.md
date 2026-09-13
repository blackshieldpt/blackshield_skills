# Authentication & Credentials

Part of the Blueprint skill. Section numbers match the original guide.

## 8. Authentication & Credentials

### 8.1 GoAuth Integration

Initialize GoAuth with an ECDSA key pair for JWT signing; if no keys are provided, generate ephemeral ES256 keys at startup.

```go
func NewGoAuthService(ctx context.Context, pgClient *db.SqlClient, privPEM, pubPEM string) (*goauth.AuthService, *pgstore.Store, error) {
    store := pgstore.New(ctx, pgClient)
    if privPEM == "" || pubPEM == "" {
        var err error
        if privPEM, pubPEM, err = generateES256Keys(); err != nil {
            return nil, nil, fmt.Errorf("generating JWT keys: %w", err)
        }
    }
    cfg := goauth.NewConfig()
    cfg.Local.Enabled = true
    cfg.JWT.SigningMethod = "ES256"
    cfg.JWT.PrivateKeyPEM = privPEM
    cfg.JWT.PublicKeyPEM = pubPEM
    svc, err := goauth.NewAuthService(ctx, cfg, store)
    if err != nil { return nil, nil, err }
    return svc, store, nil
}
```

Wire into `AppContext` in `Build()`:

```go
authSvc, authStore, err := auth.NewGoAuthService(m.Context, ctx.PgDb, "", "")
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

```go
type ApiConfig struct {
    Server   *httpserver.ServerConfig `json:"server"`
    AuthKeys []string                 `json:"authKeys"`
}
// config: {"api": {"server": {"host": "0.0.0.0", "port": 8080}, "authKeys": ["key1", "key2"]}}

srv, err := httpserver.NewServer(cfg.Server, logger)   // err → return nil, err
srv.UseAuth(auth.NewAuthTokenList("X-API-Key", cfg.AuthKeys))
```

### 8.4 Login / Token Flow

```go
func (s *ApiServer) login(g *gin.Context) {
    var req LoginRequest
    if !httpserver.ValidateJSON(g, &req) { return }
    tokens, user, err := s.svc.User.Authenticate(req.Email, req.Password)
    if err != nil { log.FromContext(g).Error(err, "login failed"); response.Http401(g); return }
    response.Success(g, &LoginResponse{AccessToken: tokens.AccessToken, RefreshToken: tokens.RefreshToken, Name: user.Name})
}

func (s *ApiServer) refreshToken(g *gin.Context) {
    var req RefreshTokenRequest
    if !httpserver.ValidateJSON(g, &req) { return }
    tokens, err := s.svc.User.RefreshToken(req.RefreshToken, nil)
    if err != nil { response.Http401(g); return }
    response.Success(g, tokens)
}
```

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

Use `Argon2Hasher` for stored passwords; the interface supports transparent rehashing when parameters change.

```go
type PasswordHasher interface {
    Generate(password string) (string, error)
    Verify(hashedPassword, password string) (bool, func() (string, error), error)
}

hasher := auth.NewArgon2Hasher()
hash, err := hasher.Generate("password")
match, rehashFn, err := hasher.Verify(hash, "password")
if match && rehashFn != nil { newHash, _ := rehashFn() }   // re-hash with current parameters
```

- `Generate()` returns `$argon2id$v=19$m=65536,t=4,p=12$salt$hash`.
- `Verify()` returns match bool + optional rehash func (non-nil when the stored hash uses outdated parameters).
- Constant-time comparison (`crypto/subtle.ConstantTimeCompare`).
- Defaults: 64 MB memory, 4 iterations, 12 parallelism, 16-byte salt, 32-byte key.

### 8.7 Blueprint Crypt Utilities

`crypt/` subpackages for secure token and PIN generation:

| Need | Import | Call |
|---|---|---|
| Secure tokens (invite codes, password reset links, API keys) — URL-safe base64 | `bptoken "github.com/oddbit-project/blueprint/crypt/token"` | `code, err := bptoken.GenerateSecureBase64Token(32)` (32-char) |
| PINs (invite PINs, 2FA) — short alphanumeric | `github.com/oddbit-project/blueprint/crypt/pin` | `pinCode, err := pin.GenerateAlphanumeric(6)`; `match := pin.CompareAlphanumeric(userInput, storedPin)` (constant-time) |
| Random alphanumeric IDs (source tokens, unpredictable identifiers) via `crypto/rand` | `crypto/rand` | `GenerateRandomAlphanumericID() string`: `rand.Read` 48 bytes → base64 encode, filter to alphanumeric, return 32+ chars |

### 8.8 Constant-Time Comparison

Always use `crypto/subtle.ConstantTimeCompare` when validating tokens or secrets (timing attacks): `subtle.ConstantTimeCompare([]byte(stored), []byte(provided)) == 1` → valid.
Applies to: source token validation, PIN comparison, API key checks, any secret comparison at a system boundary.

### 8.9 Secrets in Configuration

`crypt/secure` (`github.com/oddbit-project/blueprint/crypt/secure`) config structs declare secrets from three sources, in priority order: plaintext value, environment variable, file. `Fetch()` resolves from whichever source is set and clears the env var after reading. Both structs implement `secure.CredentialConfig` (`Fetch() (string, error)` + `IsEmpty() bool`). Never inline secrets in config JSON — always use `*EnvVar` or `*File` fields for production.

| Struct | For | Embeds | JSON | Resolve |
|---|---|---|---|---|
| `secure.DefaultCredentialConfig` | passwords | `Password`, `PasswordEnvVar`, `PasswordFile` | `{"smtp": {"host": "smtp.example.com", "port": 587, "username": "user@example.com", "passwordEnvVar": "SMTP_PASSWORD"}}` | `password, err := cfg.Smtp.Fetch()` (reads env var, clears it, returns value) |
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
}

func NewPgAuditor(ctx context.Context, client *db.SqlClient, logger *log.Logger) *PgAuditor {
    a := &PgAuditor{repo: db.NewRepository(ctx, client, "audit_events"), logger: logger, ch: make(chan AuditEvent, 1024), done: make(chan struct{})}
    go a.loop()
    return a
}
func (a *PgAuditor) Log(_ context.Context, event AuditEvent) {
    select {
    case a.ch <- event:
    default: a.logger.Warn("audit buffer full, dropping event: " + event.Event)
    }
}
func (a *PgAuditor) Close() { close(a.ch); <-a.done }
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

HMAC-SHA256 signatures with optional replay protection (nonces + timestamps). Modes: **simple** (`SHA256Sign`/`SHA256Verify`) and **secure** (`Sign256`/`Verify256`, nonce-based replay prevention). Imports: `.../crypt/secure`, `.../provider/hmacprovider`, `.../provider/hmacprovider/store`, `.../provider/httpserver/auth` (all under `github.com/oddbit-project/blueprint`).

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
hmacAuth := auth.NewHMACAuthProvider(provider)
router.Use(auth.AuthMiddleware(hmacAuth))
keyId, ok := auth.GetHMACIdentity(g)                       // in handler
keyId, timestamp, nonce, ok := auth.GetHMACDetails(g)
```

| Item | Detail |
|---|---|
| Headers | `X-HMAC-Hash`, `X-HMAC-Timestamp`, `X-HMAC-Nonce` |
| Key providers | `NewSingleKeyProvider` (single key); multi-tenant implements `HMACKeyProvider` (`GetKey(keyId) (*secure.Credential, error)`, `ListKeyIds() []string`) |
| Nonce stores | Memory `store.NewMemoryNonceStore` (TTL/eviction), Redis `store.NewRedisStore`, KV `store.NewKvStore`; eviction policies `EvictNone()`, `EvictAll()`, `EvictHalfLife()` |
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

Hash algorithms: bcrypt (default, recommended), Apache MD5 (`$apr1$`), SHA256 (`{SHA256}`), SHA512 (`{SHA512}`), SHA1 (`{SHA}`, deprecated), crypt (deprecated), plain (dev only).

---
