# Amazon Connect Service Quota Monitor

Get an email before your Amazon Connect quotas run out, instead of finding out when calls start failing.

## What it does

Amazon Connect enforces service quotas on almost everything: phone numbers, contact flows, queues, concurrent calls, and the rate at which you can call its APIs. When you hit one, the failure usually shows up in production as a dropped call or a throttled API, and by then it is a customer-facing incident.

This tool watches those quotas for you. A Lambda function runs on a schedule (hourly by default), discovers every Connect instance in your account and Region, measures how close each quota is to its limit, and emails you when something crosses a threshold you set. It reads usage from the Connect APIs, CloudWatch, and Service Quotas, and it stores a JSON report of every run so you have a record over time.

You deploy it once with a single command. There is no code to write.

## The pieces, and what each one produces

The repository looks like several scripts, so here is what each one is for and what it gives you. You do not run them together. The monitor stands on its own. The rest are optional tools you run when you want a visual snapshot.

| You run | What it is | What you get | Where it lands |
|---------|-----------|--------------|----------------|
| `./deploy.sh` | The monitor. CloudFormation stack plus a scheduled Lambda. This is the product. | A JSON report every run, and an email when a quota crosses your threshold. No HTML. | S3 (`connect-reports/...`) and your inbox |
| `quota_report_to_html.py` | A small converter | An HTML table of every quota with a utilization bar, built from a monitor JSON report | A local `.html` file you open in a browser |
| `connect-resource-mapper.py` | An on-demand deep scan of one instance | An interactive dashboard plus a consolidated report, and two JSON files (the full resource map and a quota impact model) | Local files: `connect-dashboard.html`, `connect-api-report.html`, and the two `.json` files |
| `live-refresh/` stack | An optional second stack | A dashboard that refreshes on a schedule and is served from an S3 website | S3 static website |

A few things follow from that table:

The deployed monitor writes JSON, not HTML. If you want a page you can look at, you run `quota_report_to_html.py` on a stored report for the quota table, or `connect-resource-mapper.py` for the topology dashboard. The `connect-resource-mapper.py` script uses two helper modules, `dashboard_v4.py` and `consolidated_report.py`, to render its HTML. You never call those directly.

The monitor and the mapper share the same list of quota definitions (`quota_definitions.json`) but run independently. Nothing about alerting depends on the dashboard tools, and nothing about the dashboard depends on the monitor being deployed.

## Deploy the monitor

You need the AWS CLI configured, plus `bash` and `zip` on your machine. See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) if you are setting up the CLI for the first time.

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-service-quota-monitor.git
cd sample-amazon-connect-service-quota-monitor

./deploy.sh --email your-team@example.com
```

Use `deploy.sh`, not a plain `aws cloudformation deploy`. The Lambda code plus its quota definitions are larger than CloudFormation's inline code limit, so the template ships a tiny placeholder and `deploy.sh` uploads the real code through S3 after the stack is created. A raw `cloudformation deploy` would leave you with the placeholder and no monitoring.

Common options:

```bash
./deploy.sh --email your-team@example.com --threshold 80 --schedule "rate(1 hour)"
```

Run `./deploy.sh --help` for the full list.

## Confirm your email

After the first deploy, check your inbox for an SNS subscription confirmation and click "Confirm subscription". Alerts are not delivered until you confirm.

## What happens on each run

The Lambda checks every quota it can measure and emails you one consolidated message per instance if anything is at or above your threshold. The email lists the quotas that are close, their current usage, their limits, and the instance affected. If nothing is close, it stays quiet.

## How it finds your instances

You do not give the monitor an instance ID. On each run it calls `connect:ListInstances` to find every Connect instance in the account and Region, then reads each instance's resources (queues, flows, users, Lambda associations, and so on) with instance-scoped `List` and `Describe` calls, and pulls rate and concurrency usage from CloudWatch and Service Quotas. That is why no instance ID is configured, and why a hardcoded one is treated as a mistake. The read-only permissions it needs are in [iam/README.md](iam/README.md); the key one for discovery is `connect:ListInstances`.

The two manual tools do target one instance. Find an instance ID in the Connect console URL (`.../instance/<INSTANCE_ID>/...`) or with:

```bash
aws connect list-instances --query "InstanceSummaryList[].{Id:Id,Alias:InstanceAlias}"
```

Pass it as `--instance-id` to the mapper, or as the `ConnectInstanceId` parameter to the live-refresh stack.

## Where the reports go

The email is the alert. The JSON reports are the record. With S3 storage on (the default), every run writes to the metrics bucket, whose name is the `S3BucketName` stack output:

| Object | Cadence |
|--------|---------|
| `connect-reports/latest/latest-report.json` | Overwritten every run. Always the newest. |
| `connect-reports/<date>/report_<HHMMSS>.json` | One per run, kept as history. |
| `connect-account-metrics/latest/account-metrics.json` (and dated copies) | Account-level quotas |
| `connect-metrics/latest/<instance-id>.json` (and dated copies) | Per-instance quotas |

Read them from the CLI:

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name ConnectQuotaMonitor \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" --output text)

aws s3 cp "s3://$BUCKET/connect-reports/latest/latest-report.json" - | jq .
aws s3 ls "s3://$BUCKET/connect-reports/" --recursive
```

