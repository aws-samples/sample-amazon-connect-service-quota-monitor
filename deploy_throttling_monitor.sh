#!/bin/bash
# Deploy API Throttling Monitor

set -e

echo "========================================"
echo "Deploying Connect API Throttling Monitor"
echo "========================================"

# Get the SNS topic ARN from the existing quota monitor (used for throttle alerts)
SNS_TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name ConnectQuotaMonitor \
  --query 'Stacks[0].Outputs[?OutputKey==`SNSTopicArn`].OutputValue' \
  --output text 2>/dev/null || echo "")

if [ -z "$SNS_TOPIC_ARN" ]; then
  echo "⚠️  Could not find existing SNS topic. Using existing alerts topic..."
  SNS_TOPIC_ARN="arn:aws:sns:${AWS_DEFAULT_REGION:-us-east-1}:$(aws sts get-caller-identity --query Account --output text):ConnectQuotaAlerts"
fi

echo "Throttle alerts topic: $SNS_TOPIC_ARN"

# Create or find the utilization report topic (separate, clean emails)
UTIL_TOPIC_NAME="ConnectQuotaUtilizationReport"
UTIL_TOPIC_ARN=$(aws sns list-topics --query "Topics[?ends_with(TopicArn, ':${UTIL_TOPIC_NAME}')].TopicArn" --output text 2>/dev/null || echo "")

if [ -z "$UTIL_TOPIC_ARN" ]; then
  echo "Creating utilization report SNS topic..."
  UTIL_TOPIC_ARN=$(aws sns create-topic --name $UTIL_TOPIC_NAME --query 'TopicArn' --output text)
  echo "✅ Created: $UTIL_TOPIC_ARN"
  echo ""
  echo "⚠️  Subscribe your team to this topic for clean utilization reports:"
  echo "   aws sns subscribe --topic-arn $UTIL_TOPIC_ARN --protocol email --notification-endpoint YOUR_EMAIL"
else
  echo "Utilization report topic: $UTIL_TOPIC_ARN"
fi

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
    --environment "Variables={ALERT_SNS_TOPIC_ARN=$SNS_TOPIC_ARN,UTILIZATION_SNS_TOPIC_ARN=$UTIL_TOPIC_ARN}" \
    --timeout 600 \
    --memory-size 512
  
  echo "✅ Function updated"
else
  echo "Creating new function..."
  aws lambda create-function \
    --function-name $FUNCTION_NAME \
    --runtime python3.12 \
    --role $ROLE_ARN \
    --handler api_throttling_monitor.main \
    --zip-file fileb://api-throttling-monitor.zip \
    --environment "Variables={ALERT_SNS_TOPIC_ARN=$SNS_TOPIC_ARN,UTILIZATION_SNS_TOPIC_ARN=$UTIL_TOPIC_ARN}" \
    --timeout 600 \
    --memory-size 512 \
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

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_DEFAULT_REGION:-$(aws configure get region || echo "us-east-1")}

# Add Lambda permission for EventBridge
aws lambda add-permission \
  --function-name $FUNCTION_NAME \
  --statement-id AllowEventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/$RULE_NAME" \
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
