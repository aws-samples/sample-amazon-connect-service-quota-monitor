# Amazon Connect Service Quota Monitor - Enhanced Edition

A comprehensive solution that monitors **all 287 Amazon Connect service quotas** across all Connect services with dynamic instance discovery, consolidated alerting, intelligent deployment capabilities, **pre-throttle utilization monitoring**, and **API throttling detection**.

## 🚀 Key Features

- **Comprehensive Coverage**: Monitors all 287 quotas (47 capacity quotas + 240 API rate limits)
- **Pre-Throttle Utilization Alerts (Proactive Utilization Monitor)**: Detects APIs approaching rate limits BEFORE throttling occurs
- **Peak-Hour Aware**: Uses 1-minute CloudWatch granularity to catch real per-second spikes, not hourly averages
- **API Throttling Detection (Throttle Detection)**: Real-time monitoring of API rate limit violations
- **Dynamic Discovery**: Automatically discovers Connect instances (no hardcoded IDs)
- **Consolidated Alerts**: One email per instance with all violations
- **Flexible Storage**: Supports S3, DynamoDB, or both
- **Multi-Service Support**: Cases, Customer Profiles, Voice ID, Wisdom, and more
- **Enterprise Security**: KMS encryption, VPC support, data sanitization
- **Intelligent Deployment**: Automatic code size detection with S3 fallback

## 📊 Monitored Services & Quotas

### Core Amazon Connect (15+ quotas)
- Users, Security profiles, Contact flows, Phone numbers
- Lambda functions, Queues, Routing profiles, Hours of operation
- Quick connects, Prompts, Predefined attributes, Flow modules

### Contact Handling (10+ quotas)
- Concurrent calls, chats, tasks, emails
- Campaign calls, Real-time metrics, Historical metrics
- Maximum participants per chat, Queue capacity

### Advanced Services (45+ quotas)
- **Cases**: Domains, Fields, Templates, Layouts
- **Customer Profiles**: Domains, Object types, Integrations
- **Voice ID**: Domains, Speakers, Fraudsters, Watchlists
- **Wisdom**: Knowledge bases, Documents, Assistants
- **Integrations**: App integrations, Event integrations, Lex bots
- **Forecasting**: Forecast groups, Schedules, Data retention
- **API Rate Limits**: Various API request rates

### API Throttling Monitoring (Proactive Utilization Monitor + Throttle Detection)

**Proactive Utilization Monitor — Pre-Throttle Utilization (NEW):**
- **Proactive detection** of APIs approaching rate limits BEFORE throttling occurs
- **Peak-hour aware**: Uses 1-minute CloudWatch periods to catch real per-second spikes during business hours, not hourly averages that hide peaks
- **Dynamic quota lookup**: Fetches current rate limits from Service Quotas API — no hardcoded values
- **Multi-threshold alerts**: WARNING at 70%, CRITICAL at 90% of per-second limit
- **Batch metric retrieval**: Uses CloudWatch `GetMetricData` (up to 500 metrics per call) for efficiency

**Throttle Detection — Throttle Detection (existing):**
- **Real-time throttling detection** via CloudWatch metrics
- **Per-API tracking** of throttle rates and patterns
- **Historical analysis** of throttling trends
- **Automated alerts** when APIs are being throttled

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   CloudWatch    │───▶│  Lambda Function │───▶│  SNS Topic      │
│   Events        │    │  (Quota Monitor) │    │  (Alerts)       │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │   DynamoDB      │    │   Email         │
                       │   (Storage)     │    │   Notifications │
                       └─────────────────┘    └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │   S3 Bucket     │
                       │   (Optional)    │
                       └─────────────────┘

┌─────────────────┐    ┌──────────────────────────────┐    ┌─────────────────┐
│   EventBridge   │───▶│  API Throttling Monitor       │───▶│  SNS Topic      │
│   (Hourly)      │    │  Lambda (Proactive Utilization Monitor + Throttle Detection)  │    │  (Alerts)       │
└─────────────────┘    └──────────────────────────────┘    └─────────────────┘
                          │                    │
                          ▼                    ▼
                ┌──────────────────┐  ┌─────────────────┐
                │  Service Quotas  │  │   CloudWatch    │
                │  API (limits)    │  │   AWS/Usage +   │
                │                  │  │   AWS/Connect   │
                └──────────────────┘  └─────────────────┘
