# Authentication & Credentials

Part of the Blueprint skill. Section numbers match the original guide.

## 8. Authentication & Credentials

### 8.1 GoAuth Integration

Initialize GoAuth with an ECDSA key pair for JWT signing. If no keys are provided, generate ephemeral ES256 keys at startup:

```go
func NewGoAuthService(ctx context.Context, pgClient *db.SqlClient, privPEM, pubPEM string) (*goauth.AuthService, *pgstore.Store, error) {
    store := pgstore.New(ctx, pgClient)

    if privPEM == "" || pubPEM == "" {
        var err error
        privPEM, pubPEM, err = generateES256Keys()
        if err != nil {
            return nil, nil, fmt.Errorf("generating JWT keys: %w", err)
        }
    }

    cfg := goauth.NewConfig()
    cfg.Local.Enabled = true
    cfg.JWT.SigningMethod = "ES256"
    cfg.JWT.PrivateKeyPEM = privPEM
    cfg.JWT.PublicKeyPEM = pubPEM

    svc, err := goauth.NewAuthService(ctx, cfg, store)
    if err != nil {
        return nil, nil, err
    }
    return svc, store, nil
}
```

Wire into `AppContext` during `Build()`:

```go
authSvc, authStore, err := auth.NewGoAuthService(m.Context, ctx.PgDb, "", "")
m.AbortFatal(err)
ctx.Auth = authSvc
ctx.AuthStore = authStore
```

### 8.2 JWT Middleware

Apply GoAuth's JWT middleware to protected route groups. Public endpoints must be registered **before** the middleware:

```go
v1 := srv.Group("/v1")

// Public (no auth)
v1.POST("/login", api.login)
v1.POST("/refresh", api.refreshToken)

// Auth barrier — all routes below require a valid JWT
v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))

// Protected
v1.GET("/product", api.listProducts)
```

### 8.3 API Key Authentication

For internal or machine-to-machine services that don't need user sessions, use Blueprint's token list auth instead of GoAuth. Authenticates via a static list of API keys in the `X-API-Key` header:

```go
type ApiConfig struct {
    Server   *httpserver.ServerConfig `json:"server"`
    AuthKeys []string                 `json:"authKeys"`
}
```

```json
{
  "api": {
    "server": { "host": "0.0.0.0", "port": 8080 },
    "authKeys": ["key1", "key2"]
  }
}
```

Wire into the server with `srv.UseAuth()`:

```go
srv, err := httpserver.NewServer(cfg.Server, logger)
if err != nil {
    return nil, err
}
srv.UseAuth(auth.NewAuthTokenList("X-API-Key", cfg.AuthKeys))
```

All routes registered after `UseAuth()` require a valid key. No login/refresh flow needed.

### 8.4 Login / Token Flow

```go
// Login handler
func (s *ApiServer) login(g *gin.Context) {
    var req LoginRequest
    if !httpserver.ValidateJSON(g, &req) {
        return
    }

    tokens, user, err := s.svc.User.Authenticate(req.Email, req.Password)
    if err != nil {
        log.FromContext(g).Error(err, "login failed")
        response.Http401(g)
        return
    }

    response.Success(g, &LoginResponse{
        AccessToken:  tokens.AccessToken,
        RefreshToken: tokens.RefreshToken,
        Name:         user.Name,
    })
}

// Refresh handler
func (s *ApiServer) refreshToken(g *gin.Context) {
    var req RefreshTokenRequest
    if !httpserver.ValidateJSON(g, &req) {
        return
    }
    tokens, err := s.svc.User.RefreshToken(req.RefreshToken, nil)
    if err != nil {
        response.Http401(g)
        return
    }
    response.Success(g, tokens)
}
```

### 8.5 Extracting Current User

Retrieve the authenticated user from the JWT context. Cache in `gin.Context` to avoid repeated DB lookups per request:

```go
func (s *ApiServer) CurrentUser(g *gin.Context) *entity.UserInfo {
    if v, exists := g.Get("currentUser"); exists {
        return v.(*entity.UserInfo)
    }
    userID, err := httpauth.GetUserIDFromContext(g)
    if err != nil {
        return nil
    }
    info, err := s.svc.User.GetUserInfo(userID)
    if err != nil {
        return nil
    }
    g.Set("currentUser", info)
    return info
}
```

### 8.6 Password Hashing (Argon2id)

Use `Argon2Hasher` for storing passwords. The hasher interface supports transparent rehashing when parameters change:

```go
type PasswordHasher interface {
    Generate(password string) (string, error)
    Verify(hashedPassword, password string) (bool, func() (string, error), error)
}
```

- `Generate()` returns `$argon2id$v=19$m=65536,t=4,p=12$salt$hash`
- `Verify()` returns a match bool + an optional rehash function (non-nil when stored hash uses outdated parameters)
- Uses constant-time comparison (`crypto/subtle.ConstantTimeCompare`)
- Defaults: 64 MB memory, 4 iterations, 12 parallelism, 16-byte salt, 32-byte key

