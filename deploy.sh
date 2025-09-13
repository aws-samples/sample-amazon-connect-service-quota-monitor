#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Enhanced Intelligent Deployment Script for Amazon Connect Service Quota Monitor
# This script provides intelligent deployment with automatic code size detection,
# S3 fallback for large packages, and comprehensive deployment management

set -e

# Script version and metadata
SCRIPT_VERSION="2.0"
SCRIPT_NAME="Enhanced Connect Quota Monitor Deployment"

# Configuration
STACK_NAME="${STACK_NAME:-ConnectQuotaMonitor}"
TEMPLATE_FILE="connect-quota-monitor-cfn.yaml"
PYTHON_SCRIPT="lambda_function.py"

# CloudFormation limits
CF_INLINE_CODE_LIMIT=4096  # 4KB limit for inline Lambda code
CF_TEMPLATE_SIZE_LIMIT=51200  # 50KB limit for CloudFormation template

# Default parameters (can be overridden via environment variables)
THRESHOLD_PERCENTAGE=${THRESHOLD_PERCENTAGE:-80}
NOTIFICATION_EMAIL=${NOTIFICATION_EMAIL:-""}
VPC_ID=${VPC_ID:-""}
SUBNET_IDS=${SUBNET_IDS:-""}
SCHEDULE_EXPRESSION=${SCHEDULE_EXPRESSION:-"rate(1 hour)"}
LAMBDA_RUNTIME=${LAMBDA_RUNTIME:-"python3.12"}
LAMBDA_MEMORY=${LAMBDA_MEMORY:-512}
LAMBDA_TIMEOUT=${LAMBDA_TIMEOUT:-600}
USE_S3_STORAGE=${USE_S3_STORAGE:-"true"}
USE_DYNAMODB_STORAGE=${USE_DYNAMODB_STORAGE:-"true"}
S3_BUCKET_NAME=${S3_BUCKET_NAME:-""}
DYNAMODB_TABLE_NAME=${DYNAMODB_TABLE_NAME:-"ConnectQuotaMonitor"}
CREATE_DEPLOYMENT_BUCKET=${CREATE_DEPLOYMENT_BUCKET:-"true"}
DEPLOYMENT_BUCKET_NAME=${DEPLOYMENT_BUCKET_NAME:-""}

# Deployment options
FORCE_S3_DEPLOYMENT=${FORCE_S3_DEPLOYMENT:-"false"}
SKIP_TESTS=${SKIP_TESTS:-"false"}
VERBOSE=${VERBOSE:-"false"}
DRY_RUN=${DRY_RUN:-"false"}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

log_step() {
    echo -e "${PURPLE}🔄 $1${NC}"
}

log_verbose() {
    if [ "$VERBOSE" = "true" ]; then
        echo -e "${CYAN}🔍 $1${NC}"
    fi
}

