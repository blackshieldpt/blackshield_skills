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
    srv, err := httpserver.NewServer(cfg.ApiServer, logger)   // err → return nil, err
    srv.UseDefaultSecurityHeaders()
    api := &ApiServer{Server: srv, svc: appCtx.Svc, appCtx: appCtx}
    v1 := srv.Group("/v1")
    v1.Use(gzip.Gzip(gzip.DefaultCompression))
    v1.POST("/login", api.login)                                    // public: before auth middleware
    v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))      // auth middleware
    v1.GET("/product", api.listProducts); v1.POST("/product", api.createProduct); v1.GET("/product/:id", api.getProduct)  // protected
    return api, nil
}
```

### 7.2 Request Body Size Limit

Limit body size to prevent large-payload DoS; apply early in the middleware chain. Usage: `v1.Use(maxBodyMiddleware(1 << 20))` (1 MiB).

```go
func maxBodyMiddleware(maxBytes int64) gin.HandlerFunc {
    return func(g *gin.Context) {
        if g.Request.Body != nil { g.Request.Body = http.MaxBytesReader(g.Writer, g.Request.Body, maxBytes) }
        g.Next()
    }
}
```

### 7.3 Handler Chaining

Concatenate optional middleware slices with a final handler for per-route composition:

```go
func chainHandlers(handler gin.HandlerFunc, middleware ...[]gin.HandlerFunc) []gin.HandlerFunc {
    var result []gin.HandlerFunc
    for _, mw := range middleware { result = append(result, mw...) }
    return append(result, handler)
}

authRL := []gin.HandlerFunc{authRateLimiter}; generalRL := []gin.HandlerFunc{generalRateLimiter}; jwtMW := []gin.HandlerFunc{jwtMiddleware}
v1.POST("/login", chainHandlers(h.Login, authRL)...)
v1.POST("/logout", chainHandlers(h.Logout, generalRL, jwtMW)...)
v1.GET("/me", chainHandlers(h.Me, generalRL, jwtMW)...)
```

### 7.4 Request Deserialization

| Source | Call | Rule |
|---|---|---|
| JSON body | `httpserver.ValidateJSON(g, req)` | Binds JSON, runs `binding:` tags, sends 400 with field-level errors on failure; returns `false` → just `return` (response already sent) |
| Path param | `g.Param("id")` | Always validate/parse immediately; 400 on failure |
| Query param | `g.Query("limit")`, `g.Query("tz")` | Optional values; returns `""` if absent |

Call shapes: `req := &entity.ProductParams{}; if !httpserver.ValidateJSON(g, req) { return }` and `id, err := uuid.Parse(g.Param("id")); if err != nil { response.Http400(g, "invalid id"); return }` (full form in §7.10). Request struct with Gin `binding` tags:

```go
type ProductParams struct {
    Name        string `json:"name" binding:"required,min=3,max=128"`
    CategoryId  string `json:"categoryId" binding:"required,uuid4"`
    Description string `json:"description" binding:"max=255"`
    Price       int    `json:"price" binding:"required,gt=0"`
}
```

### 7.5 Custom Validators

Register custom tags via Gin's binding engine in `init()`; use as `binding:"required,tz"`:

```go
func init() {
    if v, ok := binding.Validator.Engine().(*validator.Validate); ok {
        v.RegisterValidation("tz", func(fl validator.FieldLevel) bool {
            loc := fl.Field().String()
            if len(loc) > 0 { _, err := time.LoadLocation(loc); return err == nil }
            return true
        })
    }
}
```

### 7.6 Response Helpers

Package `github.com/oddbit-project/blueprint/provider/httpserver/response`:

| Call | Result |
|---|---|
| `response.Success(g, data)` | 200 with JSON body |
| `response.Http400(g, "human-readable message")` | 400 Bad Request |
| `response.Http401(g)` / `Http403(g)` / `Http404(g)` / `Http429(g)` | 401 / 403 / 404 / 429 |
| `response.Http500(g, err)` | 500 Internal Server Error |
| `response.ValidationError(g, []httpserver.ValidationError{{Field: "email", Message: "email already exists"}, {Field: "name", Message: "name is required"}})` | Structured per-field errors |

Raw JSON envelopes (SSE, streaming, custom shapes): `&response.JSONResponse{Success: true, Data: payload}`;
`&response.JSONResponseError{Success: false, Error: response.ErrorDetail{Message: "connection failed"}}`.

### 7.7 ValidationError Builder

Fluent wrapper over `httpserver.ValidationError`; usage `NewValidationError().AddField("name", "name already exists").Send(g)`:

```go
type ValidationError struct{ fields []httpserver.ValidationError }
func NewValidationError() *ValidationError { return &ValidationError{fields: make([]httpserver.ValidationError, 0)} }
func (v *ValidationError) AddField(fieldName string, error string) *ValidationError {
    v.fields = append(v.fields, httpserver.ValidationError{Field: fieldName, Message: error}); return v
}
func (v *ValidationError) Send(g *gin.Context) { response.ValidationError(g, v.fields) }
```

### 7.8 Domain Error Helpers

Map sentinels to HTTP; one helper per domain keeps handlers clean:

```go
var internalError = fmt.Errorf("internal error")

