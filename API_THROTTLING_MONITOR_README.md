# Amazon Connect API Throttling Monitor

## What It Does

This monitors **API rate limit throttling** using CloudWatch metrics instead of checking quota limits. This is the **correct** way to monitor API throttling because:

✅ **Real-time detection** - Catches actual throttling events as they happen  
✅ **Historical analysis** - Shows trends over time  
✅ **Per-API tracking** - Identifies which specific APIs are being throttled  
✅ **Throttle rate calculation** - Shows percentage of calls being throttled  
✅ **Automatic alerts** - Sends SNS alerts when throttling is detected  

## Why Use This Instead of Quota Monitoring?

### API Rate Limits vs Capacity Quotas

| Type | Example | Monitoring Method |
|------|---------|-------------------|
| **Capacity Quota** | "100 users per instance" | ✅ Service Quotas API (quota monitor) |
| **API Rate Limit** | "2 CreateUser calls/sec" | ✅ CloudWatch metrics (THIS monitor) |

**Key Difference:**
- Capacity quotas are **cumulative** - you can fill them up
- Rate limits **reset every second** - you can't "check" them, only detect throttling

## How It Works

1. **Reads CloudWatch Metrics**
   - `ThrottledCalls` - How many API calls were throttled
   - `CallCount` - Total API calls made

2. **Calculates Throttle Rates**
   - Overall throttle rate across all APIs
   - Per-API throttle rates

3. **Sends Alerts**
   - When throttling > 0 calls
   - Includes affected APIs
   - Shows severity levels

## Deployment

### Prerequisites
- Existing Connect Quota Monitor deployed
- AWS CLI configured
- Appropriate IAM permissions

### Deploy

```bash
chmod +x deploy_throttling_monitor.sh
./deploy_throttling_monitor.sh
```

This will:
1. ✅ Create Lambda function: `ConnectAPIThrottlingMonitor`
2. ✅ Schedule it to run every hour via EventBridge
3. ✅ Configure SNS alerts (uses same topic as quota monitor)
4. ✅ Test the deployment

## Usage

### View Logs
```bash
aws logs tail /aws/lambda/ConnectAPIThrottlingMonitor --follow
```

### Manual Test (check last 24 hours)
```bash
aws lambda invoke \
  --function-name ConnectAPIThrottlingMonitor \
  --payload '{"hours": 24}' \
  response.json

cat response.json | jq
```

### Check Specific Time Period
```bash
# Last 6 hours
aws lambda invoke \
  --function-name ConnectAPIThrottlingMonitor \
  --payload '{"hours": 6}' \
  response.json
```

## What Gets Monitored

### API Operations (20 most common)
- CreateUser, UpdateUser, ListUsers, DescribeUser
- CreateQueue, ListQueues, DescribeQueue
- CreateContactFlow, UpdateContactFlowContent, ListContactFlows
- CreateRoutingProfile, ListRoutingProfiles
- GetCurrentMetricData, GetMetricData
- StartChatContact, StartTaskContact
- GetContactAttributes, UpdateContactAttributes
- ListPhoneNumbersV2, DescribeContact

## Alert Example

When throttling is detected, you'll receive an email like:

```
Subject: ⚠️ Connect API Throttling Detected - 147 throttled calls

Amazon Connect API Throttling Alert
============================================================

Overall Throttling:
  Total Throttled Calls: 147
  Overall Throttle Rate: 2.34%

Affected APIs:

🔴 CreateUser:
  - Throttle Rate: 15.2%
  - Total Calls: 500
  - Throttled: 76
  - Severity: CRITICAL

🟡 ListUsers:
  - Throttle Rate: 8.1%
  - Total Calls: 875
  - Throttled: 71
  - Severity: WARNING

============================================================

Recommended Actions:
1. Review application logs for retry logic
2. Implement exponential backoff
3. Consider request rate optimization
4. Request quota increase if needed

Time: 2026-03-19 19:42:00 UTC
```

## Response Format

```json
{
  "timestamp": "2026-03-19T19:42:00.000000",
  "period_hours": 1,
  "throttling_metrics": {
    "total_throttled_calls": 147,
    "apis_throttled": {
      "CreateUser": {
        "total_throttled": 76,
        "max_in_period": 76,
        "status": "THROTTLED"
      }
    }
  },
  "call_volume_metrics": {
    "total_api_calls": 6283,
    "CreateUser": 500,
    "ListUsers": 875
  },
  "throttle_rates": {
    "overall_throttle_rate": 2.34,
    "api_throttle_rates": {
      "CreateUser": {
        "throttle_rate_percent": 15.2,
        "calls": 500,
        "throttled": 76,
        "severity": "CRITICAL"
      }
    }
  },
  "status": "THROTTLED"
}
```

## Severity Levels

- **🔴 CRITICAL** - Throttle rate > 10%
- **🟡 WARNING** - Throttle rate 5-10%
- **🔵 INFO** - Throttle rate < 5%

## Cost

- Lambda executions: ~720/month (hourly) = ~$0.15/month
- CloudWatch API calls: ~$0.01/month
- **Total**: < $0.20/month

## Troubleshooting

### No throttling data
- Check that your Connect instance has had API activity
- CloudWatch metrics may take 5-15 minutes to appear
- Try increasing the lookback period: `{"hours": 24}`

### Function not running
```bash
# Check EventBridge rule
aws events describe-rule --name ConnectAPIThrottlingMonitor-Schedule

# Check Lambda logs
aws logs tail /aws/lambda/ConnectAPIThrottlingMonitor --since 1h
```

### Not receiving alerts
- Verify SNS subscription is confirmed (check email)
- Check Lambda has ALERT_SNS_TOPIC_ARN environment variable
- Verify throttling is actually occurring

## Architecture

```
┌─────────────────┐
│  EventBridge    │  Triggers every hour
│  (Schedule)     │
└────────┬────────┘
         │
         v
┌─────────────────┐
│     Lambda      │  Queries CloudWatch
│   Throttling    │  for throttling metrics
│    Monitor      │
└────────┬────────┘
         │
         ├──────> CloudWatch (read metrics)
         │
         └──────> SNS (send alerts)
```

## Comparison: This vs Quota Monitor

| Feature | Quota Monitor | Throttling Monitor |
|---------|---------------|-------------------|
| **Monitors** | Resource capacity | API rate limits |
| **Checks** | Current usage vs limit | Actual throttling events |
| **Data Source** | Service Quotas API | CloudWatch metrics |
| **Alert When** | Usage > 80% of limit | Throttling detected |
| **Best For** | Capacity planning | API performance |
| **Examples** | "90/100 users" | "147 CreateUser calls throttled" |

Both monitors are complementary and solve different problems!

## Uninstall

```bash
# Delete Lambda function
aws lambda delete-function --function-name ConnectAPIThrottlingMonitor

# Delete EventBridge rule
aws events remove-targets --rule ConnectAPIThrottlingMonitor-Schedule --ids 1
aws events delete-rule --name ConnectAPIThrottlingMonitor-Schedule

# Delete deployment package
rm -f api-throttling-monitor.zip
```