# Function to display usage information
show_usage() {
    cat << EOF
$SCRIPT_NAME v$SCRIPT_VERSION

USAGE:
    $0 [OPTIONS]

OPTIONS:
    -h, --help                  Show this help message
    -v, --verbose              Enable verbose logging
    -d, --dry-run              Show what would be deployed without executing
    -f, --force-s3             Force S3 deployment method regardless of code size
    -s, --skip-tests           Skip Lambda function testing after deployment
    -n, --stack-name NAME      CloudFormation stack name (default: ConnectQuotaMonitor)
    -e, --email EMAIL          Notification email address
    -t, --threshold PERCENT    Alert threshold percentage (1-99, default: 80)
    -r, --runtime VERSION      Lambda runtime version (default: python3.12)
    -m, --memory MB            Lambda memory in MB (default: 512)
    --timeout SECONDS          Lambda timeout in seconds (default: 600)
    --schedule EXPRESSION      Schedule expression (default: rate(1 hour))
    --vpc-id VPC_ID            VPC ID for Lambda deployment
    --subnet-ids SUBNET_IDS    Comma-separated subnet IDs
    --s3-bucket BUCKET         Custom S3 bucket name for storage
    --dynamodb-table TABLE     DynamoDB table name (default: ConnectQuotaMonitor)
    --deployment-bucket BUCKET Custom deployment bucket name

ENVIRONMENT VARIABLES:
    All options can also be set via environment variables:
    STACK_NAME, NOTIFICATION_EMAIL, THRESHOLD_PERCENTAGE, LAMBDA_RUNTIME,
    LAMBDA_MEMORY, LAMBDA_TIMEOUT, SCHEDULE_EXPRESSION, VPC_ID, SUBNET_IDS,
    USE_S3_STORAGE, USE_DYNAMODB_STORAGE, S3_BUCKET_NAME, DYNAMODB_TABLE_NAME,
    CREATE_DEPLOYMENT_BUCKET, DEPLOYMENT_BUCKET_NAME, FORCE_S3_DEPLOYMENT,
    SKIP_TESTS, VERBOSE, DRY_RUN

EXAMPLES:
    # Basic deployment
    $0 --email admin@company.com

    # Advanced deployment with custom settings
    $0 --email admin@company.com --threshold 85 --runtime python3.12 --memory 1024

    # VPC deployment
    $0 --email admin@company.com --vpc-id vpc-12345 --subnet-ids subnet-123,subnet-456

    # Dry run to see what would be deployed
    $0 --dry-run --verbose

EOF
}

# Function to parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_usage
                exit 0
                ;;
            -v|--verbose)
                VERBOSE="true"
                shift
                ;;
            -d|--dry-run)
                DRY_RUN="true"
                shift
                ;;
            -f|--force-s3)
                FORCE_S3_DEPLOYMENT="true"
                shift
                ;;
            -s|--skip-tests)
                SKIP_TESTS="true"
                shift
                ;;
            -n|--stack-name)
                STACK_NAME="$2"
                shift 2
                ;;
            -e|--email)
                NOTIFICATION_EMAIL="$2"
                shift 2
                ;;
            -t|--threshold)
                THRESHOLD_PERCENTAGE="$2"
                shift 2
                ;;
            -r|--runtime)
                LAMBDA_RUNTIME="$2"
                shift 2
                ;;
            -m|--memory)
                LAMBDA_MEMORY="$2"
                shift 2
                ;;
            --timeout)
                LAMBDA_TIMEOUT="$2"
                shift 2
                ;;
            --schedule)
                SCHEDULE_EXPRESSION="$2"
                shift 2
                ;;
            --vpc-id)
                VPC_ID="$2"
                shift 2
                ;;
            --subnet-ids)
                SUBNET_IDS="$2"
                shift 2
                ;;
            --s3-bucket)
                S3_BUCKET_NAME="$2"
                shift 2
                ;;
            --dynamodb-table)
                DYNAMODB_TABLE_NAME="$2"
                shift 2
                ;;
            --deployment-bucket)
                DEPLOYMENT_BUCKET_NAME="$2"
                shift 2
                ;;
            *)
                log_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

