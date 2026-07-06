#!/usr/bin/env python3
"""
Test script for CloudWatch concurrent metrics dimension fix.

This script validates that the quota monitor correctly queries CloudWatch
metrics with the MetricGroup dimension for concurrent calls/chats/tasks.

Usage:
    # Test against your Isengard account (requires active Connect instance)
    python3 test_cloudwatch_fix.py --instance-id <YOUR_CONNECT_INSTANCE_ID> --region us-east-1

    # Test with dummy CloudWatch data (puts test metrics then queries them)
    python3 test_cloudwatch_fix.py --instance-id <YOUR_CONNECT_INSTANCE_ID> --region us-east-1 --inject-dummy

    # Test against Allstate prod (read-only, just validates metric exists)
    python3 test_cloudwatch_fix.py --instance-id 587c546e-2328-4c36-baa2-37eaf4749631 --region us-east-1 --read-only
"""

import boto3
import argparse
from datetime import datetime, timedelta
import json
import sys


def test_list_metrics(cw_client, instance_id):
    """Check what metrics actually exist for this instance."""
    print("\n" + "=" * 60)
    print("STEP 1: Listing available metrics for instance")
    print("=" * 60)

    response = cw_client.list_metrics(
        Namespace='AWS/Connect',
        Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}]
    )

    # Find concurrent/calls related metrics
    concurrent_metrics = [
        m for m in response['Metrics']
        if any(kw in m['MetricName'] for kw in ['Concurrent', 'Calls', 'Chats', 'Tasks', 'CallsPerInterval'])
    ]

    if concurrent_metrics:
        print(f"\nFound {len(concurrent_metrics)} concurrent/call metrics:")
        for m in concurrent_metrics:
            dims = {d['Name']: d['Value'] for d in m['Dimensions']}
            print(f"  {m['MetricName']} | Dimensions: {dims}")
    else:
        print("\n  NO concurrent metrics found for this instance.")
        print("  This means the instance has no active calls/chats/tasks right now,")
        print("  OR CloudWatch metrics are not enabled.")

    return concurrent_metrics


def test_old_query(cw_client, instance_id, metric_name):
    """Test the OLD query (InstanceId only, no MetricGroup) - should return empty."""
    print(f"\n{'=' * 60}")
    print(f"STEP 2: OLD query (BUG) - {metric_name} with InstanceId only")
    print("=" * 60)

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=15)

    response = cw_client.get_metric_statistics(
        Namespace='AWS/Connect',
        MetricName=metric_name,
        Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
        StartTime=start_time,
        EndTime=end_time,
        Period=300,
        Statistics=['Maximum']
    )

    if response['Datapoints']:
        print(f"  Result: {response['Datapoints'][0].get('Maximum', 0)}")
        print("  OLD query WORKS for this instance (smaller instance?)")
        return True
    else:
        print("  Result: NO DATA (returns 0)")
        print("  This confirms the bug - InstanceId-only dimension returns nothing.")
        return False


def test_new_query(cw_client, instance_id, metric_name, metric_group):
    """Test the NEW query (InstanceId + MetricGroup) - should return data."""
    print(f"\n{'=' * 60}")
    print(f"STEP 3: NEW query (FIX) - {metric_name} with MetricGroup={metric_group}")
    print("=" * 60)

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=15)

    response = cw_client.get_metric_statistics(
        Namespace='AWS/Connect',
        MetricName=metric_name,
        Dimensions=[
            {'Name': 'InstanceId', 'Value': instance_id},
            {'Name': 'MetricGroup', 'Value': metric_group}
        ],
        StartTime=start_time,
        EndTime=end_time,
        Period=300,
        Statistics=['Maximum']
    )

    if response['Datapoints']:
        value = response['Datapoints'][0].get('Maximum', 0)
        print(f"  Result: {value}")
        print(f"  FIX WORKS - got actual data with MetricGroup dimension.")
        return True
    else:
        print("  Result: NO DATA")
        print("  Metric may not have data in the last 15 minutes (no active contacts).")
        print("  Use --inject-dummy to put test data, or run during business hours.")
        return False


