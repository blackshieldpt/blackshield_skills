# Providers

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 19. Providers

Snippets elide error handling for brevity; in real code every `_` below is `err` checked and returned per the style rules.

Kafka (`provider/franz`), etcd (`provider/etcd`, beyond the configuration-and-di §5.3 factory) and Redis (`provider/redis`) have no reference in this skill. Before using one, read its package docs at the version `go.mod` pins (`go doc github.com/oddbit-project/blueprint/provider/franz`, or the module's README) instead of inferring the API from the providers below.

### 19.1 SMTP Provider

`github.com/oddbit-project/blueprint/provider/smtp` — multiple auth methods, TLS, attachments, batch sending.

```go
config := smtp.NewConfig()
config.Host = "smtp.gmail.com"; config.Port = 587
config.Username = "sender@example.com"
config.AuthType = "plain"               // plain|login|crammd5|xoauth2|scram-sha-1|scram-sha-256|noauth
config.From = "noreply@example.com"
config.TLSEnable = true
config.Bcc = "audit@example.com"        // auto-BCC all messages
config.PasswordEnvVar = "SMTP_PASSWORD" // secure credential
mailer, err := smtp.NewMailer(config)

msg, err := mailer.NewMessage([]string{"user@example.com"}, "Subject",
    smtp.WithFrom("custom@example.com"),
    smtp.WithBody("Plain text", "<h1>HTML</h1>"),
    smtp.WithAttachment("/path/to/file.pdf"))
mailer.Send(msg)
mailer.Send(msgs...) // batch: msgs []*gomail.Msg (github.com/wneessen/go-mail), one NewMessage per recipient
```

**Errors:** `smtp.ErrInvalidTo`, `smtp.ErrInvalidFrom`, `smtp.ErrSMTPServer`, `smtp.ErrMessage`.

### 19.2 S3 Provider

`github.com/oddbit-project/blueprint/provider/s3` — S3-compatible storage (AWS S3, MinIO, DigitalOcean Spaces, Backblaze B2).

```go
config := s3.NewConfig()
config.Endpoint = "localhost:9000"; config.Region = "us-east-1"
config.AccessKeyID = "minioadmin"; config.PasswordEnvVar = "S3_SECRET_KEY"
config.UseSSL = false; config.ForcePathStyle = true // required for MinIO
client, err := s3.NewClient(config, logger)
client.Connect(ctx); defer client.Close()
bucket, _ := client.Bucket("my-bucket")
```

| Area | Calls |
|---|---|
| Buckets | `bucket.Create(ctx, s3.BucketOptions{Region: "us-east-1"})`, `bucket.Exists(ctx)`, `client.ListBuckets(ctx)` |
| Objects | `bucket.PutObject(ctx, "key.txt", reader, size)`, `bucket.GetObject(ctx, "key.txt")`, `bucket.DeleteObject(ctx, "key.txt")`, `bucket.CopyObject(ctx, "src.txt", "dest-bucket", "dst.txt")` |
| Streams | `bucket.PutObjectStream(ctx, "key.txt", reader)` (unknown size), `bucket.GetObjectStream(ctx, "key.txt", writer)` (stream to writer) |
| List/metadata | `bucket.ListObjects(ctx, s3.ListOptions{Prefix: "docs/", MaxKeys: 100})`, `bucket.ObjectExists(ctx, "key.txt")`, `bucket.HeadObject(ctx, "key.txt")` (size, etag, content-type) |
| Presigned URLs | `bucket.PresignGetObject(ctx, "key.txt", 1*time.Hour)`, `bucket.PresignPutObject(ctx, "new.txt", 30*time.Minute)` |

- Upload options: `ContentType`, `CacheControl`, `Metadata`, `Tags`, `StorageClass`, `ServerSideEncryption`.
- Multipart: `MultipartThreshold` (default 100MB), `PartSize` (10MB), `Concurrency` (5).
- Connection pool: `MaxIdleConns` (50), `MaxIdleConnsPerHost` (10), `MaxConnsPerHost` (20), `IdleConnTimeout` (60s); tune up for high-throughput workloads.
- Errors: `s3.ErrBucketNotFound`, `s3.ErrBucketAlreadyExists`, `s3.ErrObjectNotFound`, `s3.ErrClientNotConnected`.

### 19.3 TLS Configuration

`github.com/oddbit-project/blueprint/provider/tls` — shared structs embedded by other providers (SMTP, S3, NATS, httpserver).

```go
// Client TLS (embedded in S3, SMTP, NATS configs)
clientCfg := &tls.ClientConfig{
    TLSCA: "/path/to/ca.crt",
    TLSCert: "/path/to/client.crt", TLSKey: "/path/to/client.key", // for mutual TLS
    TLSEnable: true, TLSInsecureSkipVerify: false,
}
tlsConfig, err := clientCfg.TLSConfig()

// Server TLS
serverCfg := &tls.ServerConfig{
    TLSCert: "/path/to/server.crt", TLSKey: "/path/to/server.key",
    TLSAllowedCACerts:  []string{"/path/to/ca.crt"}, // client cert verification
    TLSMinVersion: "TLS13", TLSMaxVersion: "TLS13", // only "TLS12" and "TLS13" are accepted; keep the 1.3 default unless a client needs 1.2
    TLSAllowedDNSNames: []string{"client.example.com"},
    TLSEnable: true,
}
```

Defaults: minimum TLS 1.3, strong cipher suites. Version strings are `"TLS12"` / `"TLS13"`; anything else (`"1.3"`, `"TLS1.3"`) fails with `ErrInvalidTlsVersion`. `TLSCipherSuites` only affects TLS 1.2 handshakes: Go's `crypto/tls` does not let you configure TLS 1.3 suites, so with the default 1.3 minimum the list has no effect. Lower `TLSMinVersion` to `"TLS12"` (and set `TLSCipherSuites`) only for a named client that cannot do 1.3. Encrypted private keys via `TlsKeyCredential` (env var or file).

### 19.4 NATS Provider

`github.com/oddbit-project/blueprint/provider/nats` — core NATS (ephemeral pub/sub) and JetStream (persistent streams with acks).

**Core NATS:**

```go
cfg := &nats.ProducerConfig{URL: "nats://localhost:4222", Subject: "events.created", AuthType: nats.AuthTypeNone}
producer, _ := nats.NewProducer(cfg, logger); defer producer.Disconnect()
producer.Publish([]byte("raw data"))
producer.PublishJSON(myStruct)
reply, _ := producer.Request("req.subject", []byte("data"), 5*time.Second) // request-reply

consumerCfg := &nats.ConsumerConfig{URL: "nats://localhost:4222", Subject: "events.created",
    AuthType: nats.AuthTypeNone, ConsumerOptions: nats.ConsumerOptions{QueueGroup: "my-service"}}
consumer, _ := nats.NewConsumer(consumerCfg, logger); defer consumer.Disconnect()
consumer.Subscribe(ctx, func(ctx context.Context, msg nats.Message) error {
    logger.Info("received", log.KV{"data": string(msg.Data)})   // package-level log.Info needs ctx first: log.Info(ctx, msg, log.ContextFields{...})
    return nil
})
```

Auth types: `nats.AuthTypeNone`, `nats.AuthTypeBasic` (Username/Password), `nats.AuthTypeToken`.

**JetStream:**

```go
jsCfg := &nats.JSProducerConfig{
    JSConnectionConfig: nats.JSConnectionConfig{URL: "nats://localhost:4222", AuthType: nats.AuthTypeNone},
    Subject: "orders.created",
    Stream: nats.StreamConfig{Name: "ORDERS", Subjects: []string{"orders.>"},
        Storage: "file", MaxAge: 24 * time.Hour, Replicas: 1},
    AutoCreateStream: true, // false in prod — stream must pre-exist
}
jsProducer, _ := nats.NewJSProducer(jsCfg, logger); defer jsProducer.Disconnect()
ack, _ := jsProducer.Publish(ctx, []byte(`{"id":"o-1"}`))
jsProducer.PublishJSON(ctx, myStruct)
fut, _ := jsProducer.PublishAsync(ctx, "orders.created", data) // non-blocking

jsConsumerCfg := &nats.JSConsumerConfig{
    JSConnectionConfig: nats.JSConnectionConfig{URL: "nats://localhost:4222", AuthType: nats.AuthTypeNone},
    StreamName: "ORDERS", Durable: "orders-worker", FilterSubject: "orders.created",
    AckPolicy: "explicit", AckWait: 30 * time.Second, MaxDeliver: 5,
    DeliverPolicy: "all", // "all"|"last"|"new"
}
jsConsumer, _ := nats.NewJSConsumer(jsConsumerCfg, logger); defer jsConsumer.Disconnect()

// Continuous consumption: nil return = auto-Ack, error return = auto-Nak
jsConsumer.Consume(ctx, func(ctx context.Context, msg nats.JSMessage) error {
    return processOrder(ctx, msg.Data())
})

// Pull-style batch fetch
msgs, _ := jsConsumer.Fetch(50, 2*time.Second)
for _, m := range msgs {
    if err := process(m.Data()); err != nil { m.Nak(); continue }
    m.Ack()
}
```

- `StreamConfig`: `Retention` (`"limits"` | `"interest"` | `"workqueue"`), `Storage` (`"file"` | `"memory"`), `MaxAge`, `MaxMsgs`, `MaxBytes`, `Replicas`. `Native *jetstream.StreamConfig` escape hatch for advanced fields.
- `JSMessage` methods: `Ack()`, `Nak()`, `InProgress()` (extend ack-wait), `Term()` (permanent reject), `Metadata()` (sequence, delivery count).

---