# Function to validate prerequisites
validate_prerequisites() {
    log_step "Validating prerequisites..."
    
    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI is not installed or not in PATH"
        exit 1
    fi
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured or invalid"
        exit 1
    fi
    
    # Check required files
    if [ ! -f "$TEMPLATE_FILE" ]; then
        log_error "CloudFormation template not found: $TEMPLATE_FILE"
        exit 1
    fi
    
    if [ ! -f "$PYTHON_SCRIPT" ]; then
        log_error "Python script not found: $PYTHON_SCRIPT"
        exit 1
    fi
    
    # Validate parameters
    if [[ ! "$THRESHOLD_PERCENTAGE" =~ ^[1-9][0-9]?$ ]] || [ "$THRESHOLD_PERCENTAGE" -gt 99 ]; then
        log_error "Threshold percentage must be between 1 and 99"
        exit 1
    fi
    
    if [ ! -z "$NOTIFICATION_EMAIL" ] && [[ ! "$NOTIFICATION_EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        log_error "Invalid email address format: $NOTIFICATION_EMAIL"
        exit 1
    fi
    
    log_success "Prerequisites validated"
}

# Function to detect code size and determine deployment method
detect_deployment_method() {
    log_step "Analyzing code size and determining deployment method..."
    
    # Create temporary deployment package to check size
    local temp_dir=$(mktemp -d)
    local temp_zip="$temp_dir/temp-package.zip"
    
    # Copy Python script to temp directory
    cp "$PYTHON_SCRIPT" "$temp_dir/"
    
    # Create ZIP package
    cd "$temp_dir"
    zip -r "$temp_zip" . > /dev/null 2>&1
    cd - > /dev/null
    
    # Get package size
    local package_size=$(stat -f%z "$temp_zip" 2>/dev/null || stat -c%s "$temp_zip" 2>/dev/null)
    local package_size_kb=$((package_size / 1024))
    
    log_verbose "Lambda package size: ${package_size} bytes (${package_size_kb} KB)"
    
    # Clean up temp files
    rm -rf "$temp_dir"
    
    # Determine deployment method
    if [ "$FORCE_S3_DEPLOYMENT" = "true" ]; then
        DEPLOYMENT_METHOD="s3"
        log_info "Forced S3 deployment method"
    elif [ "$package_size" -gt "$CF_INLINE_CODE_LIMIT" ]; then
        DEPLOYMENT_METHOD="s3"
        log_warning "Code size (${package_size_kb} KB) exceeds CloudFormation inline limit (4 KB)"
        log_info "Automatically using S3 deployment method"
    else
        DEPLOYMENT_METHOD="placeholder"
        log_info "Code size (${package_size_kb} KB) is within CloudFormation inline limit"
        log_info "Using placeholder deployment method"
    fi
    
    log_verbose "Selected deployment method: $DEPLOYMENT_METHOD"
}

# Function to build CloudFormation parameters
build_parameters() {
    log_step "Building CloudFormation parameters..."
    
    PARAMETERS="ParameterKey=ThresholdPercentage,ParameterValue=$THRESHOLD_PERCENTAGE"
    PARAMETERS="$PARAMETERS ParameterKey=LambdaRuntime,ParameterValue=$LAMBDA_RUNTIME"
    PARAMETERS="$PARAMETERS ParameterKey=LambdaMemory,ParameterValue=$LAMBDA_MEMORY"
    PARAMETERS="$PARAMETERS ParameterKey=LambdaTimeout,ParameterValue=$LAMBDA_TIMEOUT"
    PARAMETERS="$PARAMETERS ParameterKey=DeploymentMethod,ParameterValue=$DEPLOYMENT_METHOD"
    PARAMETERS="$PARAMETERS ParameterKey=UseS3Storage,ParameterValue=$USE_S3_STORAGE"
    PARAMETERS="$PARAMETERS ParameterKey=UseDynamoDBStorage,ParameterValue=$USE_DYNAMODB_STORAGE"
    PARAMETERS="$PARAMETERS ParameterKey=DynamoDBTableName,ParameterValue=$DYNAMODB_TABLE_NAME"
    PARAMETERS="$PARAMETERS ParameterKey=CreateDeploymentBucket,ParameterValue=$CREATE_DEPLOYMENT_BUCKET"
    
    if [ ! -z "$NOTIFICATION_EMAIL" ]; then
        PARAMETERS="$PARAMETERS ParameterKey=NotificationEmail,ParameterValue=$NOTIFICATION_EMAIL"
    fi
    
    if [ ! -z "$S3_BUCKET_NAME" ]; then
        PARAMETERS="$PARAMETERS ParameterKey=S3BucketName,ParameterValue=$S3_BUCKET_NAME"
    fi
    
    if [ ! -z "$DEPLOYMENT_BUCKET_NAME" ]; then
        PARAMETERS="$PARAMETERS ParameterKey=DeploymentBucketName,ParameterValue=$DEPLOYMENT_BUCKET_NAME"
    fi
    
    if [ ! -z "$VPC_ID" ]; then
        PARAMETERS="$PARAMETERS ParameterKey=VpcId,ParameterValue=$VPC_ID"
    fi
    
    if [ ! -z "$SUBNET_IDS" ]; then
        PARAMETERS="$PARAMETERS ParameterKey=SubnetIds,ParameterValue=\"$SUBNET_IDS\""
    fi
    
    if [ "$SCHEDULE_EXPRESSION" != "rate(1 hour)" ]; then
        PARAMETERS="$PARAMETERS ParameterKey=ScheduleExpression,ParameterValue=\"$SCHEDULE_EXPRESSION\""
    fi
    
    log_verbose "CloudFormation parameters built"
}

# Function to check if stack exists
stack_exists() {
    aws cloudformation describe-stacks --stack-name "$STACK_NAME" &> /dev/null
}

# Function to deploy CloudFormation stack
deploy_cloudformation_stack() {
    log_step "Deploying CloudFormation stack..."
    
    if [ "$DRY_RUN" = "true" ]; then
        log_info "DRY RUN: Would deploy stack with parameters:"
        echo "$PARAMETERS" | tr ' ' '\n' | grep ParameterKey
        return 0
    fi
    
    local stack_operation
    if stack_exists; then
        stack_operation="update-stack"
        log_info "Updating existing stack: $STACK_NAME"
    else
        stack_operation="create-stack"
        log_info "Creating new stack: $STACK_NAME"
    fi
    
    # Deploy stack
    aws cloudformation $stack_operation \
        --stack-name "$STACK_NAME" \
        --template-body file://"$TEMPLATE_FILE" \
        --capabilities CAPABILITY_NAMED_IAM \
        --parameters $PARAMETERS \
        --tags Key=Purpose,Value=ConnectQuotaMonitor Key=Version,Value=2.0 Key=ManagedBy,Value=DeploymentScript
    
    # Wait for completion
    local wait_operation
    if [ "$stack_operation" = "create-stack" ]; then
        wait_operation="stack-create-complete"
    else
        wait_operation="stack-update-complete"
    fi
    
    log_info "Waiting for stack operation to complete..."
    if aws cloudformation wait $wait_operation --stack-name "$STACK_NAME"; then
        log_success "CloudFormation stack operation completed successfully"
    else
        log_error "CloudFormation stack operation failed"
        
        # Get stack events for troubleshooting
        log_info "Recent stack events:"
        aws cloudformation describe-stack-events \
            --stack-name "$STACK_NAME" \
            --query 'StackEvents[0:5].[Timestamp,ResourceStatus,ResourceType,LogicalResourceId,ResourceStatusReason]' \
            --output table
        exit 1
    fi
}

# Function to create and upload deployment package
create_deployment_package() {
    log_step "Creating Lambda deployment package..."
    
    local package_dir="lambda-deployment-package"
    local package_file="lambda-deployment.zip"
    
    # Clean up any existing package
    rm -rf "$package_dir" "$package_file"
    
    # Create package directory
    mkdir -p "$package_dir"
    
    # Copy Python script
    cp "$PYTHON_SCRIPT" "$package_dir/"
    
    # Add any additional files if they exist
    if [ -f "requirements.txt" ]; then
        log_verbose "Found requirements.txt, including in package"
        cp "requirements.txt" "$package_dir/"
    fi
    
    # Create ZIP package
    cd "$package_dir"
    zip -r "../$package_file" . > /dev/null
    cd ..
    
    # Get package size
    local package_size=$(stat -f%z "$package_file" 2>/dev/null || stat -c%s "$package_file" 2>/dev/null)
    local package_size_kb=$((package_size / 1024))
    
    log_success "Deployment package created: $package_file (${package_size_kb} KB)"
    
    # Clean up package directory
    rm -rf "$package_dir"
}

# Function to upload package to S3 and update Lambda
upload_to_s3_and_update_lambda() {
    log_step "Uploading deployment package to S3..."
    
    # Get deployment bucket from stack outputs
    local deployment_bucket=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='DeploymentBucketName'].OutputValue" \
        --output text 2>/dev/null)
    
    if [ -z "$deployment_bucket" ] || [ "$deployment_bucket" = "None" ]; then
        log_error "Deployment bucket not found in stack outputs"
        exit 1
    fi
    
    # Generate unique S3 key
    local timestamp=$(date +%Y%m%d-%H%M%S)
    local s3_key="lambda-deployment-${timestamp}.zip"
    
    # Upload to S3
    if aws s3 cp "lambda-deployment.zip" "s3://$deployment_bucket/$s3_key"; then
        log_success "Package uploaded to S3: s3://$deployment_bucket/$s3_key"
    else
        log_error "Failed to upload package to S3"
        exit 1
    fi
    
    # Update Lambda function code
    local function_name=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='LambdaFunction'].OutputValue" \
        --output text)
    
    if [ -z "$function_name" ]; then
        log_error "Lambda function name not found in stack outputs"
        exit 1
    fi
    
    log_info "Updating Lambda function code: $function_name"
    if aws lambda update-function-code \
        --function-name "$function_name" \
        --s3-bucket "$deployment_bucket" \
        --s3-key "$s3_key" > /dev/null; then
        log_success "Lambda function code updated successfully"
    else
        log_error "Failed to update Lambda function code"
        exit 1
    fi
    
    # Clean up local package
    rm -f "lambda-deployment.zip"
}

