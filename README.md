# Amazon Connect Service Quota Monitor

Get an email before your Amazon Connect quotas run out, instead of finding out when calls start failing.

## What it does

Amazon Connect enforces service quotas on almost everything: phone numbers, contact flows, queues, concurrent calls, and the rate at which you can call its APIs. When you hit one, the failure usually shows up in production as a dropped call or a throttled API, and by then it is a customer-facing incident.

This solution has two parts:

1. **Quota monitor.** A Lambda function runs on a schedule (hourly by default), discovers every Connect instance in your account and Region, measures how close each quota is to its limit, and emails you through SNS when something crosses a threshold you set. It reads usage from the Connect APIs, CloudWatch, and Service Quotas, and stores a JSON report of every run in S3 and (optionally) DynamoDB.
2. **Live-refresh dashboard.** A second Lambda runs on its own schedule and regenerates a consolidated HTML dashboard of the same quota data, plus a `/quota` API that answers history queries (`?history=1h`, `1d`, `7d`, `trend`) for the trend chart on the page.

You do not write any code; you set a few variables and deploy.

## Two ways to deploy

Pick one. Both deploy the same two Lambdas from the same `lambda_function.py`, so the monitoring behavior is identical. They differ in how the dashboard is exposed:

| | Terraform | CloudFormation |
|---|-----------|----------------|
| Entry point | `terraform apply` in [terraform/](terraform/) | `./deploy.sh` with the root [connect-quota-monitor-cfn.yaml](connect-quota-monitor-cfn.yaml) (monitor) and [live-refresh/template.yaml](live-refresh/template.yaml) (dashboard, SAM) |
| Dashboard exposure | Private S3 + CloudFront (Origin Access Control) with a **Cognito login gate** on the dashboard and its `/quota` API (Lambda@Edge) | API Gateway endpoint, **no built-in login gate** (the SAM template leaves auth to you: add an `AWS_IAM` or Cognito authorizer before exposing it) |
| Best for | A polished, customer-facing dashboard that must not be reachable without a login | Teams already standardized on CloudFormation, or who only want the monitor + alerts and will gate or skip the dashboard themselves |

If reaching the dashboard without a username and password would be a problem for you, use the Terraform path; its login gate is built in. The two deployment paths are independent; do not run both against the same account without changing names, or their resources will collide.

## Architecture

```
                    EventBridge (rate(1 hour))
                            |
                            v
   +---------------------------------------------+
   |  quota-monitor Lambda (python3.13)           |
   |  - Connect / Service Quotas / CloudWatch      |
   |  - reads/writes S3 + DynamoDB (optional)      |
   |  - publishes to SNS on threshold breach       |
   +---------------------------------------------+
         |            |              |
         v            v              v
   S3 (metrics)   DynamoDB      SNS -> email

                    EventBridge (rate(1 hour))
                            |
                            v
   +---------------------------------------------+
   |  live-refresh Lambda (python3.13, arm64)     |
   |  - CloudWatch + Service Quotas                |
   |  - writes latest.json / archive / index.html  |
   +---------------------------------------------+
         |                              ^
         v                              |
   S3 dashboard bucket (private,  API Gateway (prod stage,
   OAC only, no public access)    throttled) -> GET/OPTIONS /quota
         |                              ^
         v                              |
   CloudFront + Cognito login    Cognito authorizer
   gate (Lambda@Edge)                   |
         |                              |
         +-------- Browser --------+
```

The two Lambda functions are independent. `quota-monitor` does the account-wide quota scan and alerting; it has no dashboard. `live-refresh` produces the dashboard and its API; it does not send alerts. Setting `deploy_live_refresh = false` in Terraform skips the dashboard entirely and leaves you with alerting only.

## Prerequisites

| What | Version | How to check |
|------|---------|---------------|
| AWS account with credentials configured | any | `aws sts get-caller-identity` |
| Terraform | >= 1.6 | `terraform version` |
| git | any | `git --version` |
| AWS CLI | v2 | `aws --version` |
| An Amazon Connect instance | already created in your account | `aws connect list-instances --query "InstanceSummaryList[].{Id:Id,Alias:InstanceAlias}"` |

You need the Connect instance ID for `connect_instance_id` if you deploy the live-refresh dashboard. The quota-monitor Lambda does not take an instance ID; it discovers every instance in the account and Region on each run by calling `connect:ListInstances`.

If `enable_auth = true` (the default), the stack includes a Lambda@Edge function, which AWS only lets you create in `us-east-1`. Keep `region` set to `us-east-1` if you use the login gate; the quota monitor itself still works against a Connect instance in any Region.

