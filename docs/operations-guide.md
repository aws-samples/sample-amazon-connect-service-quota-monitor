# Operations Guide

Detailed reference for running and maintaining the Connect Quota Monitor and its live-refresh dashboard once they are deployed with Terraform. For the deploy steps themselves, see [../terraform/README.md](../terraform/README.md).

## How the schedule works

Both Lambda functions run on their own EventBridge rule, each controlled by the single `schedule_expression` Terraform variable (default `rate(1 hour)`):

| Function | Trigger | Writes to |
|----------|---------|-----------|
| quota-monitor | EventBridge, `schedule_expression` | S3 metrics bucket, DynamoDB (if `use_dynamodb = true`), SNS on breach |
| live-refresh | EventBridge, `schedule_expression` | dashboard S3 bucket (`latest.json`, `archive/`, `peaks/`, `index.html`) |

To change the schedule, edit `schedule_expression` in `terraform.tfvars` and run `terraform apply` again. Both functions share the variable; there is no separate knob per function today.

```hcl
schedule_expression = "rate(1 hour)"      # steady state
schedule_expression = "rate(15 minutes)"  # during a migration, higher risk period
schedule_expression = "rate(4 hours)"     # low activity periods
```

A tighter schedule raises Lambda invocation count and CloudWatch `GetMetricData` calls close to linearly; see the cost table in the root README and in `terraform/README.md`.

## Where the dashboard lives

The dashboard is not a public S3 website. With the default variables (`report_bucket_name` empty, `create_dashboard_bucket = true`), the live-refresh module creates a private S3 bucket, blocks all public access on it, and serves its contents through a CloudFront distribution using Origin Access Control. Nothing in the bucket is reachable directly; every request goes through CloudFront.

Get the URL after apply:

```bash
terraform output live_refresh_dashboard_url
# -> https://dXXXXXXXXXXXXX.cloudfront.net/
```

The Lambda overwrites `index.html` and `latest.json` at the bucket root on every scheduled run, so the same URL always shows the latest data. There is no need to re-deploy or clear a cache to see fresh numbers; CloudFront's default behavior for these objects still applies, so a very recent change can take a short time to show if it was cached at the edge.

## Access requires a Cognito login

If `enable_auth = true` (the default), both the dashboard and the `GET /quota` API sit behind Cognito. Opening the dashboard URL without a session redirects you to the Cognito hosted UI. After you log in, an httpOnly session cookie is set and the dashboard loads normally. There is no way to bypass this by hitting the CloudFront URL directly with a different tool such as `curl`, since the same login check runs at the edge for every request.

To create a test login and set its password, see the "Dashboard login gate" section of the root README and `terraform/modules/live-refresh/README.md`. In short:

```bash
eval "$(terraform output -raw live_refresh_test_user_password_command)"
# edit the placeholder password in that command before running it
```

Set `enable_auth = false` in `terraform.tfvars` if you want the dashboard open with no login, then `terraform apply`.

## Archives and history

Every scheduled live-refresh run writes:

1. **Hourly snapshot** to `archive/YYYY-MM-DD/HH-MM.json`
2. **Daily peak update** to `peaks/YYYY-MM-DD.json` (only overwritten if this hour's reading exceeds the day's previous peak)

### Looking back in time

The `/quota` API answers history queries directly:

```
GET /quota                -> latest snapshot (fresh collection on demand)
GET /quota?history=1h     -> last hour, minute-by-minute
GET /quota?history=1d     -> last 24 hours, hourly
GET /quota?history=7d     -> last 7 days, daily peaks
GET /quota?history=trend  -> last 30 days, daily peaks
```

These endpoints are behind the same Cognito authorizer as the dashboard when `enable_auth = true`; calling them without a valid Cognito access token returns an authorization error. `OPTIONS /quota` stays open for CORS preflight regardless of the auth setting.

The dashboard page itself makes one fetch to `?history=7d` for its trend chart. There is no client-side polling loop.

### Retention

Neither Terraform module sets an S3 Lifecycle rule today. If you want automatic cleanup, add a lifecycle configuration to the dashboard bucket (or your own `report_bucket_name` bucket) outside this module, for example:

- `archive/`: expire after 90 days
- `peaks/`: expire after 365 days
- `index.html` and `latest.json` at the bucket root: leave without an expiration; the Lambda overwrites them every run

## Peak tracking

Each scheduled live-refresh run:

1. Queries CloudWatch for `ConcurrentCalls`, `ConcurrentChats`, `ConcurrentTasks`, and per-API call counts (`AWS/Usage`)
2. Queries Service Quotas for current limits
3. Compares usage against those limits
4. If this run's reading exceeds today's previous peak, updates `peaks/YYYY-MM-DD.json`

The dashboard uses the peak files to show which hour of the day tends to run hottest and whether peaks are trending up week over week.

## Reading logs

Both Lambda functions write to their own CloudWatch log group, named from `name_prefix` and retained for `log_retention_days` (default 30):

```bash
aws logs tail /aws/lambda/$(terraform output -raw lambda_function_name) --follow
```

For the live-refresh function, read the log group name from its function ARN output:

```bash
terraform output live_refresh_function_arn
```

then tail `/aws/lambda/<function-name>` the same way, using the function name portion of that ARN.

## Verifying a deployment after apply

**Step 1.** Confirm the quota-monitor function ran at least once and wrote a report:

```bash
BUCKET=$(terraform output -raw s3_bucket_name)
aws s3 ls "s3://$BUCKET/connect-reports/" --recursive
```

**Step 2.** Confirm the SNS subscription. If you set `notification_email`, check that inbox and confirm it; alerts do not deliver until you do.

**Step 3.** Open the dashboard URL and log in:

```bash
terraform output live_refresh_dashboard_url
```

**Step 4.** After the schedule interval has passed once (one hour by default), confirm the dashboard bucket has archive and peak files:

```bash
DASHBOARD_BUCKET=$(terraform state show 'module.live_refresh[0].aws_s3_bucket.dashboard[0]' | grep -m1 '^\s*bucket ' | cut -d'"' -f2)
aws s3 ls "s3://$DASHBOARD_BUCKET/archive/"
aws s3 ls "s3://$DASHBOARD_BUCKET/peaks/"
```

The dashboard bucket name is not currently exposed as a root-level `terraform output`; the `dashboard_bucket_name` output exists only on the `live_refresh` module itself, which is why the command above reads it from state instead. Skip this step entirely if you supplied your own bucket through `report_bucket_name`; read that bucket directly.

## Accessibility

The dashboard HTML targets WCAG 2.1 AA:

- Color plus shape indicators, not color alone, for status
- Keyboard navigation (Tab, Enter, arrow keys)
- Screen reader labels (ARIA attributes)
- Respects browser zoom (rem-based sizing)
- Reduced-motion support
- Colorblind palette available with the `?colorblind` URL parameter
- Print stylesheet included
