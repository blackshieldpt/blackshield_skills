# Providers

Part of the Blueprint skill. Section numbers match the original guide.

## 19. Providers

### 19.1 SMTP Provider

Email client with multiple auth methods, TLS, attachments, and batch sending.

```go
import "github.com/oddbit-project/blueprint/provider/smtp"

config := smtp.NewConfig()
config.Host = "smtp.gmail.com"
config.Port = 587
config.Username = "sender@example.com"
config.AuthType = "plain"             // plain|login|crammd5|xoauth2|scram-sha-1|scram-sha-256|noauth
config.From = "noreply@example.com"
config.TLSEnable = true
config.Bcc = "audit@example.com"      // auto-BCC all messages
// Secure credential: config.PasswordEnvVar = "SMTP_PASSWORD"

mailer, err := smtp.NewMailer(config)

// Send with plain+HTML body and attachments
msg, err := mailer.NewMessage(
    []string{"user@example.com"},
    "Subject",
    smtp.WithFrom("custom@example.com"),
    smtp.WithBody("Plain text", "<h1>HTML</h1>"),
    smtp.WithAttachment("/path/to/file.pdf"),
)
mailer.Send(msg)

// Batch sending (NewMessage returns *gomail.Msg from github.com/wneessen/go-mail)
var msgs []*gomail.Msg
for _, r := range recipients {
    m, _ := mailer.NewMessage([]string{r.Email}, "Newsletter",
        smtp.WithBody(plain, html))
    msgs = append(msgs, m)
}
mailer.Send(msgs...)
```

**Errors:** `smtp.ErrInvalidTo`, `smtp.ErrInvalidFrom`, `smtp.ErrSMTPServer`, `smtp.ErrMessage`.

### 19.2 S3 Provider

S3-compatible storage (AWS S3, MinIO, DigitalOcean Spaces, Backblaze B2).

```go
import "github.com/oddbit-project/blueprint/provider/s3"

config := s3.NewConfig()
config.Endpoint = "localhost:9000"
config.Region = "us-east-1"
config.AccessKeyID = "minioadmin"
config.PasswordEnvVar = "S3_SECRET_KEY"
config.UseSSL = false
config.ForcePathStyle = true // required for MinIO

client, err := s3.NewClient(config, logger)
client.Connect(ctx)
defer client.Close()

// Bucket operations
bucket, _ := client.Bucket("my-bucket")
bucket.Create(ctx, s3.BucketOptions{Region: "us-east-1"})
exists, _ := bucket.Exists(ctx)
buckets, _ := client.ListBuckets(ctx)

// Object operations
bucket.PutObject(ctx, "key.txt", reader, size)
reader, _ := bucket.GetObject(ctx, "key.txt")
bucket.PutObjectStream(ctx, "key.txt", reader)         // unknown size
bucket.GetObjectStream(ctx, "key.txt", writer)          // stream to writer
bucket.DeleteObject(ctx, "key.txt")
bucket.CopyObject(ctx, "src.txt", "dest-bucket", "dst.txt")

// List and metadata
objects, _ := bucket.ListObjects(ctx, s3.ListOptions{Prefix: "docs/", MaxKeys: 100})
exists, _ := bucket.ObjectExists(ctx, "key.txt")
info, _ := bucket.HeadObject(ctx, "key.txt") // size, etag, content-type

// Presigned URLs
downloadURL, _ := bucket.PresignGetObject(ctx, "key.txt", 1*time.Hour)
uploadURL, _ := bucket.PresignPutObject(ctx, "new.txt", 30*time.Minute)
```

**Upload options:** `ContentType`, `CacheControl`, `Metadata`, `Tags`, `StorageClass`, `ServerSideEncryption`.

**Multipart config:** `MultipartThreshold` (default 100MB), `PartSize` (10MB), `Concurrency` (5).

**Connection pooling:** `MaxIdleConns` (50), `MaxIdleConnsPerHost` (10), `MaxConnsPerHost` (20), `IdleConnTimeout` (60s). Tune up for high-throughput workloads.

**Errors:** `s3.ErrBucketNotFound`, `s3.ErrBucketAlreadyExists`, `s3.ErrObjectNotFound`, `s3.ErrClientNotConnected`.

### 19.3 TLS Configuration

Blueprint provides shared TLS config structs embedded by other providers (SMTP, S3, NATS, httpserver).