## Deploy with Terraform

Full step-by-step instructions, including the two-phase apply required for the Cognito login gate, are in [terraform/README.md](terraform/README.md). The short version:

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-service-quota-monitor.git
cd sample-amazon-connect-service-quota-monitor/terraform

cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set connect_instance_id, notification_email, tags.owner

terraform init
terraform plan
terraform apply
# This apply is phase 1 of 2 and creates a login gate with no user yet. To
# reach the dashboard, continue to "Dashboard login gate (two-phase apply)" below.
```

`terraform apply` finishes with a list of outputs, including `live_refresh_dashboard_url` and `sns_topic_arn`.

If you set `notification_email`, check that inbox for an SNS subscription confirmation and click "Confirm subscription." Alerts do not deliver until you confirm it.

**Dashboard login gate (two-phase apply).** With `enable_auth = true` (the default), the dashboard and `GET /quota` sit behind Cognito. The Cognito callback URL needs the CloudFront domain, which does not exist until after an apply has run, so reaching the dashboard takes two applies total. The apply you already ran above is phase 1: `cloudfront_domain` was empty, so CloudFront came up with no edge auth attached yet. Read its domain, then run phase 2:

```bash
terraform output live_refresh_dashboard_url   # -> https://dXXXX.cloudfront.net/

# Phase 2: set cloudfront_domain in terraform.tfvars to that host (no scheme, no trailing slash)
terraform apply
```

Set a bootstrap login and its password (set `test_user_email` in `terraform.tfvars` first). The password must be at least 12 characters and include an uppercase letter, a lowercase letter, a number, and a symbol:

```bash
eval "$(terraform output -raw live_refresh_test_user_password_command)"
# edit the placeholder password in that command before running it
```

Log in at the Cognito hosted-UI prompt (it appears when you open `live_refresh_dashboard_url`) using the `test_user_email` you set and the password from the command above.

See [terraform/README.md](terraform/README.md) and [terraform/modules/live-refresh/README.md](terraform/modules/live-refresh/README.md) for the full detail on why the two phases are needed and what each one creates.

## Deploy with CloudFormation

The CloudFormation path uses `deploy.sh`, which packages the Lambda code (`lambda_function.py` + `quota_definitions.json`), uploads it to an S3 bucket it manages, and creates or updates the stack from [connect-quota-monitor-cfn.yaml](connect-quota-monitor-cfn.yaml). It needs the AWS CLI v2 and a bash shell.

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-service-quota-monitor.git
cd sample-amazon-connect-service-quota-monitor

# Deploy the monitor + alerts. --email subscribes an address to the alert topic.
./deploy.sh --email you@example.com --threshold 80

# Options: --stack-name NAME, --threshold 1-99, --runtime, --memory,
#          --vpc-id vpc-xxxx --subnet-ids subnet-a,subnet-b. Run ./deploy.sh --help.
```

Confirm the SNS subscription email when it arrives, or alerts will not deliver.

The dashboard is a separate SAM stack ([live-refresh/template.yaml](live-refresh/template.yaml)) deployed with the AWS SAM CLI:

```bash
cd live-refresh
sam build && sam deploy --guided   # answer the prompts; re-run without --guided after the first time
```

Important: the SAM dashboard stack exposes its `/quota` API through API Gateway with **no login gate**. If anyone with the URL should not be able to read your quota data, add an `AWS_IAM` or Cognito authorizer to `DashboardApi` in the template before you deploy it, or use the Terraform path, whose Cognito login gate is built in. This is the one real difference between the two deployment methods.

Tear the CloudFormation deployment down with `aws cloudformation delete-stack --stack-name <name>` (and `sam delete` for the dashboard stack). Empty the S3 buckets first if you want them removed.

## Where the reports and dashboard live

| Output | Where | How to read it |
|--------|-------|-----------------|
| Alert email | your inbox via SNS | confirm the subscription once, then alerts arrive by email |
| JSON quota reports | S3 metrics bucket (`s3_bucket_name` output), and DynamoDB if `use_dynamodb = true` | see the command below the table |
| Dashboard | private S3 bucket, served through CloudFront + Cognito | open `terraform output live_refresh_dashboard_url` in a browser and log in |
| `/quota` history API | API Gateway behind Cognito | `GET /quota`, `?history=1h`, `?history=1d`, `?history=7d`, `?history=trend` |

You have a few ways to get at the data, pick whichever fits:

