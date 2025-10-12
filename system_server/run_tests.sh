#!/bin/bash

# FlexRun System Server Test Runner
# This script provides various options for running the test suite

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  FlexRun System Server Test Suite${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}Error: pytest is not installed${NC}"
    echo "Please install test requirements:"
    echo "  pip install -r test_requirements.txt"
    exit 1
fi

# Function to run tests
run_tests() {
    local test_type=$1
    local marker=$2
    local description=$3

    echo -e "${YELLOW}Running: ${description}${NC}"
    echo ""

    if [ -n "$marker" ]; then
        pytest tests/ -m "$marker" --ignore=/root/Arduino --ignore=Arduino
    else
        pytest tests/ --ignore=/root/Arduino --ignore=Arduino
    fi

    local exit_code=$?
    echo ""

    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✓ Tests passed${NC}"
    else
        echo -e "${RED}✗ Tests failed${NC}"
    fi

    return $exit_code
}

# Parse command line arguments
case "${1:-all}" in
    all)
        echo "Running all tests..."
        run_tests "all" "" "All Tests"
        ;;

    unit)
        echo "Running unit tests only..."
        run_tests "unit" "unit" "Unit Tests"
        ;;

    integration)
        echo "Running integration tests only..."
        run_tests "integration" "integration" "Integration Tests"
        ;;

    network)
        echo "Running network-related tests..."
        run_tests "network" "network" "Network Tests"
        ;;

    database)
        echo "Running database-related tests..."
        run_tests "database" "database" "Database Tests"
        ;;

    gpio)
        echo "Running GPIO tests..."
        run_tests "gpio" "gpio" "GPIO Tests"
        ;;

    quick)
        echo "Running quick tests (excluding slow tests)..."
        run_tests "quick" "not slow" "Quick Tests"
        ;;

    coverage)
        echo "Running tests with detailed coverage report..."
        pytest tests/ --cov=routes --cov=utils --cov-report=html --cov-report=term --ignore=/root/Arduino --ignore=Arduino
        echo ""
        echo -e "${GREEN}Coverage report generated in htmlcov/index.html${NC}"
        ;;

    file)
        if [ -z "$2" ]; then
            echo -e "${RED}Error: Please specify a test file${NC}"
            echo "Usage: ./run_tests.sh file <test_file>"
            exit 1
        fi
        echo "Running tests from file: $2"
        pytest "$2" -v
        ;;

    watch)
        echo "Running tests in watch mode..."
        echo "Tests will re-run when files change"
        pytest-watch
        ;;

    clean)
        echo "Cleaning test artifacts..."
        rm -rf .pytest_cache
        rm -rf htmlcov
        rm -rf .coverage
        rm -rf tests/__pycache__
        rm -rf tests/unit/__pycache__
        rm -rf tests/integration/__pycache__
        echo -e "${GREEN}✓ Cleaned${NC}"
        ;;

    help|--help|-h)
        echo "Usage: ./run_tests.sh [option]"
        echo ""
        echo "Options:"
        echo "  all          Run all tests (default)"
        echo "  unit         Run only unit tests"
        echo "  integration  Run only integration tests"
        echo "  network      Run tests requiring network access"
        echo "  database     Run tests requiring database access"
        echo "  gpio         Run GPIO-specific tests"
        echo "  quick        Run quick tests (exclude slow tests)"
        echo "  coverage     Run tests with detailed coverage report"
        echo "  file <path>  Run tests from specific file"
        echo "  watch        Run tests in watch mode (auto-rerun on changes)"
        echo "  clean        Clean test artifacts and cache"
        echo "  help         Show this help message"
        echo ""
        echo "Examples:"
        echo "  ./run_tests.sh                                    # Run all tests"
        echo "  ./run_tests.sh unit                               # Run unit tests"
        echo "  ./run_tests.sh file tests/unit/test_network_utils.py"
        echo "  ./run_tests.sh coverage                           # Generate coverage report"
        ;;

    *)
        echo -e "${RED}Error: Unknown option '$1'${NC}"
        echo "Run './run_tests.sh help' for usage information"
        exit 1
        ;;
esac

exit $?
