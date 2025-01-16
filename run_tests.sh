#!/bin/bash

# This script automates the process of running load tests on an application
# using k6. It prompts for necessary information, sets up the testing
# environment, and executes the tests based on the provided arguments.
#
# Features:
# - Checks if the app is enabled for load testing
# - Prompts for base URL, application GitHub hash, and infrastructure GitHub hash
# - Creates a timestamped directory for storing test results
# - Supports running all test cases or a specific test case
#
# Usage:
#   ./run_tests.sh --all
#     Runs all available test cases sequentially
#
#   ./run_tests.sh --test <test_case_name>
#     Runs a specific test case by name
#
# Example:
#   ./run_tests.sh --all
#   ./run_tests.sh --test user_library_permissions
#
# Note: Ensure that k6 is installed and properly configured before running this script.
#
# Results:
# Test results are stored in the 'load_testing/.results/<date>/<time>/' directory.


# Function to print usage
print_usage() {
    echo "Usage: $0 [--all | --test <test_case_name>] [OPTIONS]"
    echo "Options:"
    echo "  --enabled <y/n>           Is the app enabled for load testing"
    echo "  --base-url <url>          Base URL of the app being tested"
    echo "  --app-hash <hash>         GitHub hash of the application version"
    echo "  --infra-hash <hash>       GitHub hash of the infrastructure version"
    exit 1
}

# Parse command-line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --all) run_all=true ;;
        --test) test_case="$2"; shift ;;
        --enabled) load_test_enabled="$2"; shift ;;
        --base-url) base_url="$2"; shift ;;
        --app-hash) app_github_hash="$2"; shift ;;
        --infra-hash) infra_github_hash="$2"; shift ;;
        *) print_usage ;;
    esac
    shift
done

# Check if either --all or --test is provided
if [[ -z "$run_all" && -z "$test_case" ]]; then
    print_usage
fi

# Prompt for missing values
if [[ -z "$load_test_enabled" ]]; then
    echo -n "Is the app enabled for load testing? [y/N]: "
    read load_test_enabled
fi

if [[ $load_test_enabled != "Y" && $load_test_enabled != "y" ]]; then
    echo "App is not enabled for load testing. Exiting..."
    exit 1
fi

if [[ -z "$base_url" ]]; then
    echo -n "Enter the Base URL of the app being tested: "
    read base_url
fi

if [[ -z "$app_github_hash" ]]; then
    echo -n "Enter the GitHub hash of the application version being tested: "
    read app_github_hash
fi

if [[ -z "$infra_github_hash" ]]; then
    echo -n "Enter the GitHub hash of the infrastructure version being tested: "
    read infra_github_hash
fi

# Create directory based on current date and time
current_date=$(date +"%Y%m%d")
current_time=$(date +"%H%M%S")
output_dir="load_testing/.results/${current_date}/${current_time}"

# Create the directory
mkdir -p "$output_dir"

run_test() {
    local test_case="$1"
    echo "Running test case: $test_case"
    
    k6 run \
        -e BASE_URL="$base_url" \
        -e APP_GITHUB_HASH="$app_github_hash" \
        -e INFRA_GITHUB_HASH="$infra_github_hash" \
        -e OUTPUT_DIR="$output_dir" \
        -e TEST_CASE="$test_case" \
        load_testing/k6_script.js
    
    return $?
}

if [ "$run_all" == true ]; then
    counter=0
    while true; do
        if ! run_test "$counter"; then
            echo "No more test cases. Exiting."
            break
        fi
        ((counter++))
    done
elif [ "$test_case" ]; then
    run_test "$test_case"
else
    print_usage
    exit 1
fi
