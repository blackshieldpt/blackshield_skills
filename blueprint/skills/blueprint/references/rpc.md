# Inter-Service RPC

Part of the Blueprint skill. Section numbers match the original guide.

## 13. Inter-Service RPC

### 13.1 Message Envelope

MessagePack-based envelope for Kafka / WAL messages:

```go
type Message struct {
    Type      int                `json:"messageType"`
    Version   int                `json:"version"`
    CreatedAt time.Time          `json:"createdAt"`
    Data      msgpack.RawMessage `json:"data"`
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