```

## 📋 Prerequisites

- **AWS CLI** configured with appropriate permissions
- **Amazon Connect instance(s)** in your AWS account
- **Email address** for receiving alerts
- **IAM permissions** for CloudFormation, Lambda, SNS, S3, DynamoDB, Connect

## 🚀 Deployment

### Step 1: Deploy Main Quota Monitor

#### Prepare the Code Package

```bash
# Create deployment package
zip lambda-deployment.zip lambda_function.py
```

#### Deploy CloudFormation Stack

```bash
aws cloudformation create-stack \
  --stack-name ConnectQuotaMonitor \
  --template-body file://connect-quota-monitor-cfn.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=ThresholdPercentage,ParameterValue=80 \
    ParameterKey=NotificationEmail,ParameterValue=your-email@company.com \
    ParameterKey=UseS3Storage,ParameterValue=true \
    ParameterKey=UseDynamoDBStorage,ParameterValue=true
```

#### Deploy Lambda Code

```bash
# Wait for stack creation to complete
aws cloudformation wait stack-create-complete --stack-name ConnectQuotaMonitor

# Update Lambda function with actual code
aws lambda update-function-code \
  --function-name ConnectQuotaMonitor-EnhancedConnectQuotaMonitor \
  --zip-file fileb://lambda-deployment.zip
```

#### Verify Deployment

```bash
# Test the function
aws lambda invoke \
  --function-name ConnectQuotaMonitor-EnhancedConnectQuotaMonitor \
  --payload '{}' \
  test-response.json

# Check the response
cat test-response.json
```

#### Using the Deployment Script

```bash
# Make script executable
chmod +x deploy.sh

# Basic deployment (email is required)
./deploy.sh --email admin@company.com

# Deploy with custom threshold
./deploy.sh --email admin@company.com --threshold 85

# Advanced options (VPC deployment)
./deploy.sh \
  --email admin@company.com \
  --vpc-id vpc-12345678 \
  --subnet-ids subnet-123,subnet-456

# Custom memory and timeout
./deploy.sh --email admin@company.com --memory 1024 --timeout 900

# See all available options
./deploy.sh --help
```

#### Manual Deployment (Alternative)

If you prefer manual control:

### Step 2: Deploy API Throttling Monitor

The API throttling monitor provides **real-time detection** of API rate limit violations using CloudWatch metrics.

#### Why Monitor API Throttling?

| Type | Example | Monitoring Tier |
|------|---------|-----------------|
| **Capacity Quota** | "100 users per instance" | ✅ Service Quotas API (quota monitor) |
| **API Rate Limit — approaching** | "GetContactAttributes at 85% of 60/s" | ✅ Proactive Utilization Monitor: AWS/Usage metrics vs quota limits (NEW) |
| **API Rate Limit — breached** | "147 CreateUser calls throttled" | ✅ Throttle Detection: CloudWatch throttle metrics |

**Why Proactive Utilization Monitor matters:**
- Throttle Detection detects throttling **after** it happens — callers are already impacted
- Proactive Utilization Monitor catches APIs at 70-90% of their limit **before** throttling occurs
- Uses **1-minute CloudWatch periods** to find real peak-second rates during business hours, not hourly averages that hide spikes (e.g., an API averaging 3/s over an hour may spike to 40/s at 10am)

#### Deploy Throttling Monitor

```bash
chmod +x deploy_throttling_monitor.sh
./deploy_throttling_monitor.sh
```

This will:
1. ✅ Create Lambda function: `ConnectAPIThrottlingMonitor` (512 MB, 600s timeout)
2. ✅ Schedule it to run every hour via EventBridge
3. ✅ Configure SNS alerts (uses same topic as quota monitor)
4. ✅ Run both Proactive Utilization Monitor (utilization) and Throttle Detection (throttle detection)
5. ✅ Test the deployment

#### What Gets Monitored

**Proactive Utilization Monitor — Rate Limit Utilization (all Connect API quotas):**
- Dynamically fetches ALL rate limit quotas from Service Quotas API (no hardcoded list)
- Compares actual peak usage from `AWS/Usage` CloudWatch namespace against each limit
- Alerts at WARNING (≥70%) and CRITICAL (≥90%) of per-second rate limit

**Throttle Detection — Throttle Detection (40+ API operations):**
- **Core Connect APIs**: CreateUser, UpdateUser, ListUsers, DescribeUser
- **Queue Management**: CreateQueue, ListQueues, DescribeQueue
- **Contact Flow APIs**: CreateContactFlow, UpdateContactFlowContent, ListContactFlows
- **Routing**: CreateRoutingProfile, ListRoutingProfiles
- **Metrics APIs**: GetCurrentMetricData, GetMetricData, GetMetricDataV2, GetCurrentUserData
- **Contact APIs**: StartChatContact, StartTaskContact, StopContact, UpdateContact
- **Contact Attributes**: GetContactAttributes, UpdateContactAttributes
- **Participant APIs**: CreateParticipant, DisconnectParticipant, SendMessage, SendEvent
- **Integration APIs**: SendChatIntegrationEvent, CreateIntegrationAssociation
- **Contact Lens APIs**: ListRealtimeContactAnalysisSegments (V1 & V2)
- **Cases APIs**: CreateCase, SearchCases, GetCase, UpdateCase, ListCasesForContact
- And many more...

#### Usage Examples

```bash
# View throttling monitor logs
aws logs tail /aws/lambda/ConnectAPIThrottlingMonitor --follow