# Function to update Lambda function directly (for small packages)
update_lambda_directly() {
    log_step "Updating Lambda function code directly..."
    
    local function_name=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='LambdaFunction'].OutputValue" \
        --output text)
    
    if [ -z "$function_name" ]; then
        log_error "Lambda function name not found in stack outputs"
        exit 1
    fi
    
    log_info "Updating Lambda function: $function_name"
    if aws lambda update-function-code \
        --function-name "$function_name" \
        --zip-file fileb://lambda-deployment.zip > /dev/null; then
        log_success "Lambda function code updated successfully"
    else
        log_error "Failed to update Lambda function code"
        exit 1
    fi
    
    # Clean up package
    rm -f "lambda-deployment.zip"
}

# Function to test Lambda function
test_lambda_function() {
    if [ "$SKIP_TESTS" = "true" ]; then
        log_info "Skipping Lambda function tests"
        return 0
    fi
    
    log_step "Testing Lambda function..."
    
    local function_name=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='LambdaFunction'].OutputValue" \
        --output text)
    
    if [ -z "$function_name" ]; then
        log_warning "Lambda function name not found, skipping test"
        return 0
    fi
    
    # Invoke function
    local response_file="test-response.json"
    if aws lambda invoke \
        --function-name "$function_name" \
        --payload '{}' \
        "$response_file" > /dev/null; then
        
        log_success "Lambda function test completed"
        
        if [ "$VERBOSE" = "true" ]; then
            log_info "Function response:"
            cat "$response_file" | python -m json.tool 2>/dev/null || cat "$response_file"
        fi
        
        rm -f "$response_file"
    else
        log_warning "Lambda function test failed, but deployment continues"
    fi
}

