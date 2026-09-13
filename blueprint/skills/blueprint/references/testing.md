# Testing

Part of the Blueprint skill. Section numbers match the original guide.

## 14. Testing

### 14.1 Test Infrastructure

Use testcontainers for real database instances:

```go
// internal/testutil/database.go
type TestContext struct {
    Ctx       context.Context
    Databases *TestDatabases
}

func NewPgTestContext(t *testing.T) *TestContext { ... }
func (tc *TestContext) Cleanup() { ... }
```

### 14.2 Test Suite Pattern

Use testify suites for integration tests:

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

func (suite *ProductServiceTestSuite) TearDownSuite() {
    suite.tc.Cleanup()
}

func TestProductServiceSuite(t *testing.T) {
    suite.Run(t, new(ProductServiceTestSuite))
}
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

```makefile
test:              ## Run all tests
    go test ./...

test-unit:         ## Unit tests only
    go test -short ./...

test-integration:  ## Integration tests (with testcontainers)
    go test ./service/... -v

test-race:         ## Tests with race detector
    go test -race ./...

test-coverage:     ## Coverage report
    go test -coverprofile=coverage.out ./...

test-file:         ## Run tests in specific directory
    go test -v ./$(dir $(FILE))/...
```

---
