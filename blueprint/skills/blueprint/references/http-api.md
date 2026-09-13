# HTTP API Layer

Part of the Blueprint skill. Section numbers match the original guide.

## 7. HTTP API Layer

### 7.1 Server Setup

```go
type ApiConfig struct {
    ApiServer *httpserver.ServerConfig `json:"apiServer"`
    CORS      *security.SecurityConfig `json:"security"`
}

type ApiServer struct {
    *httpserver.Server
    svc    *core.ServiceContext
    appCtx *core.AppContext
}

func NewApiServer(cfg *ApiConfig, appCtx *core.AppContext, logger *log.Logger) (*ApiServer, error) {
    srv, err := httpserver.NewServer(cfg.ApiServer, logger)
    if err != nil {
        return nil, err
    }
    srv.UseDefaultSecurityHeaders()

    api := &ApiServer{Server: srv, svc: appCtx.Svc, appCtx: appCtx}

    v1 := srv.Group("/v1")
    v1.Use(gzip.Gzip(gzip.DefaultCompression))

    // Public endpoints (before auth middleware)
    v1.POST("/login", api.login)

    // Auth middleware
    v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))

    // Protected endpoints
    v1.GET("/product", api.listProducts)
    v1.POST("/product", api.createProduct)
    v1.GET("/product/:id", api.getProduct)

    return api, nil
}
```

### 7.2 Request Body Size Limit

Limit request body size to prevent large-payload DoS attacks. Apply early in the middleware chain:

```go
func maxBodyMiddleware(maxBytes int64) gin.HandlerFunc {
    return func(g *gin.Context) {
        if g.Request.Body != nil {
            g.Request.Body = http.MaxBytesReader(g.Writer, g.Request.Body, maxBytes)
        }
        g.Next()
    }
}

// Usage: 1 MiB limit
v1.Use(maxBodyMiddleware(1 << 20))
```

### 7.3 Handler Chaining

Concatenate optional middleware slices with a final handler for flexible per-route middleware composition:

```go
func chainHandlers(handler gin.HandlerFunc, middleware ...[]gin.HandlerFunc) []gin.HandlerFunc {
    var result []gin.HandlerFunc
    for _, mw := range middleware {
        result = append(result, mw...)
    }
    return append(result, handler)
}
```

Usage — apply different middleware combinations per route:

```go
authRL := []gin.HandlerFunc{authRateLimiter}
generalRL := []gin.HandlerFunc{generalRateLimiter}
jwtMW := []gin.HandlerFunc{jwtMiddleware}

v1.POST("/login", chainHandlers(h.Login, authRL)...)
v1.POST("/logout", chainHandlers(h.Logout, generalRL, jwtMW)...)
v1.GET("/me", chainHandlers(h.Me, generalRL, jwtMW)...)
```

### 7.4 Request Deserialization

**JSON body** — use `httpserver.ValidateJSON()`. It binds JSON, runs `binding:` tag validation, and sends a 400 with field-level errors automatically on failure. Returns `false` if validation failed (response already sent — just return):

```go
req := &entity.ProductParams{}
if !httpserver.ValidateJSON(g, req) {
    return  // 400 already sent with validation details
}
```

The request struct uses Gin's `binding` tags for validation:

```go
type ProductParams struct {
    Name        string `json:"name" binding:"required,min=3,max=128"`
    CategoryId  string `json:"categoryId" binding:"required,uuid4"`
    Description string `json:"description" binding:"max=255"`
    Price       int    `json:"price" binding:"required,gt=0"`
}
```

**Path parameters** — use `g.Param()`. Always validate/parse immediately and return 400 on failure:

```go
id, err := uuid.Parse(g.Param("id"))
if err != nil {
    response.Http400(g, "invalid id")
    return
}
```

**Query parameters** — use `g.Query()` for optional values:

```go
limitStr := g.Query("limit")      // returns "" if absent
tz := g.Query("tz")
```

### 7.5 Custom Validators

Register custom validation tags via Gin's binding engine in an `init()` function:

```go
func init() {
    if v, ok := binding.Validator.Engine().(*validator.Validate); ok {
        v.RegisterValidation("tz", func(fl validator.FieldLevel) bool {
            loc := fl.Field().String()
            if len(loc) > 0 {
                _, err := time.LoadLocation(loc)
                return err == nil
            }
            return true
        })
    }
}
```

Then use in struct tags: `binding:"required,tz"`

### 7.6 Response Helpers

Blueprint's `response` package (`github.com/oddbit-project/blueprint/provider/httpserver/response`):

```go
// Success — 200 with JSON body
response.Success(g, data)

// Error responses — set status code + JSON error body
response.Http400(g, "human-readable message")   // 400 Bad Request
response.Http401(g)                              // 401 Unauthorized
response.Http403(g)                              // 403 Forbidden
response.Http404(g)                              // 404 Not Found
response.Http429(g)                              // 429 Too Many Requests
response.Http500(g, err)                         // 500 Internal Server Error
```

