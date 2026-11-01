# Inter-Service RPC

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 13. Inter-Service RPC

### 13.1 Message Envelope

MessagePack-based envelope for Kafka / WAL messages. `msgpack` (vmihailenco/msgpack/v5) reads `msgpack:` tags and ignores `json:` tags; without a `msgpack:` tag the wire key is the Go field name (`Type`, not `messageType`). Tag both, here and on every record that travels over RPC (§13.3). Adding `msgpack:` tags changes the wire keys (`Type` becomes `messageType`), so every producer and consumer of a topic must switch in the same release, or old consumers read zero values from new messages:

```go
type Message struct {
    Type      int                `json:"messageType" msgpack:"messageType"`
    Version   int                `json:"version" msgpack:"version"`
    CreatedAt time.Time          `json:"createdAt" msgpack:"createdAt"`
    Data      msgpack.RawMessage `json:"data" msgpack:"data"`
}
```

### 13.2 Marshal / Unmarshal

```go
// Producer side
bytes, err := rpc.MarshalMsgPack(TypeEvent, 1, &eventPayload)

// Consumer side
msg, err := rpc.UnmarshalMessage(record.Value)
if msg.Type == TypeEvent {
    payload := &EventPayload{}
    err = msg.UnmarshalData(payload)
}
```

### 13.3 Domain Record Serialization

Records that travel over RPC implement Serialize/Deserialize:

```go
func (r *MyRecord) Serialize() ([]byte, error) {
    return msgpack.Marshal(r)
}

func DeserializeMyRecord(data []byte) (*MyRecord, error) {
    result := &MyRecord{}
    return result, msgpack.Unmarshal(data, result)
}
```

---
