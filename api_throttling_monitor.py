#!/usr/bin/env python3
"""
Amazon Connect API Throttling Monitor
Monitors CloudWatch metrics for API throttling and pre-throttle utilization.

Proactive Utilization: Rate limit utilization monitoring (catches APIs approaching limits BEFORE throttling)
Throttle Detection:   Throttle detection (detects actual throttling events)
Peak-hour aware: Uses CloudWatch Maximum statistic to catch per-minute spikes, not just averages.
"""

import boto3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any

cloudwatch = boto3.client('cloudwatch')
sns = boto3.client('sns')
service_quotas = boto3.client('service-quotas')

# Common Connect API operations to monitor (including newer APIs from Connect 2.0)
MONITORED_APIS = [
    # Core User & Resource Management
    'CreateUser',
    'UpdateUser',
    'ListUsers',
    'DescribeUser',
    'UpdateUserConfig',
    
    # Queue Management
    'CreateQueue',
    'ListQueues',
    'DescribeQueue',
    
    # Contact Flow Management
    'CreateContactFlow',
    'UpdateContactFlowContent',
    'ListContactFlows',
    
    # Routing
    'CreateRoutingProfile',
    'ListRoutingProfiles',
    
    # Metrics & Analytics
    'GetCurrentMetricData',
    'GetMetricData',
    'GetMetricDataV2',
    'GetContactMetrics',
    'GetAnalyticsInsights',
    
    # Contact Management
    'StartChatContact',
    'StartTaskContact',
    'GetContactAttributes',
    'UpdateContactAttributes',
    'DescribeContact',
    
    # Phone Numbers
    'ListPhoneNumbersV2',
    
    # DataTable APIs (Newer Feature)
    'ListDataTablePrimaryValues',
    'ListDataTableValues',
    'GetDataTable',
    'UpdateDataTable',
    
    # Workspace APIs (Newer Feature)
    'ListWorkspaces',
    'DescribeWorkspace',
    
    # Security & Profiles
    'ListSecurityProfileFlowModules',
    'ListEntitySecurityProfiles',
    
    # Hours of Operation
    'ListChildHoursOfOperations',
    
    # Chat & Messaging (Newer Features)
    'SendOutboundChatMessage',
    'SendOutboundEmail',
    
    # Agent Management
    'UpdateAgentStatus',
    'GetCurrentUserData',
]


def get_rate_limit_quotas() -> Dict[str, float]:
    """
    Fetch current per-second rate limits for Connect APIs from Service Quotas.
    Returns a map of API name -> rate limit (calls/sec).
    """
    quotas = {}
    paginator = service_quotas.get_paginator('list_service_quotas')
    try:
        for page in paginator.paginate(ServiceCode='connect'):
            for q in page.get('Quotas', []):
                name = q.get('QuotaName', '')
                # Match "Rate of <API> API requests" pattern
                if name.startswith('Rate of ') and name.endswith(' API requests'):
                    api_name = name[len('Rate of '):-len(' API requests')]
                    quotas[api_name] = q.get('Value', 0)
    except Exception as e:
        print(f"Error fetching rate limit quotas: {e}")
    return quotas