# Manual test — both tiers (last 24 hours)
aws lambda invoke \
  --function-name ConnectAPIThrottlingMonitor \
  --payload '{"hours": 24}' \
  response.json

# Run only Proactive Utilization Monitor utilization check (skip throttle detection)
aws lambda invoke \
  --function-name ConnectAPIThrottlingMonitor \
  --payload '{"hours": 6, "skip_throttling": true}' \
  response.json

# Run only Throttle Detection throttle detection (skip utilization)
aws lambda invoke \
  --function-name ConnectAPIThrottlingMonitor \
  --payload '{"hours": 1, "skip_utilization": true}' \
  response.json
```

## ⚙️ Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ThresholdPercentage` | 80 | Alert threshold (1-99%) |
| `NotificationEmail` | - | Email for alerts (optional) |
| `LambdaRuntime` | python3.12 | Python runtime version |
| `LambdaMemory` | 512 | Memory in MB (256-10240) |
| `LambdaTimeout` | 600 | Timeout in seconds (60-900) |
| `UseS3Storage` | true | Enable S3 storage |
| `UseDynamoDBStorage` | true | Enable DynamoDB storage |
| `ScheduleExpression` | rate(1 hour) | Monitoring frequency |

## 📧 Alert Examples

### Quota Violation Alert
```
Subject: Connect Quota Alert - Instance: MyConnectInstance (2 violations)

Instance: MyConnectInstance (12345678-1234-1234-1234-123456789012)
Threshold: 80%
Violations Found: 2

VIOLATIONS:
• Contact flows per instance: 147/100 (147.0%) - VIOLATION
• Lambda functions per instance: 42/50 (84.0%) - VIOLATION

SUMMARY:
• Total quotas monitored: 24
• Maximum utilization: 147.0%
• Average utilization: 16.8%
```

### Rate Limit Utilization Alert (Proactive Utilization Monitor — NEW)
```
Subject: 📊 Connect API Rate Limit Utilization — 1 critical, 2 warning

Amazon Connect API Rate Limit Utilization Alert
(Pre-throttle warning — APIs approaching limits)
============================================================

🔴 GetContactAttributes:
   Limit: 60/s
   Peak:  52.3/s (87.2% of limit)
   Avg:   9.1/s
   Peak at: 2026-03-19 14:23 UTC
   Calls in period: 823,041

🟡 DescribeContact:
   Limit: 20/s
   Peak:  15.8/s (79.0% of limit)
   Avg:   8.9/s
   Peak at: 2026-03-19 14:25 UTC
   Calls in period: 769,422

🟡 GetCurrentMetricData:
   Limit: 5/s
   Peak:  3.7/s (74.0% of limit)
   Avg:   3.8/s
   Peak at: 2026-03-19 10:02 UTC
   Calls in period: 341,208

============================================================
Recommended Actions:
• CRITICAL (≥90%): Request quota increase immediately
• WARNING (≥70%): Evaluate caching or request increase proactively
• For cacheable APIs (Describe*, List*): implement DynamoDB cache
• For real-time APIs (GetCurrentMetricData): reduce polling frequency

Time: 2026-03-19 19:42:00 UTC
```

### API Throttling Alert (Throttle Detection)
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

## 💾 Data Storage

### DynamoDB Structure
```json
{
  "id": "instance_12345678_1640995200",
  "timestamp": "2025-09-12T23:08:19.645730",
  "instance_id": "12345678-1234-1234-1234-123456789012",
  "instance_alias": "MyConnectInstance",
  "metrics_count": 24,
  "violations_count": 1,
  "metrics": [...],
  "summary": {
    "max_utilization": 147.0,
    "avg_utilization": 16.8
  }
}
```

### S3 Structure
```
s3://bucket/
├── connect-metrics/
│   ├── 2025/09/12/instance-metrics-timestamp.json
│   └── 2025/09/12/account-metrics-timestamp.json
└── connect-reports/
    └── 2025/09/12/execution-summary-timestamp.json
```

