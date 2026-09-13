# Logging

Part of the Blueprint skill. Section numbers match the original guide.

## 15. Logging

### 15.1 Logger Creation

```go
logger := log.New("component-name")
```

### 15.2 Log Methods

```go
logger.Info("message")
logger.Infof("formatted %s", value)
logger.Error(err, "context message")
logger.Errorf(err, "formatted error %s", value)
logger.Info("message", log.KV{"key": value})
```

### 15.3 In Gin Handlers

```go
log.FromContext(g).Error(err, "failed to process request")
```

### 15.4 Nil Logger Fallback

Services accept `nil` logger and create a default:

```go
func NewMyService(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *MyService {
    if logger == nil {
        logger = log.New("svcMy")
    }
    // ...
}
```

---