def get_peak_utilization(hours: int = 1) -> Dict[str, Any]:
    """
    Proactive Utilization: Pre-throttle utilization monitoring.
    
    Compares actual peak per-second API call rates against quota limits
    BEFORE throttling occurs. Uses CloudWatch Maximum statistic with
    1-minute periods to catch real peak bursts, not hourly averages.
    
    Args:
        hours: Lookback period
        
    Returns:
        Dict with per-API utilization vs quota limit
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)

    # Step 1: Get rate limits from Service Quotas
    rate_limits = get_rate_limit_quotas()
    if not rate_limits:
        print("WARNING: Could not fetch rate limits from Service Quotas")
        return {'error': 'Could not fetch rate limits', 'apis': {}}

    print(f"Fetched {len(rate_limits)} API rate limits from Service Quotas")

    # Step 2: For each API with a known limit, get peak usage from AWS/Usage
    results = {}
    # Build batch query — up to 500 metrics per GetMetricData call
    queries = []
    api_index = {}
    idx = 0
    for api_name, limit in rate_limits.items():
        if limit <= 0:
            continue
        query_id = f"api{idx}"
        api_index[query_id] = (api_name, limit)
        queries.append({
            'Id': query_id,
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Usage',
                    'MetricName': 'CallCount',
                    'Dimensions': [
                        {'Name': 'Type', 'Value': 'API'},
                        {'Name': 'Resource', 'Value': api_name},
                        {'Name': 'Service', 'Value': 'Connect'},
                        {'Name': 'Class', 'Value': 'None'}
                    ]
                },
                'Period': 60,  # 1-minute granularity — catches real peaks
                'Stat': 'Sum'  # Sum of calls in that 1-minute window
            },
            'ReturnData': True
        })
        idx += 1

    if not queries:
        return {'error': 'No rate-limited APIs found', 'apis': {}}

    # GetMetricData supports up to 500 queries per call
    apis = {}
    for batch_start in range(0, len(queries), 500):
        batch = queries[batch_start:batch_start + 500]
        try:
            response = cloudwatch.get_metric_data(
                MetricDataQueries=batch,
                StartTime=start_time,
                EndTime=end_time,
                ScanBy='TimestampDescending'
            )
            for metric_result in response.get('MetricDataResults', []):
                query_id = metric_result['Id']
                api_name, limit = api_index[query_id]
                values = metric_result.get('Values', [])
                timestamps = metric_result.get('Timestamps', [])

                if not values:
                    continue

                # Peak calls in any 1-minute window
                peak_per_minute = max(values)
                # Convert to per-second rate (peak minute / 60)
                peak_per_second = peak_per_minute / 60.0
                utilization_pct = (peak_per_second / limit) * 100 if limit > 0 else 0

                # Find when the peak occurred
                peak_idx = values.index(peak_per_minute)
                peak_time = timestamps[peak_idx].strftime('%Y-%m-%d %H:%M UTC') if peak_idx < len(timestamps) else 'unknown'

                # Average calls per minute over the period
                avg_per_minute = sum(values) / len(values) if values else 0
                avg_per_second = avg_per_minute / 60.0

                apis[api_name] = {
                    'limit_per_second': limit,
                    'peak_per_second': round(peak_per_second, 2),
                    'avg_per_second': round(avg_per_second, 2),
                    'utilization_pct': round(utilization_pct, 1),
                    'peak_time': peak_time,
                    'total_calls_in_period': int(sum(values)),
                    'data_points': len(values),
                    'severity': (
                        'CRITICAL' if utilization_pct >= 90 else
                        'WARNING' if utilization_pct >= 70 else
                        'OK'
                    )
                }
        except Exception as e:
            print(f"Error fetching usage metrics batch: {e}")

    # Sort by utilization descending
    sorted_apis = dict(sorted(apis.items(), key=lambda x: x[1]['utilization_pct'], reverse=True))

    # Count by severity
    critical = sum(1 for a in sorted_apis.values() if a['severity'] == 'CRITICAL')
    warning = sum(1 for a in sorted_apis.values() if a['severity'] == 'WARNING')

    return {
        'apis': sorted_apis,
        'summary': {
            'total_monitored': len(sorted_apis),
            'critical_count': critical,
            'warning_count': warning,
            'period_hours': hours
        }
    }


def send_utilization_alert(utilization: Dict[str, Any], sns_topic_arn: str):
    """
    Send a clean, polished utilization report email.
    This goes to a SEPARATE SNS topic from throttle alerts so customers
    see a concise table — not mixed with noisy throttle event data.
    """
    apis = utilization.get('apis', {})
    summary = utilization.get('summary', {})
    total = summary.get('total_monitored', 0)
    critical = summary.get('critical_count', 0)
    warning = summary.get('warning_count', 0)

    at_risk = {k: v for k, v in apis.items() if v['severity'] in ('CRITICAL', 'WARNING')}
    if not at_risk:
        print("No APIs at risk — no utilization alert needed")
        return

    subject = f"Connect API Rate Limit Report — {critical} critical, {warning} warning"

    # Build clean table
    hdr = f"{'API':<35} {'Limit':>7} {'Peak':>8} {'Util%':>7}  {'Status'}"
    sep = "-" * 75
    rows = []
    for api_name, d in at_risk.items():
        icon = "CRITICAL" if d['severity'] == 'CRITICAL' else "WARNING"
        rows.append(
            f"{api_name:<35} {d['limit_per_second']:>5}/s {d['peak_per_second']:>6}/s {d['utilization_pct']:>6.1f}%  {icon}"
        )

    lines = [
        "Amazon Connect — API Rate Limit Utilization Report",
        sep,
        "",
        f"  Period:     Last {summary.get('period_hours', 1)} hour(s)",
        f"  Monitored:  {total} APIs",
        f"  Critical:   {critical}  (>= 90% of per-second limit)",
        f"  Warning:    {warning}  (>= 70% of per-second limit)",
        "",
        sep,
        hdr,
        sep,
    ]
    lines.extend(rows)
    lines.append(sep)

    # Add top OK APIs for context (top 5 by utilization that aren't at risk)
    ok_apis = {k: v for k, v in apis.items() if v['severity'] == 'OK'}
    top_ok = list(ok_apis.items())[:5]
    if top_ok:
        lines.append("")
        lines.append("Top APIs within safe range:")
        for api_name, d in top_ok:
            lines.append(
                f"  {api_name:<35} {d['limit_per_second']:>5}/s {d['peak_per_second']:>6}/s {d['utilization_pct']:>6.1f}%  OK"
            )

    lines.extend([
        "",
        sep,
        "Recommended Actions:",
        "",
        "  CRITICAL — Request a quota increase via AWS Support immediately.",
        "  WARNING  — Plan a quota increase or reduce call volume:",
        "             - Describe*/List* APIs: cache results in DynamoDB",
        "             - GetCurrentMetricData: reduce polling frequency",
        "             - Write APIs: batch multiple updates per call",
        "",
        f"  Report generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ])

    try:
        sns.publish(
            TopicArn=sns_topic_arn,
            Subject=subject[:100],
            Message="\n".join(lines)
        )
        print(f"Utilization report sent: {critical} critical, {warning} warning")
    except Exception as e:
        print(f"Error sending utilization report: {e}")


def get_api_throttling_metrics(hours: int = 1) -> Dict[str, Any]:
    """
    Get API throttling metrics from CloudWatch for the past N hours
    
    Args:
        hours: Number of hours to look back
        
    Returns:
        Dictionary with throttling statistics per API
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    
    results = {}
    
    print(f"Checking API throttling metrics for the last {hours} hour(s)...")
    
    # Query CloudWatch for throttling metrics
    try:
        # Get all Connect API throttling events
        response = cloudwatch.get_metric_statistics(
            Namespace='AWS/Connect',
            MetricName='ThrottledCalls',
            Dimensions=[],
            StartTime=start_time,
            EndTime=end_time,
            Period=3600,  # 1 hour
            Statistics=['Sum', 'SampleCount']
        )
        
        total_throttled = sum([point['Sum'] for point in response.get('Datapoints', [])])
        
        results['total_throttled_calls'] = total_throttled
        results['period_hours'] = hours
        results['apis_throttled'] = {}
        
        # Check specific API operations
        for api_name in MONITORED_APIS:
            try:
                api_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/Connect',
                    MetricName='ThrottledCalls',
                    Dimensions=[
                        {'Name': 'APIName', 'Value': api_name}
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum', 'Maximum']
                )
                
                datapoints = api_response.get('Datapoints', [])
                if datapoints:
                    api_throttled = sum([point['Sum'] for point in datapoints])
                    max_throttled = max([point['Maximum'] for point in datapoints])
                    
                    if api_throttled > 0:
                        results['apis_throttled'][api_name] = {
                            'total_throttled': api_throttled,
                            'max_in_period': max_throttled,
                            'status': 'THROTTLED' if api_throttled > 10 else 'WARNING'
                        }
                        
            except Exception as e:
                print(f"Error checking {api_name}: {str(e)}")
                
    except Exception as e:
        print(f"Error getting throttling metrics: {str(e)}")
        results['error'] = str(e)
    
    return results