### API Throttling Response Format
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

## 🔧 Post-Deployment Configuration

### Update Alert Threshold
```bash
aws lambda update-function-configuration \
  --function-name ConnectQuotaMonitor-EnhancedConnectQuotaMonitor \
  --environment Variables='{
    "THRESHOLD_PERCENTAGE": "85",
    "ALERT_SNS_TOPIC_ARN": "arn:aws:sns:region:account:topic",
    "USE_S3_STORAGE": "true",
    "USE_DYNAMODB": "true"
  }'
```

### Add Email Subscribers
```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:region:account:ConnectQuotaAlerts \
  --protocol email \
  --notification-endpoint admin2@company.com
```

## 🔍 Monitoring & Troubleshooting

### Check Execution Logs

**Quota Monitor:**
```bash
aws logs tail /aws/lambda/ConnectQuotaMonitor-EnhancedConnectQuotaMonitor --follow
```

**Throttling Monitor:**
```bash
aws logs tail /aws/lambda/ConnectAPIThrottlingMonitor --follow
```

### Query DynamoDB Data
```bash
aws dynamodb scan --table-name ConnectQuotaMonitor --max-items 5
```

### Verify SNS Topic
```bash
aws sns get-topic-attributes --topic-arn your-sns-topic-arn
```

### Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| No alerts received | Email not confirmed | Check email and confirm SNS subscription |
| Permission errors | Missing IAM permissions | Review CloudFormation IAM policies |
| Quota over 100% | Soft limits exceeded | Normal behavior - existing resources remain |
| Missing quotas | Service not enabled | Enable Connect features (Cases, Voice ID, etc.) |
| Rate limit errors | Too many API calls | Normal - function handles gracefully |
| No throttling data | No recent API activity | Check instance has activity, increase lookback period |

### Throttling Monitor Troubleshooting

**No throttling data:**
- Check that your Connect instance has had API activity
- CloudWatch metrics may take 5-15 minutes to appear
- Try increasing the lookback period: `{"hours": 24}`

**Function not running:**
```bash
# Check EventBridge rule
aws events describe-rule --name ConnectAPIThrottlingMonitor-Schedule

# Check Lambda logs
aws logs tail /aws/lambda/ConnectAPIThrottlingMonitor --since 1h
```

**Not receiving alerts:**
- Verify SNS subscription is confirmed (check email)
- Check Lambda has ALERT_SNS_TOPIC_ARN environment variable
- Verify throttling is actually occurring

## 💰 Cost Estimation

**Monthly Costs (typical):**
- **Lambda (Quota Monitor)**: ~$2-5 (hourly execution)
- **Lambda (Throttling Monitor)**: ~$0.15 (hourly execution)
- **DynamoDB**: ~$1-3 (quota data storage)
- **S3**: ~$0.50-1 (optional storage)
- **SNS**: ~$0.10-0.50 (notifications)
- **CloudWatch**: ~$0.50-1 (logs & metrics)

**Total: ~$4-11/month** for comprehensive Connect monitoring

## 🔒 Security Features

- ✅ **KMS Encryption**: SNS topics, DynamoDB tables, Lambda environment
- ✅ **Data Sanitization**: Removes sensitive data from logs
- ✅ **IAM Least Privilege**: Minimal required permissions
- ✅ **VPC Support**: Optional VPC deployment
- ✅ **Dead Letter Queue**: Failed execution handling
- ✅ **Reserved Concurrency**: Prevents excessive executions

## 📈 Performance

### Quota Monitor
- **Execution Time**: 60-120 seconds (depending on instance count)
- **Memory Usage**: ~100-150 MB (512 MB allocated)
- **Quota Coverage**: 287 quotas total (47 capacity quotas via Service Quotas API)
- **Scalability**: Handles multiple instances automatically
- **Rate Limiting**: Built-in retry logic and graceful degradation

### API Throttling Monitor (Proactive Utilization Monitor + Throttle Detection)
- **Execution Time**: 30-90 seconds (Proactive Utilization Monitor fetches quotas + batch CloudWatch metrics)
- **Memory Usage**: ~100-200 MB (512 MB allocated)
- **Lookback Period**: 1-24 hours configurable
- **Proactive Utilization Monitor Coverage**: All Connect API rate limit quotas (dynamically discovered)
- **Throttle Detection Coverage**: 40+ API operations monitored for throttling
- **Execution Cost**: < $0.50/month

## 🎯 Success Validation

