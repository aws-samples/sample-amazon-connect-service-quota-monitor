#!/usr/bin/env python3
"""Generate synthetic API load on a Connect instance.

Calls each monitored API at least once to generate CloudWatch Usage metrics.
This ensures the dashboard has data points to display.

Usage:
    python generate_synthetic_load.py --profile YOUR_AWS_PROFILE --region us-east-1
"""

import argparse
import time
import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic Connect API load")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--instance-id", default="YOUR_INSTANCE_ID")
    parser.add_argument("--rounds", type=int, default=3, help="Number of rounds to call each API")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    connect = session.client("connect")
    instance_id = args.instance_id
    instance_arn = f"arn:aws:connect:{args.region}:YOUR_ACCOUNT_ID:instance/{instance_id}"

    print(f"Generating synthetic load on instance {instance_id}")
    print(f"Rounds: {args.rounds}")
    print("-" * 60)

    results = {}

    for round_num in range(1, args.rounds + 1):
        print(f"\n--- Round {round_num}/{args.rounds} ---")

        # 1. GetContactAttributes — needs a contact ID (will fail gracefully)
        results["GetContactAttributes"] = _call(
            connect.get_contact_attributes,
            InstanceId=instance_id,
            InitialContactId="00000000-0000-0000-0000-000000000000"
        )

        # 2. DescribeContact — needs a contact ID
        results["DescribeContact"] = _call(
            connect.describe_contact,
            InstanceId=instance_id,
            ContactId="00000000-0000-0000-0000-000000000000"
        )

        # 3. GetCurrentMetricData — needs at least one queue
        queues_resp = connect.list_queues(InstanceId=instance_id, MaxResults=1)
        queue_ids = [q["Id"] for q in queues_resp.get("QueueSummaryList", [])]
        if queue_ids:
            results["GetCurrentMetricData"] = _call(
                connect.get_current_metric_data,
                InstanceId=instance_id,
                Filters={"Queues": queue_ids, "Channels": ["VOICE"]},
                CurrentMetrics=[{"Name": "AGENTS_ONLINE", "Unit": "COUNT"}]
            )
        else:
            results["GetCurrentMetricData"] = "SKIP (no queues)"
            print("  SKIP GetCurrentMetricData (no queues found)")

        # 4. GetMetricDataV2
        results["GetMetricDataV2"] = _call(
            connect.get_metric_data_v2,
            ResourceArn=instance_arn,
            StartTime="2026-07-10T00:00:00Z",
            EndTime="2026-07-10T23:59:59Z",
            Filters=[{"FilterKey": "CHANNEL", "FilterValues": ["VOICE"]}],
            Metrics=[{"Name": "CONTACTS_HANDLED", "MetricFilters": []}]
        )

        # 5. SearchContacts
        results["SearchContacts"] = _call(
            connect.search_contacts,
            InstanceId=instance_id,
            TimeRange={"Type": "INITIATION_TIMESTAMP", "StartTime": "2026-07-09T00:00:00Z", "EndTime": "2026-07-10T23:59:59Z"},
            MaxResults=1
        )

        # 6. ListContactFlows
        results["ListContactFlows"] = _call(
            connect.list_contact_flows,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 7. ListQueues
        results["ListQueues"] = _call(
            connect.list_queues,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 8. ListRoutingProfiles
        results["ListRoutingProfiles"] = _call(
            connect.list_routing_profiles,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 9. ListUsers
        results["ListUsers"] = _call(
            connect.list_users,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 10. ListPhoneNumbersV2
        results["ListPhoneNumbersV2"] = _call(
            connect.list_phone_numbers_v2,
            MaxResults=10
        )

        # 11. ListLambdaFunctions
        results["ListLambdaFunctions"] = _call(
            connect.list_lambda_functions,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 12. DescribeContactFlow (get first flow ID from ListContactFlows)
        flows_resp = connect.list_contact_flows(InstanceId=instance_id, MaxResults=1)
        if flows_resp.get("ContactFlowSummaryList"):
            flow_id = flows_resp["ContactFlowSummaryList"][0]["Id"]
            results["DescribeContactFlow"] = _call(
                connect.describe_contact_flow,
                InstanceId=instance_id,
                ContactFlowId=flow_id
            )

        # 13. ListTrafficDistributionGroups
        results["ListTrafficDistributionGroups"] = _call(
            connect.list_traffic_distribution_groups,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 14. TagContact — needs real contact, will fail gracefully
        results["TagContact"] = _call(
            connect.tag_contact,
            ContactId="00000000-0000-0000-0000-000000000000",
            InstanceId=instance_id,
            Tags={"synthetic": "load-test"}
        )

        # 15. ListAgentStatuses
        results["ListAgentStatuses"] = _call(
            connect.list_agent_statuses,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 16. ListHoursOfOperations
        results["ListHoursOfOperations"] = _call(
            connect.list_hours_of_operations,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 17. ListQuickConnects
        results["ListQuickConnects"] = _call(
            connect.list_quick_connects,
            InstanceId=instance_id,
            MaxResults=10
        )

        # 18. DescribeInstance
        results["DescribeInstance"] = _call(
            connect.describe_instance,
            InstanceId=instance_id
        )

        # Small delay between rounds to spread across time
        if round_num < args.rounds:
            time.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("LOAD GENERATION SUMMARY")
    print("=" * 60)
    success = sum(1 for v in results.values() if v == "OK")
    failed = sum(1 for v in results.values() if v != "OK")
    print(f"APIs called: {len(results)}")
    print(f"Succeeded:   {success}")
    print(f"Expected failures (no real contact): {failed}")
    print(f"Rounds:      {args.rounds}")
    print(f"Total calls: ~{len(results) * args.rounds}")
    print("\nCloudWatch Usage metrics should appear within 5-10 minutes.")
    print("Run the dashboard generator after that to see real data.")


def _call(func, **kwargs):
    """Call an API and return OK/error status."""
    name = func.__name__
    try:
        func(**kwargs)
        print(f"  OK  {name}")
        return "OK"
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # ResourceNotFoundException and InvalidParameterException are expected
        # for synthetic contact IDs — the API was still called and metered
        if code in ("ResourceNotFoundException", "InvalidParameterException",
                    "ContactNotFoundException", "InvalidContactFlowException"):
            print(f"  OK  {name} (expected: {code})")
            return "OK"
        else:
            print(f"  ERR {name}: {code} — {e.response['Error']['Message'][:60]}")
            return code
    except Exception as e:
        print(f"  ERR {name}: {str(e)[:60]}")
        return str(e)[:20]


if __name__ == "__main__":
    main()