```go
hasher := auth.NewArgon2Hasher()
hash, err := hasher.Generate("password")
match, rehashFn, err := hasher.Verify(hash, "password")
if match && rehashFn != nil {
    newHash, _ := rehashFn()  // re-hash with current parameters
}
```

### 8.7 Blueprint Crypt Utilities

Blueprint provides `crypt/` subpackages for secure token and PIN generation.

**Secure tokens** (`crypt/token`): URL-safe base64 tokens for invite codes, password reset links, API keys:

```go
import bptoken "github.com/oddbit-project/blueprint/crypt/token"

code, err := bptoken.GenerateSecureBase64Token(32) // 32-char secure token
```

**PINs** (`crypt/pin`): short alphanumeric codes for user verification (invite PINs, 2FA):

```go
import "github.com/oddbit-project/blueprint/crypt/pin"

pinCode, err := pin.GenerateAlphanumeric(6)              // generate 6-char PIN
match := pin.CompareAlphanumeric(userInput, storedPin)    // constant-time compare
```

**Random alphanumeric IDs** (via `crypto/rand`): for source tokens and identifiers that must be unpredictable:

```go
func GenerateRandomAlphanumericID() string {
    bytes := make([]byte, 48)
    _, err := rand.Read(bytes)
    // ... base64 encode, filter to alphanumeric, return 32+ chars
}
```

### 8.8 Constant-Time Comparison

Always use `crypto/subtle.ConstantTimeCompare` when validating tokens or secrets to prevent timing attacks:

```go
import "crypto/subtle"

if subtle.ConstantTimeCompare([]byte(stored), []byte(provided)) == 1 {
    // valid
}
```

This applies to: source token validation, PIN comparison, API key checks, any secret comparison at a system boundary.

### 8.9 Secrets in Configuration

Blueprint provides `crypt/secure` config structs for declaring secrets. They support three sources (in priority order): plaintext value, environment variable, or file. Use `Fetch()` to resolve the secret — it reads from whichever source is set and clears the env var after reading.

**`secure.DefaultCredentialConfig`** — for passwords:

```go
import "github.com/oddbit-project/blueprint/crypt/secure"

type SmtpConfig struct {
    Host     string                       `json:"host"`
    Port     int                          `json:"port"`
    Username string                       `json:"username"`
    secure.DefaultCredentialConfig                          // embeds Password, PasswordEnvVar, PasswordFile
}
```

```json
{
  "smtp": {
    "host": "smtp.example.com",
    "port": 587,
    "username": "user@example.com",
    "passwordEnvVar": "SMTP_PASSWORD"
  }
}
```

```go
password, err := cfg.Smtp.Fetch()  // reads env var, clears it, returns value
```

**`secure.KeyConfig`** — for encryption keys, API keys, and similar non-password secrets:

```go
type EncryptionConfig struct {
    Algorithm string           `json:"algorithm"`
    secure.KeyConfig                                // embeds Key, KeyEnvVar, KeyFile
}
```

```json
{
  "encryption": {
    "algorithm": "aes256",
    "keyEnvVar": "ENCRYPTION_KEY"
  }
}
```

```go
key, err := cfg.Encryption.Fetch()  // same resolution logic as DefaultCredentialConfig
if cfg.Encryption.IsEmpty() {
    // no key source configured
}
```

Both structs implement the `secure.CredentialConfig` interface (`Fetch() (string, error)` + `IsEmpty() bool`). Never inline secrets in config JSON — always use `*EnvVar` or `*File` fields for production.

### 8.10 Audit Trail

For security-sensitive operations, emit structured audit events via an `Auditor` interface. Audit failures must never break application flows.

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

type Auditor interface {
    Log(ctx context.Context, event AuditEvent)
}
```

**PostgreSQL-backed auditor** — writes asynchronously via a buffered channel to avoid blocking callers:

```go
type PgAuditor struct {
    repo   db.Repository
    logger *log.Logger
    ch     chan AuditEvent
    done   chan struct{}
}

func NewPgAuditor(ctx context.Context, client *db.SqlClient, logger *log.Logger) *PgAuditor {
    a := &PgAuditor{
        repo:   db.NewRepository(ctx, client, "audit_events"),
        logger: logger,
        ch:     make(chan AuditEvent, 1024),
        done:   make(chan struct{}),
    }
    go a.loop()
    return a
}

func (a *PgAuditor) Log(_ context.Context, event AuditEvent) {
    select {
    case a.ch <- event:
    default:
        a.logger.Warn("audit buffer full, dropping event: " + event.Event)
    }
}

func (a *PgAuditor) Close() {
    close(a.ch)
    <-a.done
}

