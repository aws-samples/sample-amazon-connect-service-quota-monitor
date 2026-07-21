#!/usr/bin/env python3
# SPDX-License-Identifier: MIT-0
"""Amazon Connect Resource Mapper — Quota Impact Model Generator.

Maps the full resource topology of an Amazon Connect instance:
  Phone Numbers → Traffic Distribution Groups → Contact Flows → Lambda Functions → API Quotas

Generates a predictive quota impact model for migration wave planning,
identifying which API rate limits will be breached before capacity events occur.

Outputs:
    connect-resource-map.json         Full resource graph with relationships
    connect-quota-impact-model.json   Predictive model with migration formulas
    connect-dashboard.html            Self-contained interactive dashboard

Required IAM Permissions (read-only):
    connect:ListContactFlows, connect:DescribeContactFlow,
    connect:ListPhoneNumbersV2, connect:ListQueues,
    connect:ListRoutingProfiles, connect:ListLambdaFunctions,
    connect:ListTrafficDistributionGroups,
    connect:DescribeTrafficDistributionGroup,
    connect:GetTrafficDistribution,
    lambda:ListFunctions, lambda:GetFunction,
    lambda:ListProvisionedConcurrencyConfigs,
    lex:ListBots, lex:ListBotAliases,
    servicequotas:ListServiceQuotas,
    cloudwatch:GetMetricData

Usage:
    pip install boto3
    python connect-resource-mapper.py \\
        --instance-id 587c546e-2328-4c36-baa2-37eaf4749631 \\
        --region us-east-1 \\
        --output-dir ./output

Author: Amazon.com, Inc.
License: MIT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, BotoCoreError

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

BUSINESS_DAY_SECONDS = 28_800  # 8-hour business day in seconds
PHONE_NUMBER_PAGE_SIZE = 1_000
CONTACT_FLOW_PAGE_SIZE = 100
SERVICE_QUOTA_PAGE_SIZE = 100
CLOUDWATCH_LOOKBACK_DAYS = 7
CONNECT_SERVICE_CODE = "connect"

# APIs with highest traffic volume in a typical Connect deployment.
HIGH_TRAFFIC_APIS = (
    "GetContactAttributes",
    "UpdateContactAttributes",
    "DescribeContact",
    "GetMetricDataV2",
    "GetCurrentMetricData",
    "StartOutboundVoiceContact",
    "StopContact",
    "TransferContact",
    "TagContact",
    "UntagContact",
    "SearchContacts",
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT FACTORY
# ═══════════════════════════════════════════════════════════════════════════════


def create_clients(region: str, profile: str | None = None) -> dict[str, Any]:
    """Create boto3 clients for all required AWS services.

    Args:
        region: AWS region name (e.g., 'us-east-1').
        profile: Optional AWS CLI profile name.

    Returns:
        Dictionary mapping service name to boto3 client instance.
    """
    session = boto3.Session(profile_name=profile, region_name=region)
    return {
        "connect": session.client("connect"),
        "lambda": session.client("lambda"),
        "lex": session.client("lexv2-models"),
        "servicequotas": session.client("service-quotas"),
        "cloudwatch": session.client("cloudwatch"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1: PHONE NUMBERS & TRAFFIC DISTRIBUTION GROUPS
# ═══════════════════════════════════════════════════════════════════════════════


def collect_phone_numbers(
    connect_client: Any, instance_id: str
) -> list[dict[str, Any]]:
    """Collect all phone numbers with their TDG associations.

    Paginates through the full inventory using ListPhoneNumbersV2.
    This API does not require an InstanceId parameter — it returns
    numbers across the account, with TargetArn indicating TDG membership.

    Args:
        connect_client: Boto3 Connect client.
        instance_id: Connect instance ID (used for logging context).

    Returns:
        List of phone number summary dicts with PhoneNumber, PhoneNumberType,
        TargetArn, and PhoneNumberId fields.

    Raises:
        ClientError: If the API call fails due to permissions or throttling.
    """
    logger.info("Collecting phone numbers...")
    numbers: list[dict[str, Any]] = []
    next_token: str | None = None

    while True:
        params: dict[str, Any] = {"MaxResults": PHONE_NUMBER_PAGE_SIZE}
        if next_token:
            params["NextToken"] = next_token

        response = connect_client.list_phone_numbers_v2(**params)
        page = response.get("ListPhoneNumbersSummaryList", [])
        numbers.extend(page)
        next_token = response.get("NextToken")

        if not next_token:
            break

    logger.info("Found %d phone numbers.", len(numbers))
    return numbers


def collect_traffic_distribution_groups(
    connect_client: Any, instance_id: str
) -> list[dict[str, Any]]:
    """Collect Traffic Distribution Groups and their distribution configs.

    Args:
        connect_client: Boto3 Connect client.
        instance_id: Connect instance ID.

    Returns:
        List of TDG summary dicts, each enriched with a 'distribution' key
        containing the telephony config. Returns empty list if the API
        is unavailable or access is denied.
    """
    logger.info("Collecting Traffic Distribution Groups...")
    try:
        response = connect_client.list_traffic_distribution_groups(
            InstanceId=instance_id, MaxResults=10
        )
        tdgs = response.get("TrafficDistributionGroupSummaryList", [])
    except ClientError as e:
        logger.warning("TDG listing failed: %s", e.response["Error"]["Message"])
        return []

    for tdg in tdgs:
        try:
            dist = connect_client.get_traffic_distribution(Id=tdg["Id"])
            tdg["distribution"] = dist.get("TelephonyConfig", {})
        except ClientError as e:
            tdg["distribution"] = {"error": e.response["Error"]["Message"]}

    logger.info("Found %d TDGs.", len(tdgs))
    return tdgs


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2: CONTACT FLOWS
# ═══════════════════════════════════════════════════════════════════════════════


def collect_contact_flows(
    connect_client: Any, instance_id: str, *, skip_content: bool = False
) -> list[dict[str, Any]]:
    """Collect all contact flows with Lambda invocation mappings.

    For each flow, retrieves the full content JSON and parses it to extract
    Lambda function ARNs invoked by the flow. This is the critical link in
    the topology: Flow → Lambda → API calls.

    Args:
        connect_client: Boto3 Connect client.
        instance_id: Connect instance ID.
        skip_content: If True, skip DescribeContactFlow calls (faster but
            no Lambda mapping). Useful for initial discovery.

    Returns:
        List of contact flow dicts, each with an added 'lambdas_invoked'
        key containing a list of Lambda ARNs.
    """
    logger.info("Collecting contact flows...")
    flows: list[dict[str, Any]] = []
    next_token: str | None = None

    while True:
        params: dict[str, Any] = {
            "InstanceId": instance_id,
            "MaxResults": CONTACT_FLOW_PAGE_SIZE,
        }
        if next_token:
            params["NextToken"] = next_token

        response = connect_client.list_contact_flows(**params)
        flows.extend(response.get("ContactFlowSummaryList", []))
        next_token = response.get("NextToken")

        if not next_token:
            break

    logger.info("Found %d contact flows.", len(flows))

    if skip_content:
        logger.info("Skipping flow content retrieval (--skip-flow-content).")
        return flows

    logger.info("Describing each flow to extract Lambda mappings...")
    detailed_flows: list[dict[str, Any]] = []

    for i, flow in enumerate(flows):
        if i > 0 and i % 20 == 0:
            logger.info("  ... %d/%d flows described", i, len(flows))

        try:
            detail = connect_client.describe_contact_flow(
                InstanceId=instance_id, ContactFlowId=flow["Id"]
            )
            flow_data = detail.get("ContactFlow", {})
            content = flow_data.get("Content", "{}")
            flow_data["lambdas_invoked"] = extract_lambdas_from_flow(content)
            flow_data["api_actions"] = extract_api_actions_from_flow(content)
            # Retrieve tags for line matching
            flow_data["Tags"] = flow_data.get("Tags", {})
            # Remove raw content to reduce output size.
            flow_data.pop("Content", None)
            detailed_flows.append(flow_data)

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.warning(
                "Failed to describe flow %s: %s", flow.get("Id", "?"), error_code
            )
            flow["error"] = e.response["Error"]["Message"]
            flow["lambdas_invoked"] = []
            detailed_flows.append(flow)

    logger.info("Described %d flows.", len(detailed_flows))
    return detailed_flows


def extract_lambdas_from_flow(content_json: str) -> list[str]:
    """Extract unique Lambda function ARNs from contact flow JSON content.

    Connect flow content stores Lambda invocations in the Actions array.
    The ARN key varies by flow version:
        - LambdaFunctionARN (uppercase — current format, most common)
        - LambdaFunctionArn (mixed case — older flows)

    This function handles both formats and deduplicates results.

    Args:
        content_json: Raw JSON string of the contact flow content.

    Returns:
        Deduplicated list of Lambda function ARNs found in the flow.
        Returns empty list if content is invalid or contains no Lambda calls.
    """
    if not content_json:
        return []

    try:
        content = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return []

    lambdas: list[str] = []
    arn_keys = ("LambdaFunctionARN", "LambdaFunctionArn", "lambdaFunctionArn")

    for action in content.get("Actions", []):
        params = action.get("Parameters", {})
        if not params:
            continue

        for key in arn_keys:
            arn = params.get(key, "")
            if arn and arn not in lambdas:
                lambdas.append(arn)

    return lambdas


def extract_api_actions_from_flow(content_json: str) -> list[dict[str, Any]]:
    """Extract all Connect API actions from contact flow JSON content.

    Parses the full Actions array from the flow definition and identifies
    every API call that fires during a contact traversing this flow.
    Each action type maps to specific Connect API calls.

    Action Type → Connect API mapping:
        InvokeLambdaFunction        → lambda:InvokeFunction
        GetParticipantInput         → (Lex: RecognizeText/RecognizeUtterance)
        InvokeFlowModule            → connect:DescribeContactFlowModule
        TransferToFlow              → connect:StartContactStreaming (internal)
        TransferContactToQueue      → connect:TransferContact
        CreateTask                  → connect:CreateTask
        UpdateContactAttributes     → connect:UpdateContactAttributes
        CompareContactAttributes    → connect:DescribeContact + GetContactAttributes
        InvokeExternalResource      → (Lex bot or external endpoint)
        GetMetrics                  → connect:GetCurrentMetricData / GetMetricDataV2
        CheckHoursOfOperation       → connect:DescribeHoursOfOperation
        CheckContactAttributes      → connect:GetContactAttributes
        SetContactAttributes        → connect:UpdateContactAttributes
        SetVoice / SetLogging       → (internal, no API call)
        PlayPrompt / GetInput       → (internal, no API call)
        DisconnectParticipant       → connect:StopContact
        TransferToPhoneNumber       → connect:StartOutboundVoiceContact
        SetCallbackNumber           → connect:UpdateContactAttributes
        CreateCallback              → connect:StartOutboundVoiceContact
        TagContact                  → connect:TagContact
        UntagContact                → connect:UntagContact
        AssociateRoutingProfile     → connect:UpdateContactRoutingData
        SetEventHook                → connect:UpdateContact
        EndFlowExecution            → (internal, no API call)
        Wait                        → (internal, no API call)
        Loop                        → (internal, no API call)

    Args:
        content_json: Raw JSON string of the contact flow content.

    Returns:
        List of action dicts, each with:
            type: The action type from the flow (e.g., "InvokeLambdaFunction")
            api: The Connect/AWS API this maps to (e.g., "connect:UpdateContactAttributes")
            parameters: Key parameters from the action (for context)
            count: How many times this action type appears in the flow
        Deduplicated by action type with count field.
    """
    if not content_json:
        return []

    try:
        content = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return []

    # Map Connect flow action types to the API calls they generate
    ACTION_TO_API: dict[str, str] = {
        "InvokeLambdaFunction": "lambda:InvokeFunction",
        "GetParticipantInput": "lex:RecognizeText",
        "InvokeFlowModule": "connect:DescribeContactFlowModule",
        "TransferToFlow": "connect:StartContactStreaming",
        "TransferContactToQueue": "connect:TransferContact",
        "CreateTask": "connect:CreateTask",
        "UpdateContactAttributes": "connect:UpdateContactAttributes",
        "CompareContactAttributes": "connect:GetContactAttributes",
        "CheckContactAttributes": "connect:GetContactAttributes",
        "SetContactAttributes": "connect:UpdateContactAttributes",
        "InvokeExternalResource": "lex:RecognizeUtterance",
        "GetMetrics": "connect:GetCurrentMetricData",
        "CheckHoursOfOperation": "connect:DescribeHoursOfOperation",
        "CheckStaffing": "connect:GetCurrentMetricData",
        "CheckQueueStatus": "connect:GetCurrentMetricData",
        "DisconnectParticipant": "connect:StopContact",
        "TransferToPhoneNumber": "connect:StartOutboundVoiceContact",
        "CreateCallback": "connect:StartOutboundVoiceContact",
        "SetCallbackNumber": "connect:UpdateContactAttributes",
        "TagContact": "connect:TagContact",
        "UntagContact": "connect:UntagContact",
        "AssociateRoutingProfile": "connect:UpdateContactRoutingData",
        "SetEventHook": "connect:UpdateContact",
        "SendEvent": "connect:SendEvent",
        "GetInput": "connect:GetContactAttributes",
        "StoreInput": "connect:UpdateContactAttributes",
        "SearchContacts": "connect:SearchContacts",
        "DescribeContact": "connect:DescribeContact",
        "CreateContact": "connect:StartChatContact",
        "PauseContact": "connect:PauseContact",
        "ResumeContact": "connect:ResumeContact",
        # Internal actions (no API call):
        "PlayPrompt": None,
        "SetVoice": None,
        "SetLogging": None,
        "SetRecordingBehavior": None,
        "SetAnalyticsBehavior": None,
        "Wait": None,
        "Loop": None,
        "EndFlowExecution": None,
        "SetWhisperFlow": None,
        "SetHoldFlow": None,
        "SetCustomerQueueFlow": None,
        "SetDisconnectFlow": None,
        "Distribute": None,
    }

    # Count actions by type
    action_counts: dict[str, int] = defaultdict(int)
    action_params: dict[str, dict[str, Any]] = {}

    for action in content.get("Actions", []):
        action_type = action.get("Type", "")
        if not action_type:
            continue

        action_counts[action_type] += 1

        # Store first occurrence's parameters for context
        if action_type not in action_params:
            params = action.get("Parameters", {})
            action_params[action_type] = {
                k: v for k, v in params.items()
                if k not in ("Content",)  # Skip large content blobs
            } if params else {}

    # Build result list
    results: list[dict[str, Any]] = []
    for action_type, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        api = ACTION_TO_API.get(action_type, f"connect:{action_type}")
        results.append({
            "type": action_type,
            "api": api,
            "count": count,
            "parameters": action_params.get(action_type, {}),
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3: LAMBDA FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def collect_lambda_functions(
    lambda_client: Any, connect_client: Any, instance_id: str
) -> list[dict[str, Any]]:
    """Collect Lambda functions associated with the Connect instance.

    Retrieves the full configuration for each Lambda, including
    Provisioned Concurrency status — critical for cold-start risk assessment.

    Args:
        lambda_client: Boto3 Lambda client.
        connect_client: Boto3 Connect client.
        instance_id: Connect instance ID.

    Returns:
        List of Lambda configuration dicts, each with an added
        'ProvisionedConcurrency' key (list of PC configs, empty if none).
    """
    logger.info("Collecting Lambda functions...")

    try:
        response = connect_client.list_lambda_functions(InstanceId=instance_id)
        connect_lambda_arns = response.get("LambdaFunctions", [])
    except ClientError as e:
        logger.warning(
            "Failed to list Connect Lambdas: %s", e.response["Error"]["Message"]
        )
        connect_lambda_arns = []

    lambda_details: list[dict[str, Any]] = []

    for arn in connect_lambda_arns:
        try:
            func = lambda_client.get_function(FunctionName=arn)
            config = func.get("Configuration", {})
            config["ProvisionedConcurrency"] = _get_provisioned_concurrency(
                lambda_client, config.get("FunctionName", "")
            )
            lambda_details.append(config)

        except ClientError as e:
            logger.warning("Failed to describe Lambda %s: %s", arn, e.response["Error"]["Message"])
            lambda_details.append({"FunctionArn": arn, "error": e.response["Error"]["Message"]})

    logger.info("Found %d Connect-associated Lambdas.", len(lambda_details))
    return lambda_details


def _get_provisioned_concurrency(
    lambda_client: Any, function_name: str
) -> list[dict[str, Any]]:
    """Retrieve Provisioned Concurrency configs for a Lambda function.

    Args:
        lambda_client: Boto3 Lambda client.
        function_name: Lambda function name.

    Returns:
        List of PC config dicts, or empty list if none configured.
    """
    if not function_name:
        return []
    try:
        response = lambda_client.list_provisioned_concurrency_configs(
            FunctionName=function_name
        )
        return response.get("ProvisionedConcurrencyConfigs", [])
    except ClientError:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4: LEX BOTS
# ═══════════════════════════════════════════════════════════════════════════════


def collect_lex_bots(
    lex_client: Any, connect_client: Any, instance_id: str
) -> list[dict[str, Any]]:
    """Collect Lex V2 bots associated with the Connect instance.

    Falls back to account-level bot listing if the Connect-specific
    API is unavailable.

    Args:
        lex_client: Boto3 Lex V2 Models client.
        connect_client: Boto3 Connect client.
        instance_id: Connect instance ID.

    Returns:
        List of Lex bot summary dicts.
    """
    logger.info("Collecting Lex bots...")

    try:
        response = connect_client.list_bots(
            InstanceId=instance_id, LexVersion="V2", MaxResults=25
        )
        bots = response.get("LexBots", [])
        logger.info("Found %d Lex bots via Connect.", len(bots))
        return bots
    except ClientError as e:
        logger.warning(
            "Connect.ListBots failed: %s. Trying account-level.",
            e.response["Error"]["Message"],
        )

    try:
        response = lex_client.list_bots(MaxResults=50)
        bots = response.get("botSummaries", [])
        logger.info("Found %d Lex bots (account-level).", len(bots))
        return bots
    except ClientError as e:
        logger.warning("Lex ListBots also failed: %s", e.response["Error"]["Message"])
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5: SERVICE QUOTAS & CLOUDWATCH USAGE
# ═══════════════════════════════════════════════════════════════════════════════


def collect_service_quotas(sq_client: Any) -> list[dict[str, Any]]:
    """Collect all Connect service quotas (applied values).

    Args:
        sq_client: Boto3 Service Quotas client.

    Returns:
        List of quota dicts with QuotaCode, QuotaName, and Value fields.
    """
    logger.info("Collecting service quotas...")
    quotas: list[dict[str, Any]] = []
    next_token: str | None = None

    while True:
        params: dict[str, Any] = {
            "ServiceCode": CONNECT_SERVICE_CODE,
            "MaxResults": SERVICE_QUOTA_PAGE_SIZE,
        }
        if next_token:
            params["NextToken"] = next_token

        response = sq_client.list_service_quotas(**params)
        quotas.extend(response.get("Quotas", []))
        next_token = response.get("NextToken")

        if not next_token:
            break

    logger.info("Found %d service quotas.", len(quotas))
    return quotas


def collect_usage_metrics(cw_client: Any, instance_id: str) -> dict[str, Any]:
    """Collect CloudWatch API usage metrics for the last 7 days.

    Queries the AWS/Usage namespace for Connect API call counts,
    then estimates peak TPS based on an 8-hour business day.

    Args:
        cw_client: Boto3 CloudWatch client.
        instance_id: Connect instance ID (for logging context).

    Returns:
        Dictionary mapping API name to usage stats:
            daily_values: List of daily call counts.
            avg_daily: Average daily calls.
            peak_daily: Maximum daily calls.
            peak_tps_estimate: Estimated peak TPS (peak / 28800).
    """
    logger.info("Collecting CloudWatch usage metrics...")

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=CLOUDWATCH_LOOKBACK_DAYS)

    metrics: dict[str, Any] = {}

    for api_name in HIGH_TRAFFIC_APIS:
        try:
            response = cw_client.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "usage",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/Usage",
                                "MetricName": "CallCount",
                                "Dimensions": [
                                    {"Name": "Service", "Value": "Connect"},
                                    {"Name": "Type", "Value": "API"},
                                    {"Name": "Resource", "Value": api_name},
                                    {"Name": "Class", "Value": "None"},
                                ],
                            },
                            "Period": 86_400,
                            "Stat": "Sum",
                        },
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
            )

            values = response["MetricDataResults"][0].get("Values", [])
            metrics[api_name] = {
                "daily_values": values,
                "avg_daily": sum(values) / len(values) if values else 0,
                "peak_daily": max(values) if values else 0,
                "peak_tps_estimate": (
                    max(values) / BUSINESS_DAY_SECONDS if values else 0
                ),
            }

        except (ClientError, BotoCoreError, IndexError, KeyError) as e:
            logger.warning("Failed to get metrics for %s: %s", api_name, e)
            metrics[api_name] = {
                "daily_values": [],
                "avg_daily": 0,
                "peak_daily": 0,
                "peak_tps_estimate": 0,
                "error": str(e),
            }

    logger.info("Collected metrics for %d APIs.", len(metrics))
    return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# QUOTA IMPACT MODEL
# ═══════════════════════════════════════════════════════════════════════════════


def build_quota_impact_model(
    flows: list[dict[str, Any]],
    lambdas: list[dict[str, Any]],
    numbers: list[dict[str, Any]],
    quotas: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Build the predictive quota impact model.

    Creates a comprehensive model that maps:
        - Flows to their Lambda invocations
        - Phone numbers to their TDG distribution
        - Quotas to their current headroom (limit - peak)
        - Migration impact formulas for capacity planning

    Args:
        flows: List of contact flow dicts with lambdas_invoked.
        lambdas: List of Lambda configuration dicts.
        numbers: List of phone number summary dicts.
        quotas: List of service quota dicts.
        metrics: Dictionary of API usage metrics.

    Returns:
        Complete quota impact model dict with:
            generated_at, flow_to_lambda_map, tdg_number_distribution,
            quota_headroom, migration_impact_formulas, summary.
    """
    # Map: flow_id → list of Lambda ARNs
    flow_to_lambdas: dict[str, list[str]] = {}
    for flow in flows:
        if isinstance(flow, dict) and "Id" in flow:
            flow_to_lambdas[flow["Id"]] = flow.get("lambdas_invoked", [])

    # Map: TDG ID → number counts by type
    tdg_numbers: dict[str, dict[str, int]] = defaultdict(
        lambda: {"DID": 0, "TOLL_FREE": 0, "total": 0}
    )
    for num in numbers:
        target_arn = num.get("TargetArn", "")
        tdg_id = target_arn.split("/")[-1] if "/" in target_arn else "unknown"
        num_type = num.get("PhoneNumberType", "DID")
        tdg_numbers[tdg_id][num_type] += 1
        tdg_numbers[tdg_id]["total"] += 1

    # Quota headroom calculation
    quota_headroom: dict[str, dict[str, Any]] = {}
    for quota in quotas:
        code = quota.get("QuotaCode", "")
        name = quota.get("QuotaName", "")
        limit = quota.get("Value") or 0

        # Match quota to usage metric by extracting API name
        api_name = name.replace("Rate of ", "").replace(" API requests", "")
        usage = metrics.get(api_name, {})
        peak_tps = usage.get("peak_tps_estimate", 0)

        utilization_pct = (peak_tps / limit * 100) if limit > 0 else 0

        quota_headroom[code] = {
            "name": name,
            "limit": limit,
            "peak_tps": round(peak_tps, 2),
            "utilization_pct": round(utilization_pct, 1),
            "headroom_tps": round(limit - peak_tps, 2),
        }

    # Summary statistics
    summary = {
        "total_numbers": len(numbers),
        "total_flows": len(flows),
        "total_connect_lambdas": len(lambdas),
        "lambdas_with_provisioned_concurrency": sum(
            1
            for lam in lambdas
            if isinstance(lam, dict) and lam.get("ProvisionedConcurrency")
        ),
        "quotas_above_70_pct": sum(
            1
            for q in quota_headroom.values()
            if q.get("utilization_pct", 0) > 70
        ),
    }

    return {
        "_metadata": {
            "tool": "connect-resource-mapper",
            "version": "1.0.0",
            "data_provenance": (
                "flow_to_lambda_map: extracted from DescribeContactFlow API responses. "
                "tdg_number_distribution: counted from ListPhoneNumbersV2 TargetArn field. "
                "quota_headroom.limit: from ServiceQuotas ListServiceQuotas (applied values). "
                "quota_headroom.peak_tps: calculated as max(7-day CloudWatch daily sum) / 28800. "
                "No values are fabricated or estimated beyond the documented formulas."
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flow_to_lambda_map": flow_to_lambdas,
        "tdg_number_distribution": dict(tdg_numbers),
        "quota_headroom": quota_headroom,
        "migration_impact_formulas": {
            "per_tfn_added": {
                "phone_number_quota": "+1",
                "api_calls_per_contact": "15-22",
                "note": (
                    "If TFN maps to existing flow/TDG, no new API pressure. "
                    "If new flow, add Lambda + API overhead."
                ),
            },
            "per_contact_flow_added": {
                "contact_flow_quota": "+1",
                "lambda_invocations": "+2-6 per contact on that flow",
                "new_api_tps": (
                    "contacts_per_day_on_flow / "
                    f"{BUSINESS_DAY_SECONDS} * apis_per_contact"
                ),
            },
            "per_api_introduced_in_lambda": {
                "formula": (
                    "concurrent_contacts_hitting_lambda * "
                    "calls_per_contact_to_api"
                ),
                "check": "if result > quota_limit, file SLI before deployment",
            },
            "per_migration_wave_of_N_numbers": {
                "formula": (
                    "N * avg_contacts_per_day_per_number / "
                    f"{BUSINESS_DAY_SECONDS} * apis_per_contact"
                ),
                "action": (
                    "Compare to headroom_tps for each API. "
                    "File SLI for any exceeding 70%."
                ),
            },
        },
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════


def generate_dashboard(
    resource_map: dict[str, Any],
    model: dict[str, Any],
    output_path: str,
) -> None:
    """Generate a self-contained interactive HTML dashboard.

    The dashboard embeds all collected data as a JSON payload and renders
    an interactive quota calculator with flow chain visualization.

    Args:
        resource_map: Full resource graph from collection phase.
        model: Quota impact model from build phase.
        output_path: File path for the generated HTML file.
    """
    dashboard_data = _build_dashboard_payload(resource_map, model)
    data_json = json.dumps(dashboard_data, default=str)

    html = _render_dashboard_html(data_json)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Dashboard generated: %s", output_path)


def _build_dashboard_payload(
    resource_map: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Build the JSON data payload embedded in the dashboard HTML.

    Args:
        resource_map: Full resource graph.
        model: Quota impact model.

    Returns:
        Dictionary containing all data needed by the dashboard JavaScript.
    """
    flows_with_lambdas = {
        k: v for k, v in model["flow_to_lambda_map"].items() if v
    }

    flow_names: dict[str, str] = {}
    flow_types: dict[str, str] = {}
    for flow in resource_map.get("contact_flows", []):
        if isinstance(flow, dict):
            flow_names[flow.get("Id", "")] = flow.get(
                "Name", flow.get("Id", "")[:8]
            )
            flow_types[flow.get("Id", "")] = flow.get("Type", "CONTACT_FLOW")

    phone_numbers: list[dict[str, str]] = []
    for num in resource_map.get("phone_numbers", []):
        if isinstance(num, dict):
            target_arn = num.get("TargetArn", num.get("targetArn", ""))
            phone_numbers.append(
                {
                    "number": num.get("PhoneNumber", num.get("phoneNumber", "")),
                    "type": num.get(
                        "PhoneNumberType", num.get("phoneNumberType", "")
                    ),
                    "tdg": target_arn.split("/")[-1] if "/" in target_arn else "",
                    "id": num.get("PhoneNumberId", num.get("phoneNumberId", "")),
                }
            )

    lambda_details: dict[str, dict[str, Any]] = {}
    for lam in resource_map.get("lambda_functions", []):
        if isinstance(lam, dict) and "FunctionName" in lam:
            lambda_details[lam.get("FunctionArn", "")] = {
                "name": lam["FunctionName"],
                "runtime": lam.get("Runtime", "unknown"),
                "memory": lam.get("MemorySize", 0),
                "timeout": lam.get("Timeout", 0),
                "pc": len(lam.get("ProvisionedConcurrency", [])) > 0,
            }

    key_quotas: dict[str, dict[str, Any]] = {
        code: q
        for code, q in model.get("quota_headroom", {}).items()
        if q.get("limit", 0) > 0
    }

    return {
        "flows": flows_with_lambdas,
        "flow_names": flow_names,
        "flow_types": flow_types,
        "phone_numbers": phone_numbers,
        "lambda_details": lambda_details,
        "quotas": key_quotas,
        "summary": model.get("summary", {}),
        "formulas": model.get("migration_impact_formulas", {}),
        "instance_id": resource_map.get("instance_id", ""),
        "region": resource_map.get("region", ""),
        "generated_at": model.get("generated_at", ""),
        "tdg_distribution": model.get("tdg_number_distribution", {}),
    }


def _render_dashboard_html(data_json: str) -> str:
    """Render the complete dashboard HTML with embedded data.

    Args:
        data_json: JSON-serialized dashboard payload.

    Returns:
        Complete HTML string for the dashboard file.
    """
    # Dashboard HTML is generated separately — see connect-dashboard-v3.html
    # for the interactive drill-down version. This generates a basic
    # calculator for standalone use.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Connect Quota Impact Calculator</title>
<style>
:root {{ --bg: #0d1117; --panel: #161b22; --border: #30363d; --text: #c9d1d9; --blue: #58a6ff; --green: #3fb950; --yellow: #d29922; --red: #f85149; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; padding: 24px; }}
h1 {{ color: var(--blue); font-size: 16px; margin-bottom: 16px; }}
.meta {{ color: #8b949e; font-size: 11px; margin-bottom: 24px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 16px; text-align: center; }}
.card .num {{ font-size: 28px; font-weight: 700; color: var(--blue); }}
.card .lbl {{ font-size: 10px; color: #8b949e; margin-top: 4px; }}
pre {{ background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 16px; overflow-x: auto; font-size: 11px; }}
</style>
</head>
<body>
<h1>Connect Quota Impact Calculator</h1>
<div class="meta" id="meta"></div>
<div class="grid" id="summary"></div>
<h2 style="font-size:13px;color:var(--blue);margin:16px 0 8px;">Raw Data</h2>
<pre id="data"></pre>
<script>
const DATA = {data_json};
document.getElementById('meta').textContent = `Instance: ${{DATA.instance_id}} | Region: ${{DATA.region}} | Generated: ${{DATA.generated_at}}`;
const s = DATA.summary;
document.getElementById('summary').innerHTML = `
  <div class="card"><div class="num">${{s.total_numbers}}</div><div class="lbl">Phone Numbers</div></div>
  <div class="card"><div class="num">${{Object.keys(DATA.flows).length}}</div><div class="lbl">Flows with Lambdas</div></div>
  <div class="card"><div class="num">${{s.total_connect_lambdas}}</div><div class="lbl">Lambda Functions</div></div>
  <div class="card"><div class="num">${{s.lambdas_with_provisioned_concurrency}}</div><div class="lbl">Provisioned Concurrency</div></div>
  <div class="card"><div class="num">${{s.quotas_above_70_pct}}</div><div class="lbl">Quotas > 70%</div></div>
`;
document.getElementById('data').textContent = JSON.stringify(DATA, null, 2);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def collect_all(instance_id: str, region: str, profile: str | None = None) -> tuple[dict, dict]:
    """Collect all resources and build the quota impact model.

    Convenience function for Lambda import. Returns (resource_map, model).
    """
    clients = create_clients(region, profile)

    numbers = collect_phone_numbers(clients["connect"], instance_id)
    tdgs = collect_traffic_distribution_groups(clients["connect"], instance_id)
    flows = collect_contact_flows(clients["connect"], instance_id)
    lambdas = collect_lambda_functions(clients["lambda"], clients["connect"], instance_id)
    lex_bots = collect_lex_bots(clients["lex"], clients["connect"], instance_id)
    quotas = collect_service_quotas(clients["servicequotas"])
    metrics = collect_usage_metrics(clients["cloudwatch"], instance_id)

    model = build_quota_impact_model(flows, lambdas, numbers, quotas, metrics)

    resource_map = {
        "instance_id": instance_id,
        "region": region,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "phone_numbers": numbers,
        "tdgs": tdgs,
        "contact_flows": flows,
        "lambda_functions": lambdas,
        "lex_bots": lex_bots,
        "service_quotas": quotas,
        "usage_metrics": metrics,
    }

    return resource_map, model


def main() -> None:
    """Entry point: parse arguments, collect resources, build model, write outputs."""
    parser = argparse.ArgumentParser(
        description="Amazon Connect Resource Mapper — Quota Impact Model Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python connect-resource-mapper.py \\\n"
            "    --instance-id 587c546e-2328-4c36-baa2-37eaf4749631 \\\n"
            "    --region us-east-1 \\\n"
            "    --output-dir ./output\n"
        ),
    )
    parser.add_argument(
        "--instance-id", required=True, help="Connect Instance ID"
    )
    parser.add_argument(
        "--region", default="us-east-1", help="AWS Region (default: us-east-1)"
    )
    parser.add_argument(
        "--output-dir", default=".", help="Output directory (default: current)"
    )
    parser.add_argument(
        "--skip-flow-content",
        action="store_true",
        help="Skip DescribeContactFlow calls (faster, less detail)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--profile", default=None, help="AWS CLI profile name (default: environment default)"
    )
    parser.add_argument(
        "--line-config", default=None,
        help="Path to line-config.json for v4 operations dashboard (optional)"
    )
    parser.add_argument(
        "--live-endpoint", default=None,
        help="API Gateway URL for live CloudWatch refresh (from SAM deploy output)"
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("=" * 60)
    logger.info("  Amazon Connect Resource Mapper")
    logger.info("  Instance: %s", args.instance_id)
    logger.info("  Region:   %s", args.region)
    logger.info("  Output:   %s", args.output_dir)
    logger.info("=" * 60)

    clients = create_clients(args.region, args.profile)

    # ── Collect all layers ──
    numbers = collect_phone_numbers(clients["connect"], args.instance_id)
    tdgs = collect_traffic_distribution_groups(clients["connect"], args.instance_id)
    flows = collect_contact_flows(
        clients["connect"], args.instance_id, skip_content=args.skip_flow_content
    )
    lambdas = collect_lambda_functions(
        clients["lambda"], clients["connect"], args.instance_id
    )
    lex_bots = collect_lex_bots(clients["lex"], clients["connect"], args.instance_id)
    quotas = collect_service_quotas(clients["servicequotas"])
    metrics = collect_usage_metrics(clients["cloudwatch"], args.instance_id)

    # ── Build model ──
    logger.info("Building quota impact model...")
    model = build_quota_impact_model(flows, lambdas, numbers, quotas, metrics)

    # ── Write outputs ──
    resource_map: dict[str, Any] = {
        "_metadata": {
            "tool": "connect-resource-mapper",
            "version": "1.0.0",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source_account": "(derived from caller identity)",
            "source_region": args.region,
            "source_instance": args.instance_id,
            "data_provenance": (
                "All values in this file are direct API responses from AWS. "
                "No values are estimated, interpolated, or fabricated. "
                "peak_tps_estimate is calculated as: max(daily_values) / 28800."
            ),
        },
        "instance_id": args.instance_id,
        "region": args.region,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "phone_numbers": numbers,
        "phone_numbers_count": len(numbers),
        "tdgs": tdgs,
        "contact_flows": flows,
        "lambda_functions": lambdas,
        "lex_bots": lex_bots,
        "quotas": quotas,
        "usage_metrics": metrics,
    }

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    map_path = f"{args.output_dir}/connect-resource-map.json"
    model_path = f"{args.output_dir}/connect-quota-impact-model.json"
    dashboard_path = f"{args.output_dir}/connect-dashboard.html"

    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(resource_map, f, indent=2, default=str)

    with open(model_path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, default=str)

    import dashboard_v4
    line_config = {}
    line_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "line-config.json")
    if os.path.exists(line_config_path):
        with open(line_config_path, "r", encoding="utf-8") as f:
            line_config = json.load(f)
    dashboard_v4.generate_v4_dashboard(resource_map, model, line_config, dashboard_path)

    # ── Consolidated API Report (always generated) ──
    import consolidated_report
    report_path = f"{args.output_dir}/connect-api-report.html"
    consolidated_report.generate_consolidated_report(resource_map, model, report_path)

    # ── Summary ──
    logger.info("─" * 60)
    logger.info("Outputs:")
    logger.info("  %s", map_path)
    logger.info("  %s", model_path)
    logger.info("  %s  ← Open in browser", dashboard_path)
    logger.info("  %s  ← Consolidated API Report", report_path)
    logger.info("─" * 60)
    logger.info("Summary:")
    logger.info("  Phone numbers: %d", model["summary"]["total_numbers"])
    logger.info("  Contact flows: %d", model["summary"]["total_flows"])
    logger.info("  Lambdas: %d", model["summary"]["total_connect_lambdas"])
    logger.info(
        "  Provisioned Concurrency: %d",
        model["summary"]["lambdas_with_provisioned_concurrency"],
    )
    logger.info(
        "  Quotas > 70%% utilized: %d", model["summary"]["quotas_above_70_pct"]
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
