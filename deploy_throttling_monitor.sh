#!/bin/bash
# Deploy API Throttling Monitor

set -e

echo "========================================"
echo "Deploying Connect API Throttling Monitor"
echo "========================================"

# Get the SNS topic ARN from the existing quota monitor
SNS_TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name ConnectQuotaMonitor \
  --query 'Stacks[0].Outputs[?OutputKey==`SNSTopicArn`].OutputValue' \
  --output text 2>/dev/null || echo "")

if [ -z "$SNS_TOPIC_ARN" ]; then
  echo "⚠️  Could not find existing SNS topic. Using existing alerts topic..."
  SNS_TOPIC_ARN="arn:aws:sns:us-west-2:745351468190:ConnectQuotaAlerts"
fi

echo "Using SNS Topic: $SNS_TOPIC_ARN"

# Create deployment package
echo ""
echo "Creating deployment package..."
rm -f api-throttling-monitor.zip
zip api-throttling-monitor.zip api_throttling_monitor.py
echo "✅ Package created"

# Create/Update Lambda function
FUNCTION_NAME="ConnectAPIThrottlingMonitor"
ROLE_ARN=$(aws iam list-roles --query "Roles[?RoleName=='ConnectQuotaMonitor-EnhancedLambdaRole'].Arn" --output text)

if [ -z "$ROLE_ARN" ]; then
  echo "❌ Could not find Lambda execution role. Please deploy the quota monitor first."
  exit 1
fi

echo ""
echo "Checking if Lambda function exists..."

if aws lambda get-function --function-name $FUNCTION_NAME 2>/dev/null; then
  echo "Updating existing function..."
  aws lambda update-function-code \
    --function-name $FUNCTION_NAME \
    --zip-file fileb://api-throttling-monitor.zip
  
  aws lambda update-function-configuration \
    --function-name $FUNCTION_NAME \
    --environment "Variables={ALERT_SNS_TOPIC_ARN=$SNS_TOPIC_ARN}" \
    --timeout 300 \
    --memory-size 256
  
  echo "✅ Function updated"
else
  echo "Creating new function..."
  aws lambda create-function \
    --function-name $FUNCTION_NAME \
    --runtime python3.12 \
    --role $ROLE_ARN \
    --handler api_throttling_monitor.main \
    --zip-file fileb://api-throttling-monitor.zip \
    --environment "Variables={ALERT_SNS_TOPIC_ARN=$SNS_TOPIC_ARN}" \
    --timeout 300 \
    --memory-size 256 \
    --description "Monitors Connect API throttling via CloudWatch metrics"
  
  echo "✅ Function created"
fi

# Create EventBridge rule to run every hour
echo ""
echo "Setting up EventBridge schedule..."

RULE_NAME="ConnectAPIThrottlingMonitor-Schedule"

# Check if rule exists
if aws events describe-rule --name $RULE_NAME 2>/dev/null; then
  echo "Rule already exists, updating..."
else
  echo "Creating new rule..."
  aws events put-rule \
    --name $RULE_NAME \
    --description "Runs Connect API throttling monitor every hour" \
    --schedule-expression "rate(1 hour)"
fi

# Add Lambda permission for EventBridge
aws lambda add-permission \
  --function-name $FUNCTION_NAME \
  --statement-id AllowEventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:us-west-2:745351468190:rule/$RULE_NAME" \
  2>/dev/null || echo "Permission already exists"

# Add Lambda as target
FUNCTION_ARN=$(aws lambda get-function --function-name $FUNCTION_NAME --query 'Configuration.FunctionArn' --output text)

aws events put-targets \
  --rule $RULE_NAME \
  --targets "Id"="1","Arn"="$FUNCTION_ARN"

echo "✅ Schedule configured"

# Test the function
echo ""
echo "Testing the function..."
aws lambda invoke \
  --function-name $FUNCTION_NAME \
  --payload '{"hours": 1}' \
  /tmp/throttling-test-response.json

echo ""
echo "Test response:"
cat /tmp/throttling-test-response.json | python3 -m json.tool 2>/dev/null || cat /tmp/throttling-test-response.json

echo ""
echo "========================================"
echo "✅ Deployment Complete!"
echo "========================================"
echo ""
echo "Function: $FUNCTION_NAME"
echo "Schedule: Every 1 hour"
echo "SNS Topic: $SNS_TOPIC_ARN"
echo ""
echo "To view logs:"
echo "  aws logs tail /aws/lambda/$FUNCTION_NAME --follow"
echo ""
echo "To test manually:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --payload '{\"hours\": 24}' response.json"
echo ""
