# Live Refresh Backend

CloudWatch-powered Lambda + API Gateway that serves real-time metrics to the Connect Operations Dashboard, and regenerates that dashboard on a schedule.

This directory holds the Lambda source (`lambda_function.py`, `mapper_bridge.py`) only. It is not deployed by any script in this directory. It is packaged and deployed by the `live-refresh` Terraform module at `../terraform/modules/live-refresh/`, which is wired into the root Terraform configuration at `../terraform/`. See [../terraform/README.md](../terraform/README.md) for the actual deploy steps.

## Architecture

```
Dashboard (HTML)  <-  CloudFront + Cognito login  <-  private S3 bucket (OAC)
                                                            ^
                                                            | scheduled write
Browser fetch (7-day trend)  ->  API Gateway (Cognito authorizer)  ->  Lambda  ->  CloudWatch + Service Quotas
```

- The scheduled EventBridge rule (`schedule_expression`, default `rate(1 hour)`) invokes this Lambda. That run writes `latest.json`, appends to `archive/` and `peaks/`, and regenerates `index.html`, the dashboard page, into the report bucket.
- The API Gateway path (`GET /quota`) invokes the same Lambda on demand, for the dashboard's 7-day trend chart and for anyone querying history directly.
- The dashboard and the API are both fronted by CloudFront and Cognito when the Terraform module's `enable_auth` variable is `true` (the default). See "Deployment" below.

## Deployment

There is no `sam build` or `sam deploy` step for this component; the SAM template that used to live in this directory has been removed. Deployment is entirely through Terraform, from the `terraform/` directory at the repository root:

```bash
cd ../terraform
cp terraform.tfvars.example terraform.tfvars
# set connect_instance_id and other variables
terraform init
terraform apply
```

The Terraform module packages `lambda_function.py` and `mapper_bridge.py` from this directory, plus `connect-resource-mapper.py` and `consolidated_report.py` from the repository root, flatly into the same Lambda deployment zip at plan time (via `data.archive_file`). All four files land in `/var/task` alongside each other, which is what lets `mapper_bridge.py` import `connect-resource-mapper.py` at runtime and produce the full consolidated-report dashboard on the scheduled run rather than a stripped-down one. There is no separate build step and no artifact to upload by hand.

The module also builds the `/quota` API endpoint URL itself from the deployed API Gateway REST API ID, region, and stage, and feeds it back into the Lambda's `API_ENDPOINT` environment variable automatically. Unlike the removed SAM template, this does not require a two-pass deploy to resolve a circular dependency; Terraform computes the URL from values that do not depend on the Lambda function.

A separate two-phase apply is required only for the Cognito login gate (`enable_auth = true`, the default), because the Cognito callback URL needs the CloudFront domain, which does not exist until the first apply. See "Login gate" in [../terraform/modules/live-refresh/README.md](../terraform/modules/live-refresh/README.md) and the root [README.md](../README.md) for that sequence.

`connect-resource-mapper.py` also exists as a standalone CLI tool at the repository root, run directly with `python3 connect-resource-mapper.py --instance-id ...` to produce a one-off topology report and dashboard on your own machine. That standalone use does not go through this Lambda or this endpoint; it is a separate, unrelated invocation of the same Python module.

## How It Works

The Lambda runs on a schedule (`schedule_expression`, default `rate(1 hour)`). On each scheduled run it:

1. Queries CloudWatch and Service Quotas:
   - `AWS/Usage` for per-API call counts (current TPS)
   - `AWS/Connect` for `ConcurrentCalls`, `CallsIncoming` (volume)
   - `ServiceQuotas` for current limits (for utilization percentage)
2. Writes the snapshot to `latest.json` and appends to the `archive/` and `peaks/` history in the report bucket.
3. Regenerates `index.html` (the dashboard page) and writes it to the bucket root, so the page you open is as fresh as the last scheduled run.

When you open the dashboard page, its 7-day trend chart makes a single browser fetch to `GET /quota?history=7d`. There is no client-side polling loop. A `GET /quota` request with no query string performs a fresh collection on demand rather than reading a cached value.