# Function to display deployment summary
display_deployment_summary() {
    log_step "Gathering deployment information..."
    
    echo ""
    echo "======================================================================"
    echo "🎉 ENHANCED CONNECT QUOTA MONITOR DEPLOYMENT COMPLETE"
    echo "======================================================================"
    echo ""
    
    # Stack information
    echo "📋 STACK INFORMATION:"
    echo "   Stack Name: $STACK_NAME"
    echo "   Template: $TEMPLATE_FILE"
    echo "   Deployment Method: $DEPLOYMENT_METHOD"
    echo "   Runtime: $LAMBDA_RUNTIME"
    echo ""
    
    # Configuration
    echo "⚙️  CONFIGURATION:"
    echo "   Threshold: $THRESHOLD_PERCENTAGE%"
    echo "   Schedule: $SCHEDULE_EXPRESSION"
    echo "   Memory: ${LAMBDA_MEMORY} MB"
    echo "   Timeout: ${LAMBDA_TIMEOUT} seconds"
    echo "   S3 Storage: $USE_S3_STORAGE"
    echo "   DynamoDB Storage: $USE_DYNAMODB_STORAGE"
    
    if [ ! -z "$NOTIFICATION_EMAIL" ]; then
        echo "   Email: $NOTIFICATION_EMAIL"
    fi
    
    if [ ! -z "$VPC_ID" ]; then
        echo "   VPC: $VPC_ID"
        echo "   Subnets: $SUBNET_IDS"
    fi
    echo ""
    
    # Get stack outputs
    local function_name=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='LambdaFunction'].OutputValue" \
        --output text 2>/dev/null)
    
    local sns_topic=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='SNSTopicArn'].OutputValue" \
        --output text 2>/dev/null)
    
    local s3_bucket=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
        --output text 2>/dev/null)
    
    local dynamodb_table=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='DynamoDBTableName'].OutputValue" \
        --output text 2>/dev/null)
    
    local deployment_bucket=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='DeploymentBucketName'].OutputValue" \
        --output text 2>/dev/null)
    
    # Resources
    echo "🏗️  DEPLOYED RESOURCES:"
    if [ ! -z "$function_name" ] && [ "$function_name" != "None" ]; then
        echo "   ✅ Lambda Function: $function_name"
    fi
    
    if [ ! -z "$sns_topic" ] && [ "$sns_topic" != "None" ]; then
        echo "   ✅ SNS Topic: $sns_topic"
    fi
    
    if [ ! -z "$s3_bucket" ] && [ "$s3_bucket" != "None" ] && [ "$s3_bucket" != "Not using S3 storage" ]; then
        echo "   ✅ S3 Bucket: $s3_bucket"
    fi
    
    if [ ! -z "$dynamodb_table" ] && [ "$dynamodb_table" != "None" ]; then
        echo "   ✅ DynamoDB Table: $dynamodb_table"
    fi
    
    if [ ! -z "$deployment_bucket" ] && [ "$deployment_bucket" != "None" ]; then
        echo "   ✅ Deployment Bucket: $deployment_bucket"
    fi
    echo ""
    
    # Security features
    echo "🔒 SECURITY FEATURES:"
    echo "   ✅ SNS topic encryption with customer-managed KMS keys"
    echo "   ✅ DynamoDB encryption with customer-managed KMS keys"
    echo "   ✅ Lambda environment variable encryption"
    echo "   ✅ Dead Letter Queue for failed executions"
    echo "   ✅ S3 server-side encryption"
    echo "   ✅ Comprehensive IAM permissions (least privilege)"
    echo "   ✅ Reserved Lambda concurrency limits"
    if [ ! -z "$VPC_ID" ]; then
        echo "   ✅ VPC deployment with security groups"
    fi
    echo ""
    
    # Enhanced capabilities
    echo "🚀 ENHANCED CAPABILITIES:"
    echo "   ✅ 70+ Connect quota monitoring across all services"
    echo "   ✅ Dynamic instance discovery (no hardcoded references)"
    echo "   ✅ Consolidated alerting (one email per instance)"
    echo "   ✅ Flexible storage (S3 and/or DynamoDB)"
    echo "   ✅ Multi-service support (Cases, Profiles, Voice ID, Wisdom, etc.)"
    echo "   ✅ Intelligent deployment with S3 fallback"
    echo "   ✅ Post-deployment configuration flexibility"
    echo ""
    
    # Next steps
    echo "📋 NEXT STEPS:"
    echo "   1. Monitor execution:"
    if [ ! -z "$function_name" ] && [ "$function_name" != "None" ]; then
        echo "      aws logs tail /aws/lambda/$function_name --follow"
    fi
    
    echo "   2. Test function:"
    if [ ! -z "$function_name" ] && [ "$function_name" != "None" ]; then
        echo "      aws lambda invoke --function-name $function_name --payload '{}' test-response.json"
    fi
    
    if [ ! -z "$sns_topic" ] && [ "$sns_topic" != "None" ] && [ -z "$NOTIFICATION_EMAIL" ]; then
        echo "   3. Subscribe to alerts:"
        echo "      aws sns subscribe --topic-arn $sns_topic --protocol email --notification-endpoint your-email@example.com"
    fi
    
    echo "   4. View CloudFormation stack:"
    echo "      aws cloudformation describe-stacks --stack-name $STACK_NAME"
    
    echo ""
    echo "📚 DOCUMENTATION:"
    echo "   - Enhanced monitoring covers all Connect service categories"
    echo "   - Alerts are consolidated by instance for efficient management"
    echo "   - Storage is flexible with support for S3, DynamoDB, or both"
    echo "   - Security compliance maintained with enterprise-grade features"
    echo ""
    echo "🎯 The Enhanced Connect Quota Monitor is now active!"
    echo "======================================================================"
}

