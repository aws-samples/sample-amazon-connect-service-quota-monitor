# Amazon Connect Quota Monitor - Test Suite

Comprehensive test suite for validating the Amazon Connect Service Quota Monitor functionality.

## Test Structure

```
tests/
├── README.md                  # This file
├── test_quota_monitor.py      # Unit tests
├── test_integration.py        # Integration tests (real AWS calls)
└── run_tests.sh              # Test runner script
```

## Test Categories

### 1. Unit Tests (`test_quota_monitor.py`)

**Purpose:** Test individual components in isolation with mocked AWS services.

**Test Classes:**
- `TestInstanceDiscovery` - Dynamic instance discovery
- `TestQuotaMonitoring` - Quota monitoring methods
- `TestAlertConsolidation` - Alert consolidation engine
- `TestStorageEngine` - S3 and DynamoDB storage
- `TestErrorHandling` - Error handling and recovery
- `TestEdgeCases` - Edge cases and boundary conditions
- `TestIntegration` - Basic integration flows

**Running Unit Tests:**
```bash
# Run all unit tests
python3 tests/test_quota_monitor.py

# Or use the test runner
./tests/run_tests.sh
```

### 2. Integration Tests (`test_integration.py`)

**Purpose:** Validate end-to-end functionality with real AWS resources.

⚠️ **WARNING:** Integration tests make actual AWS API calls and may incur costs.

**Requirements:**
- Valid AWS credentials configured
- IAM permissions for Connect, CloudWatch, SNS, etc.
- At least one Connect instance (for full tests)

**Test Classes:**
- `TestRealAWSIntegration` - Real instance discovery and monitoring
- `TestAPIThrottlingMonitor` - Throttling monitor execution

**Running Integration Tests:**
```bash
# Enable integration tests
export SKIP_INTEGRATION_TESTS=false

# Run integration tests
python3 tests/test_integration.py

# Or use the test runner
./tests/run_tests.sh --integration
```

## Quick Start

### Run Unit Tests Only (Safe)
```bash
./tests/run_tests.sh
```

### Run All Tests (Including AWS Integration)
```bash
./tests/run_tests.sh --integration
```

### Run with Coverage Report
```bash
# Install coverage first if needed
pip install coverage

# Run tests with coverage
./tests/run_tests.sh --coverage
```

## Test Runner Options

```bash
./tests/run_tests.sh [OPTIONS]

Options:
  --integration    Run integration tests (makes real AWS API calls)
  --no-unit        Skip unit tests
  --coverage       Generate coverage report
  --verbose        Verbose output
  --help          Show help message
```

## Test Scenarios Covered

### Instance Discovery
- ✅ Single instance discovery
- ✅ Multiple instances discovery
- ✅ Filtering inactive instances
- ✅ Instance metadata enhancement
- ✅ Cache behavior
- ✅ Pagination handling

### Quota Monitoring
- ✅ API count method (list_users, list_queues, etc.)
- ✅ CloudWatch metrics method
- ✅ Service Quotas API method
- ✅ Multi-level API counting
- ✅ Threshold violation detection
- ✅ Account-level vs instance-level quotas

### Alert Consolidation
- ✅ Multiple violations in single alert
- ✅ Severity determination (CRITICAL, HIGH, MEDIUM, LOW)
- ✅ SNS message formatting
- ✅ Account-level alerts
- ✅ Instance-level alerts

### Storage
- ✅ S3 storage with date partitioning
- ✅ DynamoDB storage with indexes
- ✅ Dual storage (S3 + DynamoDB)
- ✅ Storage connectivity testing
- ✅ Error handling for storage failures

### Error Handling
- ✅ API throttling with exponential backoff
- ✅ Permission errors (no retry)
- ✅ Network errors (reconnect)
- ✅ Service unavailability
- ✅ Invalid parameters

### Edge Cases
- ✅ Zero quota limits
- ✅ Usage exceeding limits (soft limits)
- ✅ Empty instance lists
- ✅ Missing environment variables
- ✅ Invalid configurations