def get_api_call_volume(hours: int = 1) -> Dict[str, Any]:
    """
    Get API call volume metrics with peak-hour awareness.
    
    Uses 1-minute period with both Sum and Maximum to capture real peak
    bursts, not just hourly averages that hide spikes during business hours.
    
    Args:
        hours: Number of hours to look back
        
    Returns:
        Dictionary with API call statistics including peak rates
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    
    results = {}
    
    try:
        # Get total API calls
        response = cloudwatch.get_metric_statistics(
            Namespace='AWS/Connect',
            MetricName='CallCount',
            Dimensions=[],
            StartTime=start_time,
            EndTime=end_time,
            Period=3600,
            Statistics=['Sum']
        )
        
        total_calls = sum([point['Sum'] for point in response.get('Datapoints', [])])
        results['total_api_calls'] = total_calls
        
        # Check high-volume APIs with peak detection
        for api_name in MONITORED_APIS:
            try:
                # Use 1-minute periods to find peak minutes
                api_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/Connect',
                    MetricName='CallCount',
                    Dimensions=[
                        {'Name': 'APIName', 'Value': api_name}
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=60,  # 1-minute granularity for peak detection
                    Statistics=['Sum', 'Maximum']
                )
                
                datapoints = api_response.get('Datapoints', [])
                if datapoints:
                    sums = [point['Sum'] for point in datapoints]
                    api_calls = sum(sums)
                    peak_per_minute = max(sums)
                    peak_per_second = peak_per_minute / 60.0
                    avg_per_second = api_calls / (hours * 3600) if hours > 0 else 0
                    
                    if api_calls > 0:
                        results[api_name] = {
                            'total_calls': api_calls,
                            'peak_per_minute': peak_per_minute,
                            'peak_per_second': round(peak_per_second, 2),
                            'avg_per_second': round(avg_per_second, 2),
                            'peak_to_avg_ratio': round(peak_per_second / avg_per_second, 1) if avg_per_second > 0 else 0
                        }
                        
            except Exception as e:
                print(f"Error checking call volume for {api_name}: {str(e)}")
                
    except Exception as e:
        print(f"Error getting call volume metrics: {str(e)}")
        results['error'] = str(e)
    
    return results


def calculate_throttle_rate(throttling: Dict, call_volume: Dict) -> Dict[str, Any]:
    """
    Calculate throttle rate as percentage of total calls.
    Uses peak-aware call volume data when available.
    
    Args:
        throttling: Throttling metrics
        call_volume: API call volume metrics (now includes peak data)
        
    Returns:
        Dictionary with throttle rates per API
    """
    rates = {}
    
    total_calls = call_volume.get('total_api_calls', 0)
    total_throttled = throttling.get('total_throttled_calls', 0)
    
    if total_calls > 0:
        rates['overall_throttle_rate'] = (total_throttled / total_calls) * 100
    else:
        rates['overall_throttle_rate'] = 0
    
    rates['api_throttle_rates'] = {}
    
    for api_name, throttle_data in throttling.get('apis_throttled', {}).items():
        vol = call_volume.get(api_name, {})
        # Handle both old format (plain int) and new format (dict with peak data)
        if isinstance(vol, dict):
            api_calls = vol.get('total_calls', 0)
            peak_per_second = vol.get('peak_per_second', 0)
        else:
            api_calls = vol
            peak_per_second = 0

        api_throttled = throttle_data.get('total_throttled', 0)
        
        if api_calls > 0:
            throttle_rate = (api_throttled / api_calls) * 100
            entry = {
                'throttle_rate_percent': round(throttle_rate, 2),
                'calls': api_calls,
                'throttled': api_throttled,
                'severity': 'CRITICAL' if throttle_rate > 10 else 'WARNING' if throttle_rate > 5 else 'INFO'
            }
            if peak_per_second > 0:
                entry['peak_per_second'] = peak_per_second
            rates['api_throttle_rates'][api_name] = entry
    
    return rates


def send_throttling_alert(throttling: Dict, rates: Dict, sns_topic_arn: str):
    """
    Send SNS alert if significant throttling is detected
    
    Args:
        throttling: Throttling metrics
        rates: Throttle rate calculations
        sns_topic_arn: SNS topic ARN for alerts
    """
    total_throttled = throttling.get('total_throttled_calls', 0)
    overall_rate = rates.get('overall_throttle_rate', 0)
    
    if total_throttled == 0:
        print("No throttling detected - no alert needed")
        return
    
    # Build alert message
    subject = f"⚠️ Connect API Throttling Detected - {total_throttled} throttled calls"
    
    message_parts = [
        "Amazon Connect API Throttling Alert",
        "=" * 60,
        f"\nOverall Throttling:",
        f"  Total Throttled Calls: {total_throttled}",
        f"  Overall Throttle Rate: {overall_rate:.2f}%",
        f"\nAffected APIs:",
    ]
    
    # Add details for each throttled API
    for api_name, data in rates.get('api_throttle_rates', {}).items():
        severity = data['severity']
        emoji = "🔴" if severity == "CRITICAL" else "🟡" if severity == "WARNING" else "🔵"
        
        message_parts.append(
            f"\n{emoji} {api_name}:"
            f"\n  - Throttle Rate: {data['throttle_rate_percent']}%"
            f"\n  - Total Calls: {data['calls']}"
            f"\n  - Throttled: {data['throttled']}"
            f"\n  - Severity: {severity}"
        )
    
    message_parts.extend([
        f"\n\n" + "=" * 60,
        "\nRecommended Actions:",
        "1. Review application logs for retry logic",
        "2. Implement exponential backoff",
        "3. Consider request rate optimization",
        "4. Request quota increase if needed",
        "\nTime: " + datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    ])
    
    message = "\n".join(message_parts)
    
    try:
        response = sns.publish(
            TopicArn=sns_topic_arn,
            Subject=subject,
            Message=message
        )
        print(f"Alert sent successfully. MessageId: {response['MessageId']}")
    except Exception as e:
        print(f"Error sending alert: {str(e)}")


def main(event, context):
    """
    Lambda handler function.
    
    Runs two monitoring tiers:
      Proactive Utilization: Pre-throttle utilization (APIs approaching rate limits)
      Throttle Detection:   Throttle detection (actual throttling events)
    
    Args:
        event: Lambda event object. Options:
            hours (int): Lookback period, default 1
            skip_utilization (bool): Skip Proactive Utilization, default False
            skip_throttling (bool): Skip Throttle Detection, default False
        context: Lambda context object
        
    Returns:
        Dictionary with monitoring results
    """
    import os
    
    hours = event.get('hours', 1)
    skip_utilization = event.get('skip_utilization', False)
    skip_throttling = event.get('skip_throttling', False)

    # Separate SNS topics: utilization report (clean) vs throttle alerts (ops)
    utilization_topic = os.environ.get('UTILIZATION_SNS_TOPIC_ARN')
    throttle_topic = os.environ.get('ALERT_SNS_TOPIC_ARN')

    # Backward compatible: if only ALERT_SNS_TOPIC_ARN is set, use it for both
    if not utilization_topic:
        utilization_topic = throttle_topic
    
    print(f"Starting API throttling monitor (Proactive Utilization + Throttle Detection)...")
    print(f"Lookback period: {hours} hours")
    
    result = {
        'timestamp': datetime.utcnow().isoformat(),
        'period_hours': hours,
    }

    # ── Proactive Utilization: Pre-throttle utilization ──
    if not skip_utilization:
        print("\n── Proactive Utilization: Rate Limit Utilization ──")
        utilization = get_peak_utilization(hours)
        result['utilization'] = utilization

        summary = utilization.get('summary', {})
        print(f"  Monitored: {summary.get('total_monitored', 0)} APIs")
        print(f"  Critical (≥90%): {summary.get('critical_count', 0)}")
        print(f"  Warning  (≥70%): {summary.get('warning_count', 0)}")

        for api_name, data in list(utilization.get('apis', {}).items())[:5]:
            print(f"  {data['severity']:8s} {api_name}: {data['peak_per_second']}/{data['limit_per_second']}/s ({data['utilization_pct']}%)")

        if utilization_topic:
            send_utilization_alert(utilization, utilization_topic)
    
    # ── Throttle Detection: Throttle detection (existing) ──
    if not skip_throttling:
        print("\n── Throttle Detection: Throttle Detection ──")
        throttling = get_api_throttling_metrics(hours)
        call_volume = get_api_call_volume(hours)
        rates = calculate_throttle_rate(throttling, call_volume)

        result['throttling_metrics'] = throttling
        result['call_volume_metrics'] = call_volume
        result['throttle_rates'] = rates
        result['status'] = 'OK' if throttling.get('total_throttled_calls', 0) == 0 else 'THROTTLED'

        if throttle_topic and throttling.get('total_throttled_calls', 0) > 0:
            send_throttling_alert(throttling, rates, throttle_topic)

        print(f"  Total API Calls: {call_volume.get('total_api_calls', 0)}")
        print(f"  Total Throttled: {throttling.get('total_throttled_calls', 0)}")
        print(f"  Throttle Rate: {rates.get('overall_throttle_rate', 0):.2f}%")
        print(f"  APIs Affected: {len(throttling.get('apis_throttled', {}))}")
    
    return {
        'statusCode': 200,
        'body': json.dumps(result, indent=2, default=str)
    }


if __name__ == '__main__':
    # For local testing
    test_event = {'hours': 1}
    test_context = {}
    result = main(test_event, test_context)
    print("\n" + "=" * 60)
    print("Test Result:")
    print(result['body'])

    # Test Proactive Utilization only
    # result = main({'hours': 6, 'skip_throttling': True}, {})
    # print(result['body'])