func productError(g *gin.Context, err error, message ...string) {
    if errors.Is(err, entity.ErrNotFound)  { response.Http404(g); return }
    if errors.Is(err, entity.ErrDb)        { response.Http500(g, internalError); return }
    if errors.Is(err, entity.ErrNotUnique) { NewValidationError().AddField("name", "name already exists").Send(g); return }
    if len(message) > 0 { response.Http400(g, message[0]); return }
    response.Http400(g, "operation failed")
}
```

### 7.9 Shared Param Helpers

Path params used across handlers → helper that writes its own error response; callers check `err != nil` and return:

```go
func (s *ApiServer) _getRecord(g *gin.Context) (*entity.Record, error) {
    id, err := uuid.Parse(g.Param("id"))
    if err != nil { response.Http400(g, "invalid id"); return nil, err }
    record, err := s.svc.Product.GetById(id.String())
    if err != nil { response.Http404(g) }
    return record, err
}
// handler: record, err := s._getRecord(g); if err != nil { return }  // response already sent
```

### 7.10 Handler Pattern Summary

Canonical order: 1 auth guard (admin-only) → 2 parse/validate path params → 3 parse/validate JSON body → 4 call service → 5 map error → 6 success.

```go
func (s *ApiServer) updateProduct(g *gin.Context) {
    if !s.CurrentUser(g).IsAdmin() { response.Http401(g); return }                       // 1
    id, err := uuid.Parse(g.Param("id"))
    if err != nil { response.Http400(g, "invalid id"); return }                          // 2
    req := &entity.ProductParams{}
    if !httpserver.ValidateJSON(g, req) { return }                                       // 3
    record, err := s.svc.Product.Update(g.Request.Context(), id.String(), req)           // 4
    if err != nil {
        log.FromContext(g).Error(err, "error updating product", log.KV{"id": id})        // 5
        productError(g, err); return
    }
    response.Success(g, record)                                                          // 6
}
```

### 7.11 Pagination

List endpoints: optional pagination via query params; utilities live in `pkg/pagination/`. When `page=0` or absent, return all rows with no pagination wrapper.

```go
type PaginationParams struct{ Page, PageSize int64 }
func (p *PaginationParams) IsPaginated() bool { return p.Page > 0 }

type PaginatedDetails struct {
    CurrentPage int64       `json:"currentPage"`
    TotalPages  int64       `json:"totalPages"`
    TotalCount  int64       `json:"totalCount"`
    Rows        interface{} `json:"rows"`
}

func ParsePagination(g *gin.Context) *pagination.PaginationParams {
    params := &pagination.PaginationParams{PageSize: 100}
    if v := g.Query("page"); v != "" { params.Page, _ = strconv.ParseInt(v, 10, 64) }
    if v := g.Query("pageSize"); v != "" { params.PageSize, _ = strconv.ParseInt(v, 10, 64) }
    return params
}