def inject_dummy_metrics(cw_client, instance_id):
    """Put dummy CloudWatch metric data to simulate active Connect instance."""
    print(f"\n{'=' * 60}")
    print("INJECTING DUMMY METRICS for testing")
    print("=" * 60)

    now = datetime.utcnow()
    metrics_to_inject = [
        ('ConcurrentCalls', 'VoiceCalls', 150.0),
        ('ConcurrentActiveChats', 'Chats', 45.0),
        ('ConcurrentActiveTasks', 'Tasks', 12.0),
    ]

    for metric_name, metric_group, value in metrics_to_inject:
        cw_client.put_metric_data(
            Namespace='AWS/Connect',
            MetricData=[{
                'MetricName': metric_name,
                'Dimensions': [
                    {'Name': 'InstanceId', 'Value': instance_id},
                    {'Name': 'MetricGroup', 'Value': metric_group}
                ],
                'Timestamp': now,
                'Value': value,
                'Unit': 'Count',
                'StorageResolution': 60
            }]
        )
        print(f"  Injected: {metric_name} = {value} (MetricGroup={metric_group})")

    print("\n  Waiting 5 seconds for metrics to propagate...")
    import time
    time.sleep(5)
    print("  Done. Metrics should now be queryable.")


def run_tests(instance_id, region, inject_dummy=False, read_only=False):
    """Run the complete test suite."""
    print(f"\nConnect Quota Monitor - CloudWatch Dimension Fix Test")
    print(f"Instance: {instance_id}")
    print(f"Region: {region}")
    print(f"Mode: {'read-only' if read_only else 'inject-dummy' if inject_dummy else 'standard'}")
    print("=" * 60)

    session = boto3.Session(region_name=region)
    cw_client = session.client('cloudwatch')

    # Step 1: List what exists
    existing_metrics = test_list_metrics(cw_client, instance_id)

    # Inject dummy data if requested
    if inject_dummy and not read_only:
        inject_dummy_metrics(cw_client, instance_id)

    # Step 2 & 3: Test old vs new queries
    test_cases = [
        ('ConcurrentCalls', 'VoiceCalls'),
        ('ConcurrentActiveChats', 'Chats'),
        ('ConcurrentActiveTasks', 'Tasks'),
    ]

    results = []
    for metric_name, metric_group in test_cases:
        old_works = test_old_query(cw_client, instance_id, metric_name)
        new_works = test_new_query(cw_client, instance_id, metric_name, metric_group)
        results.append({
            'metric': metric_name,
            'group': metric_group,
            'old_query_works': old_works,
            'new_query_works': new_works
        })

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print("=" * 60)
    print(f"\n{'Metric':<30} {'Old (bug)':<15} {'New (fix)':<15} {'Verdict'}")
    print("-" * 75)
    for r in results:
        old = "DATA" if r['old_query_works'] else "EMPTY (0)"
        new = "DATA" if r['new_query_works'] else "EMPTY (0)"
        if r['new_query_works'] and not r['old_query_works']:
            verdict = "FIX NEEDED and WORKS"
        elif r['new_query_works'] and r['old_query_works']:
            verdict = "Both work (small instance)"
        elif not r['new_query_works'] and not r['old_query_works']:
            verdict = "No data (inject or wait for traffic)"
        else:
            verdict = "UNEXPECTED"
        print(f"  {r['metric']:<28} {old:<15} {new:<15} {verdict}")

    print(f"\n{'=' * 60}")
    if any(r['new_query_works'] and not r['old_query_works'] for r in results):
        print("CONCLUSION: Fix validated. MetricGroup dimension is required.")
        print("The old code returns 0, the new code returns actual values.")
        return 0
    elif any(r['new_query_works'] for r in results):
        print("CONCLUSION: Fix works. Both queries return data on this instance.")
        return 0
    else:
        print("CONCLUSION: No metric data available. Either:")
        print("  - No active contacts right now (run during business hours)")
        print("  - Use --inject-dummy to put test data")
        print("  - Instance doesn't have CloudWatch metrics enabled")
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Test CloudWatch concurrent metrics dimension fix')
    parser.add_argument('--instance-id', required=True, help='Connect instance ID')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    parser.add_argument('--inject-dummy', action='store_true', help='Inject dummy metric data for testing')
    parser.add_argument('--read-only', action='store_true', help='Read-only mode (no writes)')
    parser.add_argument('--profile', default=None, help='AWS profile name')

    args = parser.parse_args()

    if args.profile:
        boto3.setup_default_session(profile_name=args.profile)

    sys.exit(run_tests(args.instance_id, args.region, args.inject_dummy, args.read_only))