The `latest/` objects are overwritten at the schedule cadence (hourly by default). The dated reports pile up as history. If you turn on DynamoDB storage, the same records are written there too.

To turn a stored report into a page you can read:

```bash
aws s3 cp "s3://$BUCKET/connect-reports/latest/latest-report.json" ./report.json
python3 quota_report_to_html.py ./report.json ./quota-report.html
open ./quota-report.html
```

## Configuration

These are `deploy.sh` flags, and the same names exist as CloudFormation parameters:

| Setting | Default | Meaning |
|---------|---------|---------|
| `--email` | required | Where breach alerts go |
| `--threshold` | `80` | Alert when a quota is at or above this percent |
| `--schedule` | `rate(1 hour)` | How often the Lambda runs (a `rate()` or `cron()` expression) |
| `UseS3Storage` | `true` | Keep JSON reports in S3 |
| `UseDynamoDBStorage` | `false` | Also keep records in DynamoDB |

To change the threshold or schedule later, run `deploy.sh` again with the new values, or update the CloudFormation stack directly.

## Optional: the on-demand dashboard

When you want to see your whole topology and where each quota sits, scan one instance and open the dashboard it writes:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 connect-resource-mapper.py \
  --instance-id YOUR_INSTANCE_ID \
  --region us-east-1 \
  --output-dir ./output

open ./output/connect-dashboard.html
```

The output is a self-contained HTML file with capacity meters per quota, the resource topology (phone numbers, flows, Lambdas, quotas), and a migration wave planner. It needs no server. Alongside it you get `connect-api-report.html` and the raw JSON (`connect-resource-map.json`, `connect-quota-impact-model.json`).

Some of the per-line volume figures in that dashboard are estimates derived from resource counts, not live traffic. The quota utilization, API rate usage, and capacity numbers are measured from CloudWatch and Service Quotas.

For screenshots of all three views (the quota limit report, the operations dashboard, and the consolidated API report) and a walkthrough of what each one shows, see [docs/reports-and-dashboards.md](docs/reports-and-dashboards.md).

## Architecture

The monitor:

```
CloudFormation deploys:
  EventBridge (schedule) -> Lambda -> Connect APIs + CloudWatch + Service Quotas
                             |
                             |-- quota at/over threshold? -> SNS -> email
                             |-- JSON reports -> S3 (connect-reports/, connect-metrics/)
                             \-- optional records -> DynamoDB
```

The on-demand dashboard:

```
connect-resource-mapper.py -> Connect APIs (phone numbers, flows, Lambdas, quotas)
                              \-> connect-dashboard.html + connect-api-report.html
```

## Documentation

| Topic | Link |
|-------|------|
| Full getting started, all platforms | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| Reports and dashboards, with screenshots | [docs/reports-and-dashboards.md](docs/reports-and-dashboards.md) |
| IAM permissions required | [iam/README.md](iam/README.md) |
| Operations guide (archives, peaks) | [docs/operations-guide.md](docs/operations-guide.md) |
| Scheduled dashboard refresh stack | [live-refresh/README.md](live-refresh/README.md) |

## Cost

Rough monthly cost, which scales mostly with how many instances and quotas you have:

| Component | Estimate |
|-----------|----------|
| Lambda (hourly) | $1 to $3 |
| S3 (report history) | $0.10 to $0.50 |
| DynamoDB (if enabled, on-demand) | $0.25 to $2.00 |
| SNS (email) | about $0.10 |
| CloudWatch (alarms and logs) | $0.50 to $1.00 |
| Pilot total | about $3 |
| Larger production account | about $8 |

## Common issues

| Symptom | Likely cause and fix |
|---------|----------------------|
| No alert emails | You have not confirmed the SNS subscription. Check your inbox and click "Confirm subscription". |
| The Lambda just logs "run deploy.sh" | You deployed the CloudFormation template directly. Run `./deploy.sh` so the real code is uploaded. |
| Rate quotas all read 0% | The rate metrics only appear when those APIs are being called. An idle instance reads zero, which is correct. |
| "Access Denied" in the logs | The execution role is missing a permission. See [iam/README.md](iam/README.md). |
| Too many alerts | Raise `--threshold` (for example to 90). |

## Cleanup

```bash
aws cloudformation delete-stack --stack-name ConnectQuotaMonitor --region us-east-1
```

The metrics and report buckets are retained on stack deletion so you do not lose history by accident. Empty and delete them by hand when you are done with the data.

## License

MIT-0. No attribution required. See [LICENSE](LICENSE).