## Expected Test Results

### Unit Tests
```
test_discover_single_instance ...................... ok
test_discover_multiple_instances ................... ok
test_filter_inactive_instances ..................... ok
test_api_count_method .............................. ok
test_cloudwatch_method ............................. ok
test_threshold_violation_detection ................. ok
test_consolidate_multiple_violations ............... ok
test_severity_determination ........................ ok
test_s3_storage .................................... ok
test_dynamodb_storage .............................. ok
test_handle_api_throttling ......................... ok
test_handle_permission_errors ...................... ok
test_zero_quota_limit .............................. ok
test_usage_exceeds_limit ........................... ok
test_empty_instance_list ........................... ok

----------------------------------------------------------------------
Ran 15 tests in 0.234s

OK
```

### Integration Tests (if enabled)
```
test_real_instance_discovery ....................... ok
test_real_quota_monitoring ......................... ok
test_lambda_handler_execution ...................... ok
test_throttling_monitor_execution .................. ok

----------------------------------------------------------------------
Ran 4 tests in 12.456s

OK
```

## Troubleshooting

### Import Errors
```
ModuleNotFoundError: No module named 'lambda_function'
```
**Solution:** Ensure you're running tests from the project root directory.

### AWS Credential Errors
```
NoCredentialsError: Unable to locate credentials
```
**Solution:** Configure AWS credentials:
```bash
aws configure
# or
export AWS_PROFILE=your-profile
```

### Permission Errors
```
ClientError: An error occurred (AccessDeniedException)
```
**Solution:** Ensure your IAM role/user has required permissions:
- `connect:ListInstances`
- `connect:ListUsers`
- `servicequotas:GetServiceQuota`
- `cloudwatch:GetMetricStatistics`

### Missing Dependencies
```
ModuleNotFoundError: No module named 'boto3'
```
**Solution:** Install required packages:
```bash
pip install boto3
pip install coverage  # Optional, for coverage reports
```

## Continuous Integration

### GitHub Actions Example
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: pip install boto3 coverage
      - name: Run unit tests
        run: ./tests/run_tests.sh --coverage
      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

## Adding New Tests

### Unit Test Template
```python
def test_your_feature(self):
    """Test description"""
    # Arrange
    setup_data = {...}
    
    # Act
    result = function_to_test(setup_data)
    
    # Assert
    self.assertEqual(result, expected_value)
    self.assertTrue(condition)
```

### Integration Test Template
```python
def test_your_integration(self):
    """Integration test description"""
    if self.skip_tests:
        self.skipTest("Integration tests disabled")
    
    try:
        # Test with real AWS resources
        result = real_aws_call()
        self.assertIsNotNone(result)
    except Exception as e:
        self.fail(f"Test failed: {str(e)}")
```

## Coverage Goals

Target coverage metrics:
- **Overall:** > 80%
- **Core monitoring logic:** > 90%
- **Error handling:** > 85%
- **Integration points:** > 75%

## Best Practices

1. **Always run unit tests before committing**
   ```bash
   ./tests/run_tests.sh
   ```

2. **Run integration tests before releases**
   ```bash
   ./tests/run_tests.sh --integration
   ```

3. **Use mocking for external dependencies**
   - Mock AWS service calls
   - Mock time-dependent operations
   - Mock network operations

4. **Test error conditions**
   - API throttling
   - Network failures
   - Permission errors
   - Invalid inputs

5. **Keep tests fast**
   - Unit tests should run in < 1 second each
   - Use mocks instead of sleeps
   - Minimize setup/teardown

## Resources

- [Python unittest documentation](https://docs.python.org/3/library/unittest.html)
- [boto3 testing with moto](https://github.com/spulec/moto)
- [Coverage.py documentation](https://coverage.readthedocs.io/)

## Support

For issues with tests:
1. Check CloudWatch Logs for Lambda execution details
2. Verify AWS credentials and permissions
3. Review test output for specific error messages
4. Check this README for common issues

---

**Last Updated:** March 30, 2026