**Field-level validation errors** — send structured per-field error details:

```go
response.ValidationError(g, []httpserver.ValidationError{
    {Field: "email", Message: "email already exists"},
    {Field: "name", Message: "name is required"},
})
```

**Raw JSON response structs** — for SSE, streaming, or custom response shapes:

```go
// Success envelope
msg := &response.JSONResponse{
    Success: true,
    Data:    payload,
}

// Error envelope
msg := &response.JSONResponseError{
    Success: false,
    Error: response.ErrorDetail{
        Message: "connection failed",
    },
}
```

### 7.7 ValidationError Builder

Wrap `httpserver.ValidationError` in a fluent builder for cleaner handler code:

```go
type ValidationError struct {
    fields []httpserver.ValidationError
}

func NewValidationError() *ValidationError {
    return &ValidationError{fields: make([]httpserver.ValidationError, 0)}
}

func (v *ValidationError) AddField(fieldName string, error string) *ValidationError {
    v.fields = append(v.fields, httpserver.ValidationError{Field: fieldName, Message: error})
    return v
}

func (v *ValidationError) Send(g *gin.Context) {
    response.ValidationError(g, v.fields)
}
```

Usage:

```go
NewValidationError().
    AddField("name", "name already exists").
    Send(g)
```

### 7.8 Domain Error Helpers

Map sentinel errors to HTTP responses. One helper per domain — keeps handlers clean:

```go
var internalError = fmt.Errorf("internal error")

func productError(g *gin.Context, err error, message ...string) {
    if errors.Is(err, entity.ErrNotFound)  { response.Http404(g); return }
    if errors.Is(err, entity.ErrDb)        { response.Http500(g, internalError); return }
    if errors.Is(err, entity.ErrNotUnique) {
        NewValidationError().AddField("name", "name already exists").Send(g)
        return
    }
    if len(message) > 0 {
        response.Http400(g, message[0])
        return
    }
    response.Http400(g, "operation failed")
}
```

### 7.9 Shared Param Helpers

For path parameters used across multiple handlers, extract into a helper that writes its own error response. Callers just check `err != nil` and return:

```go
func (s *ApiServer) _getRecord(g *gin.Context) (*entity.Record, error) {
    id, err := uuid.Parse(g.Param("id"))
    if err != nil {
        response.Http400(g, "invalid id")
        return nil, err
    }
    record, err := s.svc.Product.GetById(id.String())
    if err != nil {
        response.Http404(g)
    }
    return record, err
}

// In handler:
record, err := s._getRecord(g)
if err != nil {
    return  // response already sent
}
```

### 7.10 Handler Pattern Summary

Full handler template showing the canonical order of operations:

```go
func (s *ApiServer) updateProduct(g *gin.Context) {
    // 1. Auth guard (if admin-only)
    if !s.CurrentUser(g).IsAdmin() {
        response.Http401(g)
        return
    }

    // 2. Parse + validate path params
    id, err := uuid.Parse(g.Param("id"))
    if err != nil {
        response.Http400(g, "invalid id")
        return
    }

    // 3. Parse + validate JSON body
    req := &entity.ProductParams{}
    if !httpserver.ValidateJSON(g, req) {
        return
    }

    // 4. Call service
    record, err := s.svc.Product.Update(g.Request.Context(), id.String(), req)
    if err != nil {
        // 5. Map error to HTTP response
        log.FromContext(g).Error(err, "error updating product", log.KV{"id": id})
        productError(g, err)
        return
    }

    // 6. Success
    response.Success(g, record)
}
```

### 7.11 Pagination

For list endpoints, support optional pagination via query parameters. Place pagination utilities in `pkg/pagination/`:

```go
type PaginationParams struct {
    Page     int64
    PageSize int64
}

type PaginatedDetails struct {
    CurrentPage int64       `json:"currentPage"`
    TotalPages  int64       `json:"totalPages"`
    TotalCount  int64       `json:"totalCount"`
    Rows        interface{} `json:"rows"`
}

func (p *PaginationParams) IsPaginated() bool {
    return p.Page > 0
}
```

**Parsing from query params:**

```go
func ParsePagination(g *gin.Context) *pagination.PaginationParams {
    params := &pagination.PaginationParams{PageSize: 100}
    if v := g.Query("page"); v != "" {
        params.Page, _ = strconv.ParseInt(v, 10, 64)
    }
    if v := g.Query("pageSize"); v != "" {
        params.PageSize, _ = strconv.ParseInt(v, 10, 64)
    }
    return params
}
```

**Applying to queries:**

