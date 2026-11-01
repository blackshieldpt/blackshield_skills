# Logging

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

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

The httpserver log middleware stores the request logger (trace ID, request ID) in the **request's** context, not in Gin's key store. Pass `g.Request.Context()`: `log.FromContext(g)` with the `*gin.Context` finds nothing and returns a fresh `"default"` logger without the trace ID.

```go
log.FromContext(g.Request.Context()).Error(err, "failed to process request")
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
