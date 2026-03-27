#!/usr/bin/env python3
"""
Test script to verify alert mechanism with comprehensive quota data in email
"""
import os
import sys

# Set environment variables for testing
os.environ['THRESHOLD_PERCENTAGE'] = '1'  # Set to 1% to trigger alerts
os.environ['ALERT_SNS_TOPIC_ARN'] = os.environ.get('ALERT_SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789012:test-connect-quota-alerts')

# Import the lambda function
import lambda_function

print("=" * 80)
print("TESTING ALERT MECHANISM WITH 1% THRESHOLD")
print("=" * 80)
print()

# Create a test event
test_event = {}
test_context = type('Context', (), {
    'function_name': 'test-function',
    'request_id': 'test-request-id',
    'invoked_function_arn': 'arn:aws:lambda:us-west-2:123456789012:function:test'
})()

print("Running monitor with 1% threshold (will trigger alerts for any usage > 1%)...")
print()

try:
    result = lambda_function.lambda_handler(test_event, test_context)
    print()
    print("=" * 80)
    print("RESULT:")
    print("=" * 80)
    print(result)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()