- **Read the latest JSON report from S3** (needs your AWS credentials):

  ```bash
  aws s3 cp s3://$(terraform output -raw s3_bucket_name)/connect-reports/latest/latest-report.json - | jq .
  ```

- **Share the report as a time-limited link** (a presigned URL anyone can open in a browser for the duration, no AWS login required). The report bucket is private, so this is how you hand the report to someone without giving them account access:

  ```bash
  aws s3 presign s3://$(terraform output -raw s3_bucket_name)/connect-reports/latest/latest-report.json --expires-in 3600
  ```

  The link expires after the number of seconds you pass (3600 = one hour) and embeds a temporary credential, so treat it as sensitive and do not commit it.

- **Query it live through the API** (behind the Cognito login, same as the dashboard): `GET /quota`, plus `?history=1h|1d|7d|trend`.

- **Open the dashboard** for the human-readable view: `terraform output live_refresh_dashboard_url`, then log in.

Historical runs are also kept under `connect-reports/<date>/` in the same bucket, and in DynamoDB when `use_dynamodb = true`.

## Teardown

```bash
cd terraform
terraform destroy
```

By default `force_destroy = false`, so `terraform destroy` fails rather than silently deleting buckets that still hold objects (the metrics bucket, the access-log bucket, and the dashboard bucket). Set `force_destroy = true` in `terraform.tfvars` and re-run `terraform destroy` only when you are certain you do not need the stored history. `terraform destroy` does not touch a bucket you supplied yourself through `report_bucket_name`, since Terraform never created it.

If you deployed with `enable_auth = true`, `terraform destroy` can fail once on the edge-auth Lambda@Edge function with `InvalidParameterValueException: ... replicated function`. CloudFront removes the edge replicas asynchronously, which can take tens of minutes up to a few hours. Wait, then re-run `terraform destroy`. See [terraform/README.md](terraform/README.md) for the full explanation.

## Cost

Rough monthly cost at the default `rate(1 hour)` schedule; see [terraform/README.md](terraform/README.md) for the full breakdown by module.

| Component | Pilot (1 instance) | Production (larger account) |
|-----------|---------------------|-------------------------------|
| Lambda (quota-monitor + live-refresh, hourly) | about $1 to $3 | up to about $7 |
| DynamoDB (on-demand, default on) | $0.25 to $2.00 | about $3 |
| S3 (metrics, access logs, dashboard) | $0.10 to $0.50 | about $1 |
| CloudFront + Cognito | a few cents at low traffic | about $1 to $2 |
| SNS (email) | about $0.10 | about $0.10 |
| CloudWatch (alarms and logs) | $0.50 to $1.00 | about $1.50 |
| KMS (3 keys, quota-monitor module) | about $3 (3 x $1/mo per key, flat regardless of traffic) | about $3 |
| **Total** | **about $6 to $10** | **about $15 to $20** |

KMS is the one line item that does not scale down with traffic. Set `use_dynamodb = false` and `use_s3_storage = false` if you only want alerting and no stored history. Cost grows mainly with schedule frequency: a tighter schedule than `rate(1 hour)` multiplies Lambda invocations and CloudWatch `GetMetricData` calls close to linearly. It does not spike nonlinearly at any scale covered by this design.

## Known limitations

- The live-refresh module does not create the S3 bucket you point it at with `report_bucket_name`. Leave that variable empty (the default) and the module creates and manages its own private dashboard bucket instead.
- Single account, single region. Nothing in this repository assumes cross-account or cross-region deployment.
- No remote Terraform backend is configured. State defaults to the local `terraform.tfstate` file in `terraform/` unless you add your own `backend` block. Start from [terraform/backend.tf.example](terraform/backend.tf.example) to enable remote state.
- No AWS WAF or CloudFront access logging is configured on the dashboard distribution. The login gate and stage throttling are in place; a WAF rate rule and access logs are reasonable additions before high-traffic production use.
- The login gate (`enable_auth = true`, the default) requires `region = us-east-1`, because it uses Lambda@Edge, which AWS only creates in us-east-1. Terraform now fails fast with a clear message if you set another region with auth on. To run the dashboard in another region, set `enable_auth = false` (the dashboard then has no Cognito login).
- Monitor run time grows with the number of Connect instances and quotas. Each instance is scanned serially, and the Lambda runs with `reserved_concurrent_executions = 1` and no retries, so on an account with roughly 30+ active instances the run can approach the Lambda timeout (default 600s, max 900s). If you have many instances, raise `lambda_timeout`, and watch the new Lambda `Duration` and `Throttles` alarms. Very large estates are not yet parallelized.
- First-run dashboard: the dashboard shows a "being generated" placeholder until the first scheduled `live-refresh` run writes real data (up to one schedule interval, `rate(1 hour)` by default). On an instance with thousands of contact flows, the resource crawl can approach the `live-refresh` Lambda timeout; if the dashboard stays on the placeholder, raise that Lambda's timeout.
- Two-phase apply and the login gate: between phase 1 and phase 2, the dashboard shell is reachable without a login (its `/quota` API is still gated). Complete phase 2 promptly, or leave `enable_auth = false` if you do not want the gate at all.
- The Cognito app-client secret is baked into the Lambda@Edge bundle (Lambda@Edge cannot use environment variables) and, with local state, is stored in `terraform.tfstate`. Use an encrypted remote backend (see [terraform/backend.tf.example](terraform/backend.tf.example)) and restrict `lambda:GetFunction` on any deploy role.
- A logged-in dashboard session lasts 60 minutes (the Cognito id-token lifetime). After it expires, an in-page history fetch fails once; reload the page to log in again.

