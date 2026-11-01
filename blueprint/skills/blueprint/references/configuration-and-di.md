# Configuration / Dependency Injection

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 4. Configuration

### 4.1 Config Struct

- One top-level `Config` per service with nested provider configs; JSON tags map to config file sections.
- Always declare `NewConfig()` returning sensible defaults. Blueprint provider configs expose their own `New*Config()` constructors; custom sub-configs follow the same pattern.

```go
type Config struct {
    WebApi     *ApiConfig          `json:"webApi"`
    Db         *pgsql.ClientConfig `json:"pgsql"`
    Metadata   *etcd.Config        `json:"etcd"`
    Prometheus *prometheus.Config  `json:"prometheus"`
    Log        *log.Config         `json:"log"`
    App        *AppConfig          `json:"app"`
    Auth       *AuthConfig         `json:"auth"`   // JWT keys as secure.KeyConfig, authentication §8.1
}

func NewConfig() *Config {
    return &Config{WebApi: NewApiConfig(), Db: pgsql.NewClientConfig(), Metadata: &etcd.Config{},
        Prometheus: prometheus.NewConfig(), Log: log.NewDefaultConfig(), App: NewAppConfig()}
}
```

### 4.2 Validation

Always provide `Validate()` that chains sub-validations, returning the first error:

```go
func (c *Config) Validate() error {
    if err := c.WebApi.Validate(); err != nil { return err }
    if err := c.Db.Validate(); err != nil { return err }
    if err := c.Metadata.Validate(); err != nil { return err }
    // ...
    if c.Auth != nil {
        if err := c.Auth.Validate(); err != nil {
            return err
        }
    }
    return nil
}
```

### 4.3 Loading

```go
cfg := NewConfig()
m.AbortFatal(m.Config.Get(cfg))   // load entire config
m.AbortFatal(cfg.Validate())

logCfg := log.NewDefaultConfig()
m.Config.GetKey("log", logCfg)    // or load a single section
```

### 4.4 Config File Format

JSON files in `config/`, one template per service. `pgsql.ClientConfig` takes a single `dsn` (plus pool fields `maxOpenConns`, `maxIdleConns`, `connLifetime`, `connIdleTime`); it has no host/port/database keys, and an empty `dsn` fails `Validate()` with `ErrEmptyDSN`. Keep the password out of the file: the pgx driver reads `PGPASSWORD` from the environment when the DSN has none.

```json
{
  "webApi": {
    "apiServer": { "host": "0.0.0.0", "port": 8080 }
  },
  "pgsql": { "dsn": "postgres://app@localhost:5432/myapp" },
  "etcd": { "endpoints": ["localhost:2379"] },
  "prometheus": { "host": "0.0.0.0", "port": 2111 },
  "log": { "level": "info" },
  "app": { "adminUser": "admin@localhost" },
  "auth": { "jwtPrivateKey": { "keyFile": "/run/secrets/jwt.key" }, "jwtPublicKey": { "keyFile": "/run/secrets/jwt.pub" } }
}
```

---

## 5. Dependency Injection

### 5.1 Context Structs

Two-level DI: **AppContext** holds infrastructure, **ServiceContext** holds business services.

```go
type AppContext struct {
    PgDb     *db.SqlClient
    Metadata metadata.Provider
    Auth      *goauth.AuthService
    AuthStore *pgstore.Store
    Logger    *log.Logger
    Svc      *ServiceContext
}

type ServiceContext struct {
    User    *service.UserService
    Product *service.ProductService
    Order   *service.OrderService
}

func NewServiceContext(ctx context.Context, appCtx *AppContext) (*ServiceContext, error) {
    return &ServiceContext{
        User:    service.NewUserService(ctx, appCtx.PgDb, appCtx.Logger),
        Product: service.NewProductService(ctx, appCtx.PgDb, appCtx.Logger),
        Order:   service.NewOrderService(ctx, appCtx.PgDb, appCtx.Logger),
    }, nil
}
```

### 5.2 Service Container (Flat Alternative)

- Simpler services (no GoAuth, no complex infrastructure): one flat container creates repositories once and wires all services.
- Services needing cross-domain validation receive multiple repositories (`NetworkService` gets `entityRepo` to verify FK existence).

```go
type Container struct {
    Entity  *EntityService
    Site    *SiteService
    Asset   *AssetService
    Network *NetworkService
}

func NewContainer(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *Container {
    entityRepo := repository.NewEntityRepository(ctx, conn, logger)
    siteRepo := repository.NewSiteRepository(ctx, conn, logger)
    assetRepo := repository.NewAssetRepository(ctx, conn, logger)
    networkRepo := repository.NewNetworkRepository(ctx, conn, logger)
    return &Container{
        Entity:  NewEntityService(entityRepo, logger),
        Site:    NewSiteService(siteRepo, logger),
        Asset:   NewAssetService(assetRepo, logger),
        Network: NewNetworkService(networkRepo, entityRepo, logger),
    }
}
```

### 5.3 Factory Functions

Cross-cutting concerns are initialized by factories in `pkg/factories/`. Factories call `app.AbortFatal(err)` on unrecoverable errors and register destructors for cleanup. `config` is `github.com/oddbit-project/blueprint/config`.

```go
func BuildLog(app *blueprint.Container, logger *log.Logger) *log.Logger {
    logCfg := log.NewDefaultConfig()
    if err := app.Config.GetKey("log", logCfg); err != nil {
        if errors.Is(err, config.ErrNoKey) {
            return logger // no "log" section: keep the default
        }
        app.AbortFatal(err) // a "log" section that does not parse is a config error, not a default
    }
    app.AbortFatal(logCfg.Validate())
    app.AbortFatal(log.Configure(logCfg))
    result, err := logCfg.Logger()
    app.AbortFatal(err)
    return result
}

func BuildMetadataClient(app *blueprint.Container, cfg *etcd.Config, logger *log.Logger) metadata.Provider {
    logger.Info("initializing etcd client...")
    app.AbortFatal(cfg.Validate())
    client, err := etcd.NewClient(cfg)
    app.AbortFatal(err)
    meta := metadata.NewEtcdMetadata(client, logger)
    blueprint.RegisterDestructor(func() error { return client.Close() })
    return meta
}
```

---
