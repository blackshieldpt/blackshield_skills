# Configuration / Dependency Injection

Part of the Blueprint skill. Section numbers match the original guide.

## 4. Configuration

### 4.1 Config Struct

Each service has a top-level `Config` with nested provider configs. JSON tags map to config file sections. Always declare a `NewConfig()` constructor that returns sensible defaults — Blueprint provider configs expose their own `New*Config()` constructors, and custom sub-configs should follow the same pattern.

```go
type Config struct {
    WebApi     *ApiConfig              `json:"webApi"`
    Db         *pgsql.ClientConfig     `json:"pgsql"`
    Metadata   *etcd.Config            `json:"etcd"`
    Prometheus *prometheus.Config       `json:"prometheus"`
    Log        *log.Config             `json:"log"`
    App        *AppConfig              `json:"app"`
}

func NewConfig() *Config {
    return &Config{
        WebApi:     NewApiConfig(),
        Db:         pgsql.NewClientConfig(),
        Metadata:   &etcd.Config{},
        Prometheus: prometheus.NewConfig(),
        Log:        log.NewDefaultConfig(),
        App:        NewAppConfig(),
    }
}
```

### 4.2 Validation

Always provide `Validate()` that chains sub-validations:

```go
func (c *Config) Validate() error {
    if err := c.WebApi.Validate(); err != nil {
        return err
    }
    if err := c.Db.Validate(); err != nil {
        return err
    }
    if err := c.Metadata.Validate(); err != nil {
        return err
    }
    // ...
    return nil
}
```

### 4.3 Loading

```go
cfg := NewConfig()
m.AbortFatal(m.Config.Get(cfg))       // load entire config
m.AbortFatal(cfg.Validate())

// or load a single section:
logCfg := log.NewDefaultConfig()
m.Config.GetKey("log", logCfg)
```

### 4.4 Config File Format

JSON files in `config/`. Each service has its own template:

```json
{
  "webApi": {
    "apiServer": { "host": "0.0.0.0", "port": 8080 },
    "security": {}
  },
  "pgsql": { "host": "localhost", "port": 5432, "database": "myapp" },
  "etcd": { "endpoints": ["localhost:2379"] },
  "prometheus": { "host": "0.0.0.0", "port": 2111 },
  "log": { "level": "info" },
  "app": { "adminUser": "admin@localhost" }
}
```

---

## 5. Dependency Injection

### 5.1 Context Structs

Two-level DI: an **AppContext** holds infrastructure, a **ServiceContext** holds business services.

```go
type AppContext struct {
    PgDb     *db.SqlClient
    Metadata metadata.Provider
    Auth     *goauth.AuthService
    Logger   *log.Logger
    Svc      *ServiceContext
}

type ServiceContext struct {
    User     *service.UserService
    Product  *service.ProductService
    Order    *service.OrderService
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

For simpler services without GoAuth or complex infrastructure, use a single flat container that creates repositories once and wires all services:

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

Services that need cross-domain validation receive multiple repositories (e.g., `NetworkService` receives `entityRepo` to verify FK existence).

### 5.3 Factory Functions

Cross-cutting concerns are initialized via factory functions in `pkg/factories/`. Factories use `app.AbortFatal(err)` for unrecoverable errors and register destructors for cleanup.

```go
func BuildLog(app *blueprint.Container, logger *log.Logger) *log.Logger {
    logCfg := log.NewDefaultConfig()
    if err := app.Config.GetKey("log", logCfg); err != nil {
        return logger // no custom config, return default
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
    blueprint.RegisterDestructor(func() error {
        return client.Close()
    })
    return meta
}
```

---
