#!/usr/bin/env python3
"""Quick test: invoke the Lambda handler locally."""
import sys
import os
import json

sys.path.insert(0, "live-refresh")
os.environ["CONNECT_INSTANCE_ID"] = "YOUR_INSTANCE_ID"
os.environ["S3_BUCKET"] = ""

from lambda_function import lambda_handler

# Test 1: Fresh collection (no history param)
print("=== Test 1: Fresh collection ===")
result = lambda_handler({"queryStringParameters": {}}, None)
body = json.loads(result["body"])
print(f"Status: {result['statusCode']}")
print(f"Quotas checked: {body['summary']['total_checked']}")
print(f"Highest util: {body['summary']['highest_utilization_pct']}% ({body['summary']['highest_utilization_api']})")
print(f"Concurrent: {body['concurrent']}")
print("Top 3 quotas:")
for q in body["quotas"][:3]:
    print(f"  {q['api']}: {q['current_tps']} TPS / {q['limit_tps']} limit = {q['utilization_pct']}%")

# Test 2: History request (should return error since no S3 bucket)
print("\n=== Test 2: History 1h (no bucket) ===")
result2 = lambda_handler({"queryStringParameters": {"history": "1h"}}, None)
body2 = json.loads(result2["body"])
print(f"Status: {result2['statusCode']}")
print(f"Response: {body2}")

# Test 3: Invalid history
print("\n=== Test 3: Invalid history ===")
os.environ["S3_BUCKET"] = "fake-bucket-for-test"
result3 = lambda_handler({"queryStringParameters": {"history": "invalid"}}, None)
body3 = json.loads(result3["body"])
print(f"Status: {result3['statusCode']}")
print(f"Response: {body3}")
assert result3["statusCode"] == 400, f"Expected 400, got {result3['statusCode']}"
assert "Invalid history value" in body3.get("error", ""), f"Wrong error: {body3}"
os.environ["S3_BUCKET"] = ""

print("\n=== ALL TESTS PASSED ===")