func (r *EntityRepository) GetAll(params *pagination.PaginationParams) (*pagination.PaginatedDetails, error) {
    totalCount, err := r.repo.Count(r.repo.SqlSelect())
    if err != nil { return nil, err }
    qry := r.repo.SqlSelect().Order(goqu.C("name").Asc())
    if params.IsPaginated() { qry = qry.Limit(uint(params.PageSize)).Offset(uint((params.Page - 1) * params.PageSize)) }
    result := make([]*entity.Entity, 0)
    if err = r.repo.Fetch(qry, &result); err != nil { return nil, err }
    totalPages := int64(0)
    if params.IsPaginated() { totalPages = (totalCount + params.PageSize - 1) / params.PageSize }
    return &pagination.PaginatedDetails{CurrentPage: params.Page, TotalPages: totalPages, TotalCount: totalCount, Rows: result}, nil
}
```

### 7.12 CSV Export

Stream CSV straight to the response. Set `Content-Disposition` with `attachment` to trigger browser download; write headers first, then stream rows; check `w.Error()` after flush — errors there are network issues (client disconnected), not data issues.

```go
func exportEntitiesCSV(g *gin.Context, svc *service.Container) {
    entities, err := svc.Entity.List(nil)
    if err != nil { log.FromContext(g).Errorf(err, "failed to list entities for CSV export"); response.Http500(g, errInternalError); return }
    fname := fmt.Sprintf("entities-%s.csv", time.Now().Format("20060102"))
    g.Header("Content-Disposition", fmt.Sprintf("attachment; filename=%q", fname))
    g.Header("Content-Type", "text/csv; charset=utf-8")
    g.Status(200)
    w := csv.NewWriter(g.Writer)
    w.Write([]string{"ID", "Name", "Email", "Status"})
    for _, e := range entities { w.Write([]string{e.ID.String(), e.Name, e.Email, strconv.Itoa(e.Status)}) }
    w.Flush()
    if err := w.Error(); err != nil { log.FromContext(g).Errorf(err, "failed to write CSV") }
}
```

### 7.13 Session Management

Cookie-based sessions with encryption, multiple storage backends, security features. Imports:
`github.com/oddbit-project/blueprint/provider/httpserver/session`,
`github.com/oddbit-project/blueprint/provider/kv`, `github.com/oddbit-project/blueprint/crypt/secure`.

```go
sessionConfig := session.NewConfig()
sessionConfig.CookieName = "app_session"
sessionConfig.ExpirationSeconds = 3600
sessionConfig.IdleTimeoutSeconds = 1800
sessionConfig.EncryptionKey = secure.DefaultCredentialConfig{PasswordEnvVar: "SESSION_ENCRYPTION_KEY"}
backend := kv.NewMemoryKV()   // or redis.NewClient(redisConfig) for prod
sessionManager, err := srv.UseSession(sessionConfig, backend, logger)
```

| Use | Calls |
|---|---|
| In handlers | `sess := session.Get(g)`; `sess.Set("key", "value")`; `val, ok := sess.GetString("key")`; `sess.Has("key")`; `sess.Delete("key")`; typed getters e.g. `sess.GetInt("count")` |
| Identity | Register custom types in `init()`: `gob.Register(&UserIdentity{})`. Login: `sess.SetIdentity(&UserIdentity{ID: user.ID, Username: user.Username})` then `sessionManager.Regenerate(g)` (prevents session fixation). Read: `identity, ok := sess.GetIdentity(); user := identity.(*UserIdentity)`. Logout: `sessionManager.Clear(g)` |
| Flash messages | One-time values across a single redirect: `sess.FlashString("Operation successful!")`; after redirect `msg, ok := sess.GetFlashString()` |
| Backends | `kv.NewMemoryKV()` (dev/single-instance), `redis.NewClient()` (production/distributed); custom backends implement `SetTTL`/`Get`/`Delete`/`Prune` |
| Marshallers | GOB (default, binary, fast); JSON via `sessionStore.WithMarshaller(session.NewJSONMarshaller())`; or custom implementing `Marshaller` |

### 7.14 Request Helpers

Import `github.com/oddbit-project/blueprint/provider/httpserver/request`.

| Helper | Use |
|---|---|
| `request.IsJSONRequest(g)` | Content-type detection: `g.JSON(200, data)` else `g.HTML(200, "template.html", data)` |
| `request.ContentTypeJson` / `ContentTypeHtml` / `ContentTypeBinary` | `"application/json"` / `"text/html"` / `"application/octet-stream"` |
| `router.Use(request.CSRFMiddleware())`; `csrfToken := request.GenerateCSRFToken(g)` | CSRF protection; pass token to templates |

The `response` package (§7.6) auto-detects JSON vs HTML requests via `request.IsJSONRequest()` internally.

---