```go
import "github.com/oddbit-project/blueprint/provider/tls"

// Client TLS (embedded in S3, SMTP, NATS configs)
clientCfg := &tls.ClientConfig{
    TLSCA:                  "/path/to/ca.crt",
    TLSCert:                "/path/to/client.crt",  // for mutual TLS
    TLSKey:                 "/path/to/client.key",
    TLSEnable:              true,
    TLSInsecureSkipVerify:  false,
}
tlsConfig, err := clientCfg.TLSConfig()

// Server TLS
serverCfg := &tls.ServerConfig{
    TLSCert:           "/path/to/server.crt",
    TLSKey:            "/path/to/server.key",
    TLSAllowedCACerts: []string{"/path/to/ca.crt"}, // client cert verification
    TLSCipherSuites:   []string{"TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384"},
    TLSMinVersion:     "1.3",
    TLSMaxVersion:     "1.3",
    TLSAllowedDNSNames: []string{"client.example.com"},
    TLSEnable:         true,
}
```

**Defaults:** TLS 1.3, strong cipher suites. Supports encrypted private keys via `TlsKeyCredential` (env var or file).

### 19.4 NATS Provider

Pub/sub messaging with core NATS and JetStream persistent streams.

**Core NATS (ephemeral pub/sub):**

```go
import "github.com/oddbit-project/blueprint/provider/nats"

// Producer
cfg := &nats.ProducerConfig{
    URL: "nats://localhost:4222", Subject: "events.created",
    AuthType: nats.AuthTypeNone,
}
producer, _ := nats.NewProducer(cfg, logger)
defer producer.Disconnect()

producer.Publish([]byte("raw data"))
producer.PublishJSON(myStruct)

// Request-Reply
reply, _ := producer.Request("req.subject", []byte("data"), 5*time.Second)

// Consumer
consumerCfg := &nats.ConsumerConfig{
    URL: "nats://localhost:4222", Subject: "events.created",
    AuthType: nats.AuthTypeNone,
    ConsumerOptions: nats.ConsumerOptions{QueueGroup: "my-service"},
}
consumer, _ := nats.NewConsumer(consumerCfg, logger)
defer consumer.Disconnect()

consumer.Subscribe(ctx, func(ctx context.Context, msg nats.Message) error {
    log.Info("received", log.KV{"data": string(msg.Data)})
    return nil
})
```

**Auth types:** `nats.AuthTypeNone`, `nats.AuthTypeBasic` (Username/Password), `nats.AuthTypeToken`.

**JetStream (persistent streams with acks):**

```go
// Producer
jsCfg := &nats.JSProducerConfig{
    JSConnectionConfig: nats.JSConnectionConfig{
        URL: "nats://localhost:4222", AuthType: nats.AuthTypeNone,
    },
    Subject: "orders.created",
    Stream: nats.StreamConfig{
        Name: "ORDERS", Subjects: []string{"orders.>"},
        Storage: "file", MaxAge: 24 * time.Hour, Replicas: 1,
    },
    AutoCreateStream: true, // false in prod — stream must pre-exist
}
jsProducer, _ := nats.NewJSProducer(jsCfg, logger)
defer jsProducer.Disconnect()

ack, _ := jsProducer.Publish(ctx, []byte(`{"id":"o-1"}`))
jsProducer.PublishJSON(ctx, myStruct)
fut, _ := jsProducer.PublishAsync(ctx, "orders.created", data) // non-blocking

// Consumer
jsConsumerCfg := &nats.JSConsumerConfig{
    JSConnectionConfig: nats.JSConnectionConfig{
        URL: "nats://localhost:4222", AuthType: nats.AuthTypeNone,
    },
    StreamName:    "ORDERS",
    Durable:       "orders-worker",
    FilterSubject: "orders.created",
    AckPolicy:     "explicit",
    AckWait:       30 * time.Second,
    MaxDeliver:    5,
    DeliverPolicy: "all", // "all"|"last"|"new"
}
jsConsumer, _ := nats.NewJSConsumer(jsConsumerCfg, logger)
defer jsConsumer.Disconnect()

// Continuous consumption: nil return = auto-Ack, error return = auto-Nak
jsConsumer.Consume(ctx, func(ctx context.Context, msg nats.JSMessage) error {
    return processOrder(ctx, msg.Data())
})

// Pull-style batch fetch
msgs, _ := jsConsumer.Fetch(50, 2*time.Second)
for _, m := range msgs {
    if err := process(m.Data()); err != nil {
        m.Nak()
        continue
    }
    m.Ack()
}
```

**StreamConfig:** `Retention` (`"limits"` | `"interest"` | `"workqueue"`), `Storage` (`"file"` | `"memory"`), `MaxAge`, `MaxMsgs`, `MaxBytes`, `Replicas`. Use `Native *jetstream.StreamConfig` escape hatch for advanced fields.

**JSMessage methods:** `Ack()`, `Nak()`, `InProgress()` (extend ack-wait), `Term()` (permanent reject), `Metadata()` (sequence, delivery count).

---