```go
func (r *EntityRepository) GetAll(params *pagination.PaginationParams) (*pagination.PaginatedDetails, error) {
    totalCount, err := r.repo.Count(r.repo.SqlSelect())
    if err != nil {
        return nil, err
    }

    qry := r.repo.SqlSelect().Order(goqu.C("name").Asc())
    if params.IsPaginated() {
        qry = qry.
            Limit(uint(params.PageSize)).
            Offset(uint((params.Page - 1) * params.PageSize))
    }

    result := make([]*entity.Entity, 0)
    if err := r.repo.Fetch(qry, &result); err != nil {
        return nil, err
    }

    totalPages := int64(0)
    if params.IsPaginated() {
        totalPages = (totalCount + params.PageSize - 1) / params.PageSize
    }

    return &pagination.PaginatedDetails{
        CurrentPage: params.Page,
        TotalPages:  totalPages,
        TotalCount:  totalCount,
        Rows:        result,
    }, nil
}
```

When `page=0` or absent, return all rows with no pagination wrapper.

### 7.12 CSV Export

Stream CSV directly to the HTTP response for data export endpoints:

```go
func exportEntitiesCSV(g *gin.Context, svc *service.Container) {
    entities, err := svc.Entity.List(nil)
    if err != nil {
        log.FromContext(g).Errorf(err, "failed to list entities for CSV export")
        response.Http500(g, errInternalError)
        return
    }

    fname := fmt.Sprintf("entities-%s.csv", time.Now().Format("20060102"))
    g.Header("Content-Disposition", fmt.Sprintf("attachment; filename=%q", fname))
    g.Header("Content-Type", "text/csv; charset=utf-8")
    g.Status(200)

    w := csv.NewWriter(g.Writer)
    w.Write([]string{"ID", "Name", "Email", "Status"})
    for _, e := range entities {
        w.Write([]string{
            e.ID.String(),
            e.Name,
            e.Email,
            strconv.Itoa(e.Status),
        })
    }
    w.Flush()

    if err := w.Error(); err != nil {
        log.FromContext(g).Errorf(err, "failed to write CSV")
    }
}
```

Set `Content-Disposition` with `attachment` to trigger browser download. Write headers first, then stream rows. Check `w.Error()` after flush — errors at this point are network issues (client disconnected), not data issues.

### 7.13 Session Management

Blueprint provides cookie-based session management with encryption, multiple storage backends, and security features.

```go
import (
    "github.com/oddbit-project/blueprint/provider/httpserver/session"
    "github.com/oddbit-project/blueprint/provider/kv"
    "github.com/oddbit-project/blueprint/crypt/secure"
)

// Setup: memory backend (dev) or Redis (prod)
sessionConfig := session.NewConfig()
sessionConfig.CookieName = "app_session"
sessionConfig.ExpirationSeconds = 3600
sessionConfig.IdleTimeoutSeconds = 1800
sessionConfig.EncryptionKey = secure.DefaultCredentialConfig{
    PasswordEnvVar: "SESSION_ENCRYPTION_KEY",
}

backend := kv.NewMemoryKV() // or redis.NewClient(redisConfig) for prod
sessionManager, err := srv.UseSession(sessionConfig, backend, logger)
```

**Using sessions in handlers:**

```go
func handler(g *gin.Context) {
    sess := session.Get(g)

    sess.Set("key", "value")
    val, ok := sess.GetString("key")
    sess.Has("key")
    sess.Delete("key")

    // Typed getters
    intVal, ok := sess.GetInt("count")
}
```

**Identity management** — register custom types with `gob.Register()` in `init()`:

```go
func init() { gob.Register(&UserIdentity{}) }

// Login: set identity + regenerate session ID
sess := session.Get(g)
sess.SetIdentity(&UserIdentity{ID: user.ID, Username: user.Username})
sessionManager.Regenerate(g) // prevents session fixation

// Read identity
identity, ok := sess.GetIdentity()
user := identity.(*UserIdentity)

// Logout
sessionManager.Clear(g)
```

**Flash messages** — one-time values that persist across a single redirect:

```go
sess.FlashString("Operation successful!")
// After redirect:
msg, ok := sess.GetFlashString()
```

**Backends:** `kv.NewMemoryKV()` (dev/single-instance), `redis.NewClient()` (production/distributed). Custom backends implement `SetTTL`/`Get`/`Delete`/`Prune`.

**Marshallers:** GOB (default, binary, fast), JSON (`sessionStore.WithMarshaller(session.NewJSONMarshaller())`), or custom implementing `Marshaller` interface.

### 7.14 Request Helpers

```go
import "github.com/oddbit-project/blueprint/provider/httpserver/request"

// Content type detection
if request.IsJSONRequest(g) {
    g.JSON(200, data)
} else {
    g.HTML(200, "template.html", data)
}

// Content type constants
request.ContentTypeJson   // "application/json"
request.ContentTypeHtml   // "text/html"
request.ContentTypeBinary // "application/octet-stream"

// CSRF protection
router.Use(request.CSRFMiddleware())
csrfToken := request.GenerateCSRFToken(g) // pass to templates
```

The `response` package (see §7.6 above) auto-detects JSON vs HTML requests via `request.IsJSONRequest()` internally.

---