func (a *PgAuditor) loop() {
    defer close(a.done)
    for evt := range a.ch {
        a.persist(evt)
    }
}
```

**Gin handler helper** — extracts actor from JWT claims and emits:

```go
func emitAudit(g *gin.Context, auditor Auditor, event string, success bool, errMsg string, meta map[string]string) {
    var actorID, tenantID uuid.UUID
    if claims, err := httpauth.GetClaimsFromContext(g); err == nil {
        actorID = claims.UserID
        tenantID = claims.TenantID
    }

    auditor.Log(g.Request.Context(), AuditEvent{
        Time:      time.Now().UTC(),
        Event:     event,
        ActorID:   actorID,
        UserID:    actorID,
        TenantID:  tenantID,
        IP:        g.ClientIP(),
        UserAgent: g.Request.UserAgent(),
        Success:   success,
        Error:     errMsg,
        Metadata:  meta,
    })
}
```

Define event type constants per domain:

```go
const (
    AuditLoginLocal  = "login.local"
    AuditLoginOIDC   = "login.oidc"
    AuditUserCreate  = "user.create"
    AuditUserUpdate  = "user.update"
    AuditEntityExport = "entity.export"
)
```

### 8.11 HMAC Authentication

Blueprint's HMAC provider implements HMAC-SHA256 signatures with optional replay protection (nonces + timestamps). Two modes: **simple** (`SHA256Sign`/`SHA256Verify`) and **secure** (`Sign256`/`Verify256` with nonce-based replay prevention).

```go
import (
    "github.com/oddbit-project/blueprint/crypt/secure"
    "github.com/oddbit-project/blueprint/provider/hmacprovider"
    "github.com/oddbit-project/blueprint/provider/hmacprovider/store"
    "github.com/oddbit-project/blueprint/provider/httpserver/auth"
)

// Setup
key, _ := secure.GenerateKey()
secret, _ := secure.NewCredential([]byte("my-secret"), key, false)
keyProvider := hmacprovider.NewSingleKeyProvider("client-key", secret)
memoryStore := store.NewMemoryNonceStore(
    store.WithTTL(1*time.Hour),
    store.WithCleanupInterval(15*time.Minute),
)
provider := hmacprovider.NewHmacProvider(keyProvider,
    hmacprovider.WithNonceStore(memoryStore),    // replay protection backend
    hmacprovider.WithKeyInterval(5*time.Minute), // timestamp tolerance
    hmacprovider.WithMaxInputSize(1024*1024),    // DoS protection
)

// Sign and verify (secure mode with replay protection)
hash, timestamp, nonce, _ := provider.Sign256("client-key", strings.NewReader(data))
keyId, valid, _ := provider.Verify256(strings.NewReader(data), hash, timestamp, nonce)

// Simple mode (no replay protection)
hash, _ := provider.SHA256Sign("client-key", strings.NewReader(data))
keyId, valid, _ := provider.SHA256Verify(strings.NewReader(data), hash)
```

**HTTP middleware integration:**

```go
hmacAuth := auth.NewHMACAuthProvider(provider)
router.Use(auth.AuthMiddleware(hmacAuth))

// In handler: extract identity
keyId, ok := auth.GetHMACIdentity(g)
keyId, timestamp, nonce, ok := auth.GetHMACDetails(g)
```

HTTP headers: `X-HMAC-Hash`, `X-HMAC-Timestamp`, `X-HMAC-Nonce`.

**Key providers:** `NewSingleKeyProvider` (single key), or implement `HMACKeyProvider` interface for multi-tenant (`GetKey(keyId) (*secure.Credential, error)`, `ListKeyIds() []string`).

**Nonce stores:** Memory (`store.NewMemoryNonceStore` with TTL/eviction), Redis (`store.NewRedisStore`), KV (`store.NewKvStore`). Eviction policies: `EvictNone()`, `EvictAll()`, `EvictHalfLife()`.

**Note:** keyId cannot contain dots (`.`) — used internally for concatenation.

### 8.12 HTPasswd

Apache-style password file management with multiple hash algorithms. Thread-safe, supports file I/O.

```go
import "github.com/oddbit-project/blueprint/provider/htpasswd"

container := htpasswd.NewContainer()

// Add user (bcrypt by default)
container.AddUserPassword("alice", "secret123")

// Add with specific algorithm
container.AddUserWithHash("bob", "pass", htpasswd.HashTypeBcrypt)

// Verify
valid, err := container.VerifyUser("alice", "secret123")

// User management
container.UserExists("alice")
container.ListUsers()
container.Count()
container.DeleteUser("alice")

// File I/O
container, err := htpasswd.NewFromFile("/path/to/.htpasswd")
container.Write(file) // io.Writer
```

**Hash algorithms:** bcrypt (default, recommended), Apache MD5 (`$apr1$`), SHA256 (`{SHA256}`), SHA512 (`{SHA512}`), SHA1 (`{SHA}`, deprecated), crypt (deprecated), plain (dev only).

---
