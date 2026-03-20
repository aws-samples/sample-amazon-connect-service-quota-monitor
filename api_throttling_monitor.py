#!/usr/bin/env python3
"""
Amazon Connect API Throttling Monitor
Monitors CloudWatch metrics for API throttling instead of checking quota limits
"""

import boto3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any

cloudwatch = boto3.client('cloudwatch')
sns = boto3.client('sns')

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
    Get API call volume metrics to understand usage patterns
    
    Args:
        hours: Number of hours to look back
        
    Returns:
        Dictionary with API call statistics
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
        
        # Check high-volume APIs
        for api_name in MONITORED_APIS:
            try:
                api_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/Connect',
                    MetricName='CallCount',
                    Dimensions=[
                        {'Name': 'APIName', 'Value': api_name}
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum']
                )
                
                datapoints = api_response.get('Datapoints', [])
                if datapoints:
                    api_calls = sum([point['Sum'] for point in datapoints])
                    if api_calls > 0:
                        results[api_name] = api_calls
                        
            except Exception as e:
                print(f"Error checking call volume for {api_name}: {str(e)}")
                
    except Exception as e:
        print(f"Error getting call volume metrics: {str(e)}")
        results['error'] = str(e)
    
    return results


def calculate_throttle_rate(throttling: Dict, call_volume: Dict) -> Dict[str, Any]:
    """
    Calculate throttle rate as percentage of total calls
    
    Args:
        throttling: Throttling metrics
        call_volume: API call volume metrics
        
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
        api_calls = call_volume.get(api_name, 0)
        api_throttled = throttle_data.get('total_throttled', 0)
        
        if api_calls > 0:
            throttle_rate = (api_throttled / api_calls) * 100
            rates['api_throttle_rates'][api_name] = {
                'throttle_rate_percent': round(throttle_rate, 2),
                'calls': api_calls,
                'throttled': api_throttled,
                'severity': 'CRITICAL' if throttle_rate > 10 else 'WARNING' if throttle_rate > 5 else 'INFO'
            }
    
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
    Lambda handler function
    
    Args:
        event: Lambda event object (can specify 'hours' to look back)
        context: Lambda context object
        
    Returns:
        Dictionary with monitoring results
    """
    import os
    
    # Configuration
    hours = event.get('hours', 1)
    sns_topic_arn = os.environ.get('ALERT_SNS_TOPIC_ARN')
    
    print(f"Starting API throttling monitor...")
    print(f"Lookback period: {hours} hours")
    
    # Get metrics
    throttling = get_api_throttling_metrics(hours)
    call_volume = get_api_call_volume(hours)
    rates = calculate_throttle_rate(throttling, call_volume)
    
    # Send alert if needed
    if sns_topic_arn and throttling.get('total_throttled_calls', 0) > 0:
        send_throttling_alert(throttling, rates, sns_topic_arn)
    
    # Prepare response
    result = {
        'timestamp': datetime.utcnow().isoformat(),
        'period_hours': hours,
        'throttling_metrics': throttling,
        'call_volume_metrics': call_volume,
        'throttle_rates': rates,
        'status': 'OK' if throttling.get('total_throttled_calls', 0) == 0 else 'THROTTLED'
    }
    
    print(f"\nSummary:")
    print(f"  Total API Calls: {call_volume.get('total_api_calls', 0)}")
    print(f"  Total Throttled: {throttling.get('total_throttled_calls', 0)}")
    print(f"  Throttle Rate: {rates.get('overall_throttle_rate', 0):.2f}%")
    print(f"  APIs Affected: {len(throttling.get('apis_throttled', {}))}")
    
    return {
        'statusCode': 200,
        'body': json.dumps(result, indent=2)
    }


if __name__ == '__main__':
    # For local testing
    test_event = {'hours': 1}
    test_context = {}
    result = main(test_event, test_context)
    print("\n" + "=" * 60)
    print("Test Result:")
    print(result['body'])
