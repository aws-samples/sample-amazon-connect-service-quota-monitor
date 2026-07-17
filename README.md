# Amazon Connect Service Quota Monitor

**Get an email alert before your Connect quotas breach — not after calls start dropping.**

> **See what the dashboard looks like:** Open [docs/screenshots/sample-dashboard.html](docs/screenshots/sample-dashboard.html) in your browser for a live interactive preview with sample data.

---

## What this does

Monitors 70+ Amazon Connect service quotas continuously. When any quota exceeds 80% utilization, you get a consolidated email alert with:

- Which quotas are approaching their limits
- Current utilization percentage
- Which Connect instance is affected
- Recommended actions

One CloudFormation deploy. No code to write. Alerts start flowing within minutes.

---

## Quick start — Deploy the monitor (2 minutes)

> **Detailed step-by-step guide:** [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)

### Prerequisites

- AWS account with an Amazon Connect instance
- An email address to receive alerts
- AWS CLI configured ([setup guide](docs/GETTING_STARTED.md#install-aws-cli-and-configure-credentials))

### Deploy

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-service-quota-monitor.git
cd sample-amazon-connect-service-quota-monitor

aws cloudformation deploy \
  --template-file connect-quota-monitor-cfn.yaml \
  --stack-name connect-quota-monitor \
  --parameter-overrides \
    NotificationEmail=your-team@company.com \
    ThresholdPercentage=80 \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
```

**Windows (PowerShell):**
```powershell
aws cloudformation deploy `
  --template-file connect-quota-monitor-cfn.yaml `
  --stack-name connect-quota-monitor `
  --parameter-overrides `
    NotificationEmail=your-team@company.com `
    ThresholdPercentage=80 `
  --capabilities CAPABILITY_IAM `
  --region us-east-1
```

### Confirm your email

After deploying, **check your inbox** for an SNS subscription confirmation email. Click "Confirm subscription" — alerts won't be delivered until you confirm.

### What happens next

- A Lambda runs every hour (configurable) and checks all Connect quotas
- If any quota exceeds 80% utilization → you get one consolidated email per instance
- The email lists all breaching quotas with current values and limits

---

## Configuration options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `NotificationEmail` | *(required)* | Email address for quota breach alerts |
| `ThresholdPercentage` | `80` | Alert when quotas exceed this % (0-100) |
| `ScheduleExpression` | `rate(1 hour)` | How often to check (cron or rate) |
| `LambdaTimeout` | `300` | Lambda timeout in seconds |
| `LambdaMemory` | `256` | Lambda memory in MB |
| `UseS3Storage` | `true` | Store historical data in S3 |
| `UseDynamoDBStorage` | `false` | Store data in DynamoDB (optional) |

### Change the threshold or schedule after deployment

```bash
aws cloudformation update-stack \
  --stack-name connect-quota-monitor \
  --use-previous-template \
  --parameter-overrides \
    NotificationEmail=your-team@company.com \
    ThresholdPercentage=70 \
    ScheduleExpression="rate(30 minutes)" \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
```

---

## Optional: Generate an on-demand dashboard

Beyond the automated alerts, you can run a one-time scan to get an interactive HTML dashboard showing your full Connect topology and quota utilization:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 connect-resource-mapper.py \
  --instance-id YOUR_INSTANCE_ID \
  --region us-east-1 \
  --output-dir ./output

open ./output/connect-dashboard.html
```

This generates a self-contained HTML file (no server needed) with:
- Capacity meters for every quota
- Resource topology (phone numbers → flows → Lambdas → quotas)
- Migration wave planner
- Peak hour trends

> **Full setup guide:** [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)

---

## What's in the box

| Component | Description | Guide |
|-----------|-------------|-------|
| **Quota Monitor** (Lambda + CFN) | Automated alerts when quotas exceed threshold | You're here |
| **Resource Mapper** (CLI) | On-demand scan → interactive HTML dashboard | [Above](#optional-generate-an-on-demand-dashboard) |
| **Live Refresh** (Lambda) | Hourly dashboard refresh, S3 hosting | [live-refresh/README.md](live-refresh/README.md) |

---

## Architecture

```
CloudFormation deploys:
  EventBridge (hourly) → Lambda → ServiceQuotas + CloudWatch + Connect APIs
                            │
                            ├→ Quota > 80%? → SNS → Email alert
                            ├→ S3: quota-history/ (historical data)
                            └→ CloudWatch Metrics (custom namespace)
```

For the on-demand dashboard:
```
python3 connect-resource-mapper.py → Phone Numbers → Flows → Lambdas → Quotas
                                       └→ connect-dashboard.html (open in browser)
```

---

## Documentation

| Topic | Link |
|-------|------|
| Full getting started (all platforms) | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| IAM permissions required | [iam/README.md](iam/README.md) |
| Operations guide (archives, peaks) | [docs/operations-guide.md](docs/operations-guide.md) |

---

## Common issues

| Symptom | Fix |
|---------|-----|
| No alert emails received | Check your inbox for the SNS confirmation email — click "Confirm subscription" |
| All quotas show 0% | Run during business hours when calls are active |
| "Access Denied" | Check IAM permissions — [iam/README.md](iam/README.md) |
| Alert threshold too noisy | Increase `ThresholdPercentage` to 90 |

---

## Cleanup

To remove the monitor and stop alerts:

```bash
aws cloudformation delete-stack --stack-name connect-quota-monitor --region us-east-1
```

---

## License

MIT-0 (No attribution required). See [LICENSE](LICENSE).