## Fronted by CloudFront and Cognito

The dashboard is not served as a public S3 website. The Terraform module puts a private S3 bucket behind a CloudFront distribution using Origin Access Control; nothing in the bucket is reachable except through CloudFront. When `enable_auth = true` (the default), CloudFront also runs a Lambda@Edge viewer-request check that redirects an unauthenticated visitor to a Cognito hosted UI login page and sets a session cookie after login. The `GET /quota` API carries its own `COGNITO_USER_POOLS` authorizer, so a request without a valid Cognito token is rejected there too; `OPTIONS /quota` stays open for CORS preflight regardless of the auth setting.

Set `enable_auth = false` in `terraform.tfvars` to leave the dashboard and API open with no login.

## API

```
GET /quota                -> latest snapshot (fresh collection)
GET /quota?history=1h     -> last hour, minute-by-minute (from the archive)
GET /quota?history=1d     -> last 24 hours, hourly
GET /quota?history=7d     -> last 7 days, daily peaks
GET /quota?history=trend  -> last 30 days, daily peaks
```

The `prod` stage throttles requests (rate 10 requests/second, burst 20) regardless of the auth setting, so a runaway client cannot drive up your CloudWatch bill. There is no API key or usage plan; an API key embedded in static JavaScript would not protect anything a browser can already see, so authentication is handled by the Cognito authorizer described above instead, not by a key.

## Configuration

Line config can be provided via:
- `LINE_CONFIG_JSON` env var (inline, for small configs), set from the Terraform variable `line_config_json`
- `LINE_CONFIG_S3_BUCKET` + `LINE_CONFIG_S3_KEY` (S3, for larger configs), set from `line_config_s3_bucket` and `line_config_s3_key`

Provide one or the other, not both.

## Cost

Costs scale with `schedule_expression`. At the default `rate(1 hour)` (24 runs per day, 512 MB equivalent workload, roughly 2 seconds per run):

- Lambda: a few cents per month
- API Gateway: negligible; only browser requests, a handful per dashboard view
- CloudWatch `GetMetricData`: the largest line item. Each run issues a few `get_metric_data` calls (concurrent metrics plus one API-usage query); the total metric count depends on how many APIs and lines you configure, billed at $0.01 per 1,000 metrics requested. At the default hourly schedule this is a few cents per month.
- CloudFront and Cognito: a few cents per month at low dashboard traffic.

**Total: well under $1/month for this module alone at the default schedule.** A tighter schedule, for example `rate(1 minute)`, raises the CloudWatch and Lambda cost roughly in proportion to run count. See the root [README.md](../README.md) and [../terraform/README.md](../terraform/README.md) for the combined cost of this module plus the quota-monitor module.

## Required IAM Permissions

```yaml
- cloudwatch:GetMetricData
- cloudwatch:GetMetricStatistics
- servicequotas:ListServiceQuotas
- s3:PutObject    # write latest.json, archive/, peaks/, index.html (report bucket)
- s3:GetObject    # read line config, latest.json, and peak history (report bucket)
- s3:ListBucket   # list archive/ entries for GET /quota?history=1h|1d (report bucket)
```

These are granted by the Terraform module's IAM policy, scoped to the dashboard bucket it manages (or to the bucket you supply through `report_bucket_name`). The line-config `s3:GetObject` permission is only needed if you supply config through S3 rather than inline.

## Known limitations

`mapper_bridge.py` looks for `connect-resource-mapper.py` in its own directory first, and one directory above as a fallback for local/CLI use where the two files are not co-located. The Terraform module bundles `connect-resource-mapper.py` and `consolidated_report.py` into the same directory as `mapper_bridge.py` inside the deployment zip, so the same-directory case is the one that applies to the deployed Lambda, and the import succeeds there. `lambda_function.py` still wraps the import in a `try`/`except ImportError` and falls back to a lightweight generated dashboard if the import ever fails for another reason (for example, if you repackage this Lambda outside the Terraform module without also bundling those two files).
