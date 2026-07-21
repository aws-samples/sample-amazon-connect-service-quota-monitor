#!/usr/bin/env python3
"""Test: verify the full API report HTML generation (same as S3-hosted version)."""
import sys
import os
sys.path.insert(0, ".")

from consolidated_report import generate_consolidated_report_string
import importlib
mapper = importlib.import_module("connect-resource-mapper")

instance_id = "YOUR_INSTANCE_ID"
region = "us-east-1"

print("Collecting all resources (full mapper run)...")
resource_map, model = mapper.collect_all(instance_id, region, profile="YOUR_AWS_PROFILE")
print(f"  Phone numbers: {len(resource_map.get('phone_numbers', []))}")
print(f"  Contact flows: {len(resource_map.get('contact_flows', []))}")
print(f"  Lambdas: {len(resource_map.get('lambda_functions', []))}")

print("Generating API report HTML...")
html = generate_consolidated_report_string(resource_map, model)
print(f"  HTML size: {len(html)} chars")

assert "<!DOCTYPE html>" in html, "Missing DOCTYPE"
assert "Connect API Consolidated Report" in html, "Missing title"
assert "tab-apis" in html, "Missing APIs tab"
assert "tab-flows" in html, "Missing Flows tab"
assert "tab-quotas" in html, "Missing Quotas tab"
assert "tab-lambdas" in html, "Missing Lambdas tab"
assert "sortTable" in html, "Missing sort function"
assert "filterTable" in html, "Missing filter function"
assert len(html) > 50000, f"HTML too small: {len(html)} (expected full report)"

out_path = os.path.expanduser("~/Downloads/test-api-report-s3.html")
with open(out_path, "w") as f:
    f.write(html)
print(f"  Saved to: {out_path}")
print(f"\nALL TESTS PASSED")
print(f"This is the exact HTML that S3 will serve at your bookmark URL.")
print(f"Open it: open {out_path}")