### Tuning knobs

| Variable | Default | Effect |
|----------|---------|--------|
| `lambda_timeout` | 600 | Monitor Lambda timeout in seconds (60-900). Raise for many instances. |
| `dynamodb_ttl_days` | 90 | Auto-expiry for stored DynamoDB records. Set to 0 to keep records forever. |
| `send_allclear_heartbeat` | false | When true, a healthy run with no violations still publishes an "all clear" SNS message, so silence is not mistaken for a working monitor. |
| `threshold_percentage` | 80 | Utilization percentage that counts as a violation. |

## Documentation

| Topic | Link |
|-------|------|
| Terraform deploy, all variables, teardown, two-phase auth apply | [terraform/README.md](terraform/README.md) |
| CloudFormation monitor template | [connect-quota-monitor-cfn.yaml](connect-quota-monitor-cfn.yaml) |
| CloudFormation deploy script (`--help` for options) | [deploy.sh](deploy.sh) |
| SAM dashboard template (no built-in login gate) | [live-refresh/template.yaml](live-refresh/template.yaml) |
| quota-monitor module detail | [terraform/modules/quota-monitor/README.md](terraform/modules/quota-monitor/README.md) |
| live-refresh module detail, login gate internals | [terraform/modules/live-refresh/README.md](terraform/modules/live-refresh/README.md) |
| Operations guide (schedule, history queries, logs) | [docs/operations-guide.md](docs/operations-guide.md) |
| Reports and dashboards, with screenshots | [docs/reports-and-dashboards.md](docs/reports-and-dashboards.md) |
| IAM permissions required | [iam/README.md](iam/README.md) |
| Live-refresh component detail | [live-refresh/README.md](live-refresh/README.md) |
| Local report generation (optional CLI tool, no deploy, no auth) | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |

## Common issues

| Symptom | Likely cause and fix |
|---------|----------------------|
| No alert emails | You have not confirmed the SNS subscription. Check your inbox and click "Confirm subscription." |
| `GET /quota` returns HTTP 503 | You set `report_bucket_name` to a bucket the module does not manage and it does not exist, or you have not applied at all yet. Leave `report_bucket_name` empty to let the module create its own dashboard bucket. |
| Dashboard login loop / callback error | You are on the phase-1 apply (`cloudfront_domain` empty) but trying to log in already. Finish phase 2 first: set `cloudfront_domain` to the CloudFront host and `terraform apply` again. |
| Rate quotas all read 0% | The rate metrics only appear when those APIs are being called. An idle instance reads zero, which is correct. |
| "Access Denied" in the Lambda logs | The execution role is missing a permission. See [iam/README.md](iam/README.md) and the IAM section of each module README. |
| Too many alerts | Raise `threshold_percentage` in `terraform.tfvars` (for example to 90) and `terraform apply` again. |
| `terraform destroy` fails on a bucket | `force_destroy = false` (the default) refuses to delete a non-empty bucket. Empty it yourself or set `force_destroy = true` and re-run. |
| `terraform destroy` fails with `InvalidParameterValueException: ... replicated function` | `enable_auth = true` deployed a Lambda@Edge function, and CloudFront has not finished removing its edge replicas yet. Wait (tens of minutes up to a few hours) and re-run `terraform destroy`. |

## License

MIT-0. No attribution required. See [LICENSE](LICENSE).
