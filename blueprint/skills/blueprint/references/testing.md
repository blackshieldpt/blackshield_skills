# Testing

Part of the Blueprint skill. Section numbers match the original guide.

## 14. Testing

### 14.1 Test Infrastructure

testcontainers for real database instances, in `internal/testutil/database.go`:

```go
type TestContext struct {
    Ctx       context.Context
    Databases *TestDatabases
}

func NewPgTestContext(t *testing.T) *TestContext { ... }
func (tc *TestContext) Cleanup() { ... }
```

### 14.2 Test Suite Pattern

testify suites for integration tests:

```go
type ProductServiceTestSuite struct {
    suite.Suite
    tc      *testutil.TestContext
    service *ProductService
}

func (suite *ProductServiceTestSuite) SetupSuite() {
    suite.tc = testutil.NewPgTestContext(suite.T())
    // run migrations, create fixtures, wire services
}
func (suite *ProductServiceTestSuite) TearDownSuite() { suite.tc.Cleanup() }
func TestProductServiceSuite(t *testing.T)           { suite.Run(t, new(ProductServiceTestSuite)) }
```

### 14.3 Test Naming

- Suite runner: `TestXxxSuite(t *testing.T)`
- Test methods: `func (suite *XxxSuite) TestMethodName_Scenario()`
- Unique data helpers for isolation: `uniqueEmail(prefix)`, `uniqueName(prefix)`

### 14.4 Assertions

```go
suite.Require().NoError(err)           // fatal on error
suite.Assert().Equal(expected, actual)  // non-fatal
suite.Assert().NotNil(value)
suite.Assert().NotEmpty(value)
```

### 14.5 Makefile Targets

| Target | Command | Purpose |
|---|---|---|
| `test` | `go test ./...` | All tests |
| `test-unit` | `go test -short ./...` | Unit tests only |
| `test-integration` | `go test ./service/... -v` | Integration tests (with testcontainers) |
| `test-race` | `go test -race ./...` | Race detector |
| `test-coverage` | `go test -coverprofile=coverage.out ./...` | Coverage report |
| `test-file` | `go test -v ./$(dir $(FILE))/...` | Tests in a specific directory |

---
