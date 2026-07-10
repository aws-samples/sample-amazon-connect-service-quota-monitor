# Live Refresh Backend

CloudWatch-powered Lambda + API Gateway that serves real-time metrics to the Connect Operations Dashboard.

## Architecture

```
Dashboard (HTML)  →  API Gateway  →  Lambda  →  CloudWatch + Service Quotas
      ↑                                              ↓
      └──────── JSON response (every 60s) ──────────┘
```

## Deploy

```bash
# Prerequisites: AWS SAM CLI
pip install aws-sam-cli

# Build and deploy
cd live-refresh/
sam build
sam deploy --guided \
    --parameter-overrides \
        ConnectInstanceId=587c546e-2328-4c36-baa2-37eaf4749631
```

SAM will output your API endpoint URL. Pass it to the mapper:

```bash
python connect-resource-mapper.py \
    --instance-id 587c546e-2328-4c36-baa2-37eaf4749631 \
    --region us-east-1 \
    --line-config line-config.json \
    --live-endpoint https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/prod/metrics \
    --output-dir ./output
```

## How It Works

1. Dashboard loads with static data (from the mapper's last run)
2. On page load, JavaScript starts polling the live endpoint every 60s
3. Lambda queries CloudWatch for:
   - `AWS/Usage` → per-API call counts (current TPS)
   - `AWS/Connect` → ConcurrentCalls, CallsIncoming (volume)
   - `ServiceQuotas` → current limits (for utilization %)
4. Dashboard merges live data into its state and re-renders
5. Polling pauses when the browser tab is hidden (saves costs)

## API

```
GET /metrics?view=today   → Hourly granularity, midnight to now
GET /metrics?view=hour    → 1-minute granularity, current hour
GET /metrics?view=week    → Daily granularity, last 7 days
```

## Configuration

Line config can be provided via:
- `LINE_CONFIG_JSON` env var (inline, for small configs)
- `LINE_CONFIG_S3_BUCKET` + `LINE_CONFIG_S3_KEY` (S3, for larger configs)

## Cost

- Lambda: ~$0.01/day (720 invocations × 256MB × 2s avg)
- API Gateway: ~$0.003/day (720 requests)
- CloudWatch GetMetricData: ~$0.07/day (720 × 13 metrics)
- **Total: ~$0.08/day ($2.50/month)**

## Required IAM Permissions

```yaml
- cloudwatch:GetMetricData
- cloudwatch:GetMetricStatistics
- servicequotas:ListServiceQuotas
- servicequotas:GetServiceQuota
- s3:GetObject (if using S3 config)
```