# Main execution function
main() {
    echo ""
    echo "======================================================================"
    echo "🚀 $SCRIPT_NAME v$SCRIPT_VERSION"
    echo "======================================================================"
    echo ""
    
    # Parse command line arguments
    parse_arguments "$@"
    
    # Show configuration
    if [ "$VERBOSE" = "true" ] || [ "$DRY_RUN" = "true" ]; then
        echo "📋 DEPLOYMENT CONFIGURATION:"
        echo "   Stack Name: $STACK_NAME"
        echo "   Template: $TEMPLATE_FILE"
        echo "   Python Script: $PYTHON_SCRIPT"
        echo "   Threshold: $THRESHOLD_PERCENTAGE%"
        echo "   Runtime: $LAMBDA_RUNTIME"
        echo "   Memory: ${LAMBDA_MEMORY} MB"
        echo "   Timeout: ${LAMBDA_TIMEOUT} seconds"
        echo "   Schedule: $SCHEDULE_EXPRESSION"
        echo "   S3 Storage: $USE_S3_STORAGE"
        echo "   DynamoDB Storage: $USE_DYNAMODB_STORAGE"
        echo "   Force S3 Deployment: $FORCE_S3_DEPLOYMENT"
        echo "   Skip Tests: $SKIP_TESTS"
        echo "   Verbose: $VERBOSE"
        echo "   Dry Run: $DRY_RUN"
        echo ""
    fi
    
    # Validate prerequisites
    validate_prerequisites
    
    # Detect deployment method
    detect_deployment_method
    
    # Build parameters
    build_parameters
    
    # Deploy CloudFormation stack
    deploy_cloudformation_stack
    
    if [ "$DRY_RUN" = "true" ]; then
        log_info "Dry run completed - no actual deployment performed"
        exit 0
    fi
    
    # Create deployment package
    create_deployment_package
    
    # Deploy Lambda code based on method
    if [ "$DEPLOYMENT_METHOD" = "s3" ]; then
        upload_to_s3_and_update_lambda
    else
        update_lambda_directly
    fi
    
    # Test Lambda function
    test_lambda_function
    
    # Display summary
    display_deployment_summary
}

# Execute main function with all arguments
main "$@"

# This section has been replaced by the enhanced main() function above