#!/bin/bash
"""
Test runner script for Amazon Connect Quota Monitor

Runs all test suites with proper configuration and reporting
"""

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Amazon Connect Quota Monitor Test Suite${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Change to project directory
cd "$PROJECT_DIR"

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
python3 --version
echo ""

# Check for required modules
echo -e "${YELLOW}Checking for required Python modules...${NC}"
python3 -c "import boto3; print('✓ boto3 installed')" || {
    echo -e "${RED}✗ boto3 not installed${NC}"
    echo "Install with: pip install boto3"
    exit 1
}
echo ""

# Parse command line arguments
RUN_UNIT_TESTS=true
RUN_INTEGRATION_TESTS=false
RUN_COVERAGE=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --integration)
            RUN_INTEGRATION_TESTS=true
            shift
            ;;
        --no-unit)
            RUN_UNIT_TESTS=false
            shift
            ;;
        --coverage)
            RUN_COVERAGE=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --integration    Run integration tests (makes real AWS API calls)"
            echo "  --no-unit        Skip unit tests"
            echo "  --coverage       Generate coverage report (requires coverage package)"
            echo "  --verbose        Verbose output"
            echo "  --help          Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                           # Run unit tests only"
            echo "  $0 --integration             # Run unit and integration tests"
            echo "  $0 --coverage                # Run unit tests with coverage"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Initialize test results
UNIT_TEST_RESULT=0
INTEGRATION_TEST_RESULT=0
COVERAGE_RESULT=0

# Run unit tests
if [ "$RUN_UNIT_TESTS" = true ]; then
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Running Unit Tests${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    if [ "$RUN_COVERAGE" = true ]; then
        # Check if coverage is installed
        python3 -c "import coverage" 2>/dev/null || {
            echo -e "${YELLOW}Coverage module not installed. Installing...${NC}"
            pip install coverage
        }
        
        coverage run -m pytest tests/test_quota_monitor.py -v || UNIT_TEST_RESULT=$?
        
        echo ""
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}Coverage Report${NC}"
        echo -e "${BLUE}========================================${NC}"
        coverage report -m
        coverage html
        echo ""
        echo -e "${GREEN}HTML coverage report generated in htmlcov/index.html${NC}"
    else
        python3 tests/test_quota_monitor.py || UNIT_TEST_RESULT=$?
    fi
    
    echo ""
    if [ $UNIT_TEST_RESULT -eq 0 ]; then
        echo -e "${GREEN}✓ Unit tests passed${NC}"
    else
        echo -e "${RED}✗ Unit tests failed${NC}"
    fi
    echo ""
fi

# Run integration tests
if [ "$RUN_INTEGRATION_TESTS" = true ]; then
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Running Integration Tests${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    echo -e "${YELLOW}⚠️  WARNING: Integration tests will make real AWS API calls${NC}"
    echo -e "${YELLOW}   Ensure you have:${NC}"
    echo -e "${YELLOW}   - Valid AWS credentials configured${NC}"
    echo -e "${YELLOW}   - Appropriate IAM permissions${NC}"
    echo -e "${YELLOW}   - At least one Connect instance (for full tests)${NC}"
    echo ""
    
    # Set environment variable to enable integration tests
    export SKIP_INTEGRATION_TESTS=false
    
    python3 tests/test_integration.py || INTEGRATION_TEST_RESULT=$?
    
    echo ""
    if [ $INTEGRATION_TEST_RESULT -eq 0 ]; then
        echo -e "${GREEN}✓ Integration tests passed${NC}"
    else
        echo -e "${RED}✗ Integration tests failed${NC}"
    fi
    echo ""
fi

# Print final summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

if [ "$RUN_UNIT_TESTS" = true ]; then
    if [ $UNIT_TEST_RESULT -eq 0 ]; then
        echo -e "Unit Tests:        ${GREEN}✓ PASSED${NC}"
    else
        echo -e "Unit Tests:        ${RED}✗ FAILED${NC}"
    fi
fi

if [ "$RUN_INTEGRATION_TESTS" = true ]; then
    if [ $INTEGRATION_TEST_RESULT -eq 0 ]; then
        echo -e "Integration Tests: ${GREEN}✓ PASSED${NC}"
    else
        echo -e "Integration Tests: ${RED}✗ FAILED${NC}"
    fi
fi

echo ""
echo -e "${BLUE}========================================${NC}"

# Exit with error if any tests failed
if [ $UNIT_TEST_RESULT -ne 0 ] || [ $INTEGRATION_TEST_RESULT -ne 0 ]; then
    exit 1
fi

exit 0