**✅ Deployment is successful when:**
1. CloudFormation stack creates without errors
2. Lambda functions execute successfully
3. Email alerts are received for violations
4. DynamoDB contains quota data
5. CloudWatch logs show successful execution
6. All Connect instances are discovered
7. Throttling monitor detects API usage patterns

## 📊 Coverage Analysis

### ✅ Well Covered Categories

1. **Core Amazon Connect** - Excellent coverage (15+ quotas)
2. **Contact Handling & Metrics** - Good coverage (10+ quotas)
3. **Routing & Queues** - Comprehensive coverage
4. **Integrations** - Good coverage (Lambda, Lex, App integrations)
5. **Related Services** - Good coverage (Customer Profiles, Cases, Voice ID, Wisdom)
6. **API Throttling** - Real-time monitoring via CloudWatch

### ⚠️ Areas for Enhancement

1. **Contact Lens** - Minimal coverage (4 entries)
   - Missing: post-call analytics jobs, chat analytics jobs, summary jobs

2. **Tasks** - Basic coverage
   - Has task templates and fields
   - Missing: concurrent task limits monitoring

3. **Forecasting & Capacity** - Limited (4 quota entries)
   - Missing: actual usage monitoring

4. **Email Capabilities** - Recently added (2024-2026)
   - Email addresses per instance: 100 (adjustable to 500)
   - Email domains per instance: 1 Connect + 100 custom
   - Concurrent active emails: 1000

### Monitoring Approach Comparison

| Feature | Quota Monitor | Utilization Monitor | Throttle Detection |
|---------|--------------|---------------------|-------------------|
| **Monitors** | Resource capacity | API rate limit utilization | Actual throttling events |
| **Checks** | Current count vs limit | Peak calls/sec vs limit/sec | Throttled call count |
| **Data Source** | Service Quotas API | Service Quotas + AWS/Usage | AWS/Connect CloudWatch |
| **Alert When** | Usage > 80% of limit | Peak rate ≥ 70% of limit | Throttling detected |
| **Granularity** | Point-in-time count | 1-minute peak (catches spikes) | Hourly aggregate |
| **Best For** | Capacity planning | Proactive rate limit management | Incident detection |
| **Examples** | "90/100 users" | "GetContactAttributes at 85% of 60/s" | "147 CreateUser calls throttled" |

**All three monitors are complementary:**
- Quota Monitor prevents running out of resources
- Utilization Monitor prevents throttling before it impacts callers (proactive)
- Throttle Detection catches throttling that slipped through (reactive)

## 📞 Support

**For issues:**
1. Check CloudWatch logs first
2. Verify IAM permissions
3. Confirm Connect instances are active
4. Review email subscription status

**Common Commands:**
```bash
# Check stack status
aws cloudformation describe-stacks --stack-name ConnectQuotaMonitor

# Manual quota monitor execution
aws lambda invoke --function-name ConnectQuotaMonitor-EnhancedConnectQuotaMonitor test.json

# Manual throttling monitor execution
aws lambda invoke --function-name ConnectAPIThrottlingMonitor test2.json

# View recent data
aws dynamodb scan --table-name ConnectQuotaMonitor --max-items 3
```

## 🗑️ Uninstall

### Remove Throttling Monitor
```bash
# Delete Lambda function
aws lambda delete-function --function-name ConnectAPIThrottlingMonitor

# Delete EventBridge rule
aws events remove-targets --rule ConnectAPIThrottlingMonitor-Schedule --ids 1
aws events delete-rule --name ConnectAPIThrottlingMonitor-Schedule

# Delete deployment package
rm -f api-throttling-monitor.zip
```

### Remove Quota Monitor
```bash
# Delete CloudFormation stack (removes all resources)
aws cloudformation delete-stack --stack-name ConnectQuotaMonitor

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete --stack-name ConnectQuotaMonitor
```

---

## 🎉 Ready to Deploy!

This enhanced Connect Quota Monitor provides enterprise-grade monitoring for your Amazon Connect environment with both capacity quota tracking and API throttling detection. Follow the deployment steps above to get started with comprehensive quota monitoring in minutes!

**Quick Start:**
```bash
# 1. Deploy main quota monitor (email is required)
./deploy.sh --email admin@company.com

# 2. Deploy API throttling monitor
./deploy_throttling_monitor.sh

# 3. Confirm email subscriptions
# 4. Wait for first execution (1 hour)
# 5. Check CloudWatch logs and email alerts
```

---

## 📄 License

This library is licensed under the MIT-0 License. See the LICENSE file.
