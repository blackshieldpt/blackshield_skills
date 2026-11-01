# HTTP API Layer

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 7. HTTP API Layer

### 7.1 Server Setup

```go
type ApiConfig struct {
    ApiServer *httpserver.ServerConfig `json:"apiServer"`
}
type ApiServer struct {
    *httpserver.Server
    svc    *core.ServiceContext
    appCtx *core.AppContext
}

func NewApiServer(cfg *ApiConfig, appCtx *core.AppContext, logger *log.Logger) (*ApiServer, error) {
    srv, err := httpserver.NewServer(cfg.ApiServer, logger)   // err → return nil, err
    srv.UseDefaultSecurityHeaders()   // CSP, HSTS, X-Frame-Options, nosniff, ... from security.DefaultSecurityConfig()
    api := &ApiServer{Server: srv, svc: appCtx.Svc, appCtx: appCtx}
    v1 := srv.Group("/v1")
    v1.Use(gzip.Gzip(gzip.DefaultCompression))
    v1.POST("/login", api.login)                                    // public: before auth middleware
    v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))      // auth middleware
    v1.GET("/product", api.listProducts); v1.POST("/product", api.createProduct); v1.GET("/product/:id", api.getProduct)  // protected
    return api, nil
}
```

Use `UseDefaultSecurityHeaders()`. Do not load a `security.SecurityConfig` from JSON for `UseSecurityHeaders(cfg)`: the struct has no JSON tags, and the middleware sets only non-empty fields, so a partial or empty config (`"security": {}`) silently drops CSP, HSTS and X-Frame-Options. Pass a custom config only when every field is filled in code, starting from `security.DefaultSecurityConfig()`. CORS is separate: `security.CorsConfig` + `security.CORSMiddleware`.

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
func productError(g *gin.Context, err error, message ...string) {
    if errors.Is(err, entity.ErrNotFound)  { response.Http404(g); return }
    if errors.Is(err, entity.ErrDb)        { response.Http500(g, err); return }   // logs err with the request's trace ID; the client sees only "Internal Server Error"
    if errors.Is(err, entity.ErrNotUnique) { NewValidationError().AddField("name", "name already exists").Send(g); return }
    if len(message) > 0 { response.Http400(g, message[0]); return }   // caller says it is a client error
    response.Http500(g, err)   // unrecognised (driver, connection) errors: logged, generic 500
}
```

### 7.9 Shared Param Helpers

Path params used across handlers → helper that writes its own error response; callers check `err != nil` and return:

```go
func (s *ApiServer) _getRecord(g *gin.Context) (*entity.Record, error) {
    id, err := uuid.Parse(g.Param("id"))
    if err != nil { response.Http400(g, "invalid id"); return nil, err }
    record, err := s.svc.Product.GetById(id.String())
    if err != nil { productError(g, err) }   // ErrNotFound → 404, ErrDb → 500; not every error is a 404
    return record, err
}
// handler: record, err := s._getRecord(g); if err != nil { return }  // response already sent
```

### 7.10 Handler Pattern Summary

Canonical order: 1 auth guard (admin-only) → 2 parse/validate path params → 3 parse/validate JSON body → 4 call service → 5 map error → 6 success.

```go
func (s *ApiServer) updateProduct(g *gin.Context) {
    if u := s.CurrentUser(g); u == nil || !u.IsAdmin() { response.Http403(g); return }   // 1 (nil = unresolved user)
    id, err := uuid.Parse(g.Param("id"))
    if err != nil { response.Http400(g, "invalid id"); return }                          // 2
    req := &entity.ProductParams{}
    if !httpserver.ValidateJSON(g, req) { return }                                       // 3
    record, err := s.svc.Product.Update(id.String(), req)                                // 4 (repository queries use the ctx bound at construction)
    if err != nil {
        log.FromContext(g.Request.Context()).Error(err, "error updating product", log.KV{"id": id}) // 5
        productError(g, err); return
    }
    response.Success(g, record)                                                          // 6
}
```

### 7.11 Pagination

List endpoints paginate through query params; utilities live in `pkg/pagination/`. A LIMIT always applies: an absent or invalid `page` means page 1, so no request reads the whole table.

```go
type PaginationParams struct{ Page, PageSize int64 }

type PaginatedDetails struct {
    CurrentPage int64       `json:"currentPage"`
    TotalPages  int64       `json:"totalPages"`
    TotalCount  int64       `json:"totalCount"`
    Rows        interface{} `json:"rows"`
}

const defaultPageSize, maxPageSize int64 = 100, 1000

// Invalid, zero or negative values fall back to the defaults. pageSize is capped so a client
// cannot request an unbounded page (and a zero pageSize cannot reach the division below);
// page is capped so (page-1)*pageSize cannot overflow int64 into a huge uint offset.
func ParsePagination(g *gin.Context) *pagination.PaginationParams {
    params := &pagination.PaginationParams{Page: 1, PageSize: defaultPageSize}
    if v, err := strconv.ParseInt(g.Query("page"), 10, 64); err == nil && v > 0 && v <= math.MaxInt64/maxPageSize { params.Page = v }
    if v, err := strconv.ParseInt(g.Query("pageSize"), 10, 64); err == nil && v > 0 { params.PageSize = min(v, maxPageSize) }
    return params
}

func (r *EntityRepository) GetAll(params *pagination.PaginationParams) (*pagination.PaginatedDetails, error) {
    totalCount, err := r.repo.Count()   // Count() takes no query: whole table; CountWhere(db.FV{...}) for filters
    if err != nil { return nil, err }
    qry := r.repo.SqlSelect().Order(goqu.C("name").Asc()).
        Limit(uint(params.PageSize)).Offset(uint((params.Page - 1) * params.PageSize))
    result := make([]*entity.Entity, 0)
    if err = r.repo.Fetch(qry, &result); err != nil { return nil, err }
    totalPages := (totalCount + params.PageSize - 1) / params.PageSize
    return &pagination.PaginatedDetails{CurrentPage: params.Page, TotalPages: totalPages, TotalCount: totalCount, Rows: result}, nil
}
```

### 7.12 CSV Export

Stream CSV straight to the response. Set `Content-Disposition` with `attachment` to trigger browser download; write headers first, then stream rows; check `w.Error()` after flush — errors there are network issues (client disconnected), not data issues.

Every user-controlled cell goes through `csvCell`: a value whose first non-blank character is `=`, `+`, `-`, `@` (or the fullwidth `＝＋－＠`, which some spreadsheets also accept), or that starts with tab or carriage return, is run as a formula when the file is opened in a spreadsheet (CSV injection), so prefix it with `'`.

```go
func csvCell(s string) string {
    if s != "" && strings.ContainsRune("\t\r", rune(s[0])) { return "'" + s }
    if r, _ := utf8.DecodeRuneInString(strings.TrimLeft(s, " \t\r\n")); strings.ContainsRune("=+-@＝＋－＠", r) { return "'" + s }
    return s
}

func exportEntitiesCSV(g *gin.Context, svc *service.Container) {
    entities, err := svc.Entity.List(nil)
    if err != nil { response.Http500(g, err); return }   // Http500 logs err
    fname := fmt.Sprintf("entities-%s.csv", time.Now().Format("20060102"))
    g.Header("Content-Disposition", fmt.Sprintf("attachment; filename=%q", fname))
    g.Header("Content-Type", "text/csv; charset=utf-8")
    g.Status(200)
    w := csv.NewWriter(g.Writer)
    w.Write([]string{"ID", "Name", "Email", "Status"})
    for _, e := range entities { w.Write([]string{e.ID.String(), csvCell(e.Name), csvCell(e.Email), strconv.Itoa(e.Status)}) }
    w.Flush()
    if err := w.Error(); err != nil { log.FromContext(g.Request.Context()).Errorf(err, "failed to write CSV") }
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
| Backends | `kv.NewMemoryKV()` (dev/single-instance), `redis.NewClient()` (production/distributed); custom backends implement `kv.KV`: `SetTTL`/`Set`/`Get`/`Delete`/`Prune` |
| Marshallers | GOB (default, binary, fast); or JSON / a custom `Marshaller`. `WithMarshaller` is a `*session.Store` method, and `srv.UseSession` builds its store internally, so wire it by hand: `store, err := session.NewStore(sessionConfig, backend, logger)`; `store.WithMarshaller(session.NewJSONMarshaller())`; `mgr, err := session.NewManager(sessionConfig, session.ManagerWithStore(store), session.ManagerWithLogger(logger))`; `srv.AddMiddleware(mgr.Middleware())` |

### 7.14 Request Helpers

Import `github.com/oddbit-project/blueprint/provider/httpserver/request`.

| Helper | Use |
|---|---|
| `request.IsJSONRequest(g)` | Content-type detection: `g.JSON(200, data)` else `g.HTML(200, "template.html", data)` |
| `request.ContentTypeJson` / `ContentTypeHtml` / `ContentTypeBinary` | `"application/json"` / `"text/html"` / `"application/octet-stream"` |
| `srv.UseCSRFProtection()` (or `router.Use(security.CSRFProtection())`, package `.../provider/httpserver/security`) | CSRF protection. Needs the session middleware (§7.13) first: the token lives in the session under `_csrf` and is sent in the `X-CSRF-Token` response header; unsafe methods must echo it in `X-CSRF-Token` or a `_csrf` form field, else 403. For templates read it with `sess.GetString("_csrf")`; `security.GenerateCSRFToken(g)` only returns a new random value and stores nothing |

The `response` package (§7.6) auto-detects JSON vs HTML requests via `request.IsJSONRequest()` internally.

---
