# Connect Quota Monitor, Terraform port

This is one of the two deployment methods for the solution (the other is the
CloudFormation/SAM path driven by `deploy.sh`; see the root README). It was
ported from the two IaC stacks that still live at the repository root:

- `modules/quota-monitor` (ported from [../connect-quota-monitor-cfn.yaml](../connect-quota-monitor-cfn.yaml))
- `modules/live-refresh` (ported from [../live-refresh/template.yaml](../live-refresh/template.yaml))

Each module's README has a "Parity notes" section recording the deliberate
fixes and improvements made during that port.

## Purpose

Monitors Amazon Connect service quota utilization on a schedule, alerts via
SNS when usage crosses a threshold, and (optionally) serves a live dashboard
of the same metrics through an API Gateway endpoint. See the root repository
README for what the monitoring code itself does; this directory only covers
how it is deployed.

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
   S3 (metrics)   DynamoDB      SNS (+ email)
   S3 (access     (metrics)     -> CloudWatch alarms
    logs)                          (errors, duration)
                                    -> same SNS topic

                    EventBridge (rate(1 hour))
                            |
                            v
   +---------------------------------------------+
   |  live-refresh Lambda (python3.13, arm64)      |
   |  - CloudWatch + Service Quotas                |
   |  - full resource crawl on the scheduled run   |
   |  - writes latest.json / archive / index.html  |
   +---------------------------------------------+
         |                                    ^
         v (scheduled writes)                 | GET /quota (Cognito authorizer)
   private S3 dashboard bucket          API Gateway (prod stage, throttled)
         ^                                     ^
         | OAC (read)                          |
   CloudFront distribution  <----------  Browser (dashboard + 7-day trend fetch)
         ^
         | viewer-request
   Lambda@Edge -> Cognito hosted-UI login (when enable_auth = true)
```

By default (`report_bucket_name` empty) the module creates a private S3 bucket,
serves it over HTTPS through CloudFront with Origin Access Control (no public
bucket), and gates both the dashboard and `GET /quota` behind Cognito when
`enable_auth` is true (the default). Provide `report_bucket_name` to use an
existing bucket instead.

The two Lambda functions are independent. `quota-monitor` does the
account-wide quota scan and alerting. `live-refresh` serves a
browser-facing dashboard API and writes its own snapshot on its own
schedule. Deploying `live-refresh` is optional (`deploy_live_refresh = false`
skips it entirely).

## Prerequisites

- Terraform >= 1.6 (tested with 1.16.1).
- git, to clone the repository (`git --version`).
- AWS CLI v2, to check credentials and read outputs (`aws --version`).
- AWS provider credentials with permission to create the resources listed in
  each module README (Lambda, IAM, S3, DynamoDB, SNS, SQS, KMS, EventBridge,
  CloudWatch, API Gateway, and optionally EC2 security groups for VPC mode).
  Least-privilege deploy roles are your responsibility; this project does not
  ship one.
- If `enable_auth = true` (the default), the stack creates a Lambda@Edge
  function, which AWS only allows in `us-east-1`. Keep `region` set to
  `us-east-1` when using the login gate; the quota monitor still works
  against a Connect instance in any Region.
- The repository checkout itself: this configuration packages
  `../lambda_function.py`, `../quota_definitions.json`,
  `../live-refresh/lambda_function.py`, and `../live-refresh/mapper_bridge.py`
  directly from the filesystem relative to this `terraform/` directory. Do
  not move this folder out of the repository.
- If you plan to run `live-refresh` with report storage, an existing S3
  bucket for `report_bucket_name`. This module does not create that bucket
  (neither does the SAM template it ports).
- A remote backend if you want shared state (not configured here). Start from
  [backend.tf.example](backend.tf.example): copy it to `backend.tf`, fill in
  your own bucket/table/region, and run `terraform init` again.

## Setup and run

1. Copy the example variables file and fill in real values.

   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars
   # edit terraform.tfvars: set connect_instance_id, notification_email,
   # report_bucket_name, tags.owner, etc.
   ```

   `name_prefix` (default `cs360-cqm`) is applied to every resource name so
   this deployment does not collide with an existing CloudFormation/SAM
   deployment of the same solution in the same account. Change it if you are
   running more than one copy side by side.

2. Initialize.

   ```bash
   terraform init
   ```

   Expected output ends with `Terraform has been successfully initialized!`.

3. Review the plan.

   ```bash
   terraform plan
   ```

4. Apply.

   ```bash
   terraform apply
   ```

   Expected output ends with `Apply complete! Resources: N added, 0 changed,
   0 destroyed.` followed by the outputs (`lambda_function_name`,
   `sns_topic_arn`, `live_refresh_api_endpoint`, etc).

   If `enable_auth = true` (the default) and you left `cloudfront_domain`
   empty, this apply is phase 1 of the two-phase apply described in step 7.
   It brings up CloudFront with no edge auth attached yet; the dashboard is
   not reachable until you complete phase 2.

5. If you set `notification_email`, confirm the SNS subscription email that
   arrives in that inbox. Alerts will not deliver until you confirm it.

6. If you deployed `live-refresh` and left `report_bucket_name` empty (the
   default), the module created a private dashboard bucket and serves it over
   HTTPS through CloudFront (Origin Access Control, no public bucket). Read the
   URL:

   ```bash
   terraform output live_refresh_dashboard_url
   ```

7. **Dashboard login gate (two-phase apply).** With `enable_auth = true` (the
   default), the dashboard and `GET /quota` are protected by Cognito. Because
   the Cognito callback URL needs the CloudFront domain, reaching the
   dashboard takes two applies total. The apply you ran in step 4 was phase 1:
   `cloudfront_domain` was empty, so CloudFront came up with no edge auth
   attached yet. Read its domain, then run phase 2:

   ```bash
   terraform output live_refresh_dashboard_url   # -> https://dXXXX.cloudfront.net/

   # Phase 2: set cloudfront_domain to that host (no scheme / trailing slash),
   # e.g. in terraform.tfvars: cloudfront_domain = "dXXXX.cloudfront.net"
   terraform apply
   ```

   Create a login and set its password (set `test_user_email` first). The
   password must be at least 12 characters and include an uppercase letter, a
   lowercase letter, a number, and a symbol:

   ```bash
   eval "$(terraform output -raw live_refresh_test_user_password_command)"
   # edit the placeholder password in that command first
   ```

   Log in at the Cognito hosted-UI prompt (it appears when you open
   `live_refresh_dashboard_url`) using the `test_user_email` you set and the
   password from the command above.

   See `modules/live-refresh/README.md` "Login gate" for the full details. Set
   `enable_auth = false` to leave the dashboard open.

## Managing dashboard logins

The `test_user_email` account is a bootstrap login, not the account you leave in
place for real use. Everything below uses the AWS CLI against the Cognito user
pool this stack created; read the pool ID from `terraform output
cognito_user_pool_id` first, or substitute it directly.

Add a real operator (they set their own password on first login):

```bash
POOL=$(terraform output -raw cognito_user_pool_id)
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL" \
  --username analyst@yourcompany.com \
  --user-attributes Name=email,Value=analyst@yourcompany.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL \
  --region us-east-1
```

Cognito emails them a temporary password; they are forced to set a permanent one
at first sign-in. (Email delivery uses the Cognito default, which is rate
limited; wire up Amazon SES on the user pool for volume.)

Reset or set a password for an existing user (this is the same command
`terraform output live_refresh_test_user_password_command` prints):

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL" \
  --username analyst@yourcompany.com \
  --password '<STRONG_PASSWORD>' --permanent \
  --region us-east-1
```

The password must meet the pool policy: at least 12 characters with an
uppercase letter, a lowercase letter, a number, and a symbol.

Remove the bootstrap account once real users exist:

```bash
aws cognito-idp admin-delete-user \
  --user-pool-id "$POOL" \
  --username dashboard-admin@example.com \
  --region us-east-1
```

Do not remove that user through the AWS console while leaving `test_user_email`
set in `terraform.tfvars`: the next `terraform apply` recreates it. To stop
Terraform managing a bootstrap user at all, clear `test_user_email` (leave it
empty) and apply; then manage all logins with the commands above.

List who currently has access:

```bash
aws cognito-idp list-users --user-pool-id "$POOL" \
  --query "Users[].{user:Username,email:Attributes[?Name=='email']|[0].Value,status:UserStatus}" \
  --output table --region us-east-1
```

## Teardown

```bash
terraform destroy
```

By default `force_destroy = false`, matching the CFN template's `Retain`
deletion policy on the metrics and access-log buckets: if those buckets still
contain objects, `terraform destroy` will fail rather than silently delete
your history. To force a full teardown including bucket contents, set
`force_destroy = true` in `terraform.tfvars` (or `-var force_destroy=true`)
and re-run `terraform destroy`. Do this only when you are certain you do not
need the stored metrics/reports.

`terraform destroy` removes every resource this configuration created,
including all three KMS keys' aliases and the keys themselves (pending
deletion), the SNS topic and its subscription, the DynamoDB table, the SQS
DLQ, the API Gateway REST API, the CloudFront distribution, the Cognito user
pool, and both Lambda functions. It does **not** touch a `report_bucket_name`
bucket you supplied yourself.

Lambda@Edge caveat: if you deployed with `enable_auth = true`, the
`-edge-auth` function is replicated to CloudFront edge locations. CloudFront
removes those replicas asynchronously after the distribution is deleted, which
can take from tens of minutes up to a few hours. `terraform destroy` may
therefore fail once with `InvalidParameterValueException: Lambda was unable to
delete ... because it is a replicated function`. This is an AWS Lambda@Edge
constraint, not a bug: wait for the replicas to clear and re-run
`terraform destroy`, or delete that function later once the replicas are gone.

## Known limitations

- No AWS WAF and no CloudFront access logging are configured on the dashboard
  distribution. The Cognito login gate and API Gateway stage throttling are in
  place; a WAF rate-based rule and CloudFront access logs are reasonable
  additions before high-traffic production use.
- The `live-refresh` module creates its own private dashboard bucket by default
  (when `report_bucket_name` is empty), served over HTTPS by CloudFront via
  Origin Access Control. Supply `report_bucket_name` only if you want to use an
  existing bucket instead; leave `report_bucket_name` empty and
  `create_dashboard_bucket = false` to run the API with no report storage (in
  which case `GET /quota` returns HTTP 503 until a bucket is supplied).
- Single-account, single-region only. No cross-account or cross-region
  assumptions are made anywhere in these modules.
- No remote backend is configured. State defaults to local
  `terraform.tfstate` unless you add your own `backend` block. Start from
  [backend.tf.example](backend.tf.example) to enable remote state.

## Cost

Rough monthly cost, consistent with the parent repository's README, which
estimates cost for the same underlying resources:

| Component | Pilot (1 instance, hourly schedule) | Production (larger account) |
|-----------|--------------------------------------|------------------------------|
| Lambda (quota-monitor, hourly) | $1 to $3 | up to ~$5 |
| Lambda (live-refresh, hourly) | well under $1 | ~$1 to $2 |
| S3 (metrics + access logs) | $0.10 to $0.50 | ~$1 |
| DynamoDB (on-demand, default on) | $0.25 to $2.00 | ~$3 |
| SNS (email) | ~$0.10 | ~$0.10 |
| CloudWatch (alarms and logs) | $0.50 to $1.00 | ~$1.50 |
| API Gateway (live-refresh, browser traffic only) | negligible | negligible |
| KMS (3 keys) | ~$3 (3 x $1/mo per key) | ~$3 |
| **Total** | **about $6 to $10** | **about $15 to $20** |

KMS keys are the one component that costs money even at zero traffic ($1/mo
per key regardless of usage); the other components scale with schedule
frequency and instance/quota count. Set `use_dynamodb = false` and
`use_s3_storage = false` to drop those line items if you only need alerting.

**Beyond pilot scale**: cost grows primarily with schedule frequency (tighter
than `rate(1 hour)` multiplies Lambda invocations and CloudWatch
`GetMetricData` calls roughly linearly) and with the number of Connect
instances/quotas monitored per run. It does not explode nonlinearly at any
point covered by this design; the largest single lever is schedule frequency.

## Structure

```
terraform/
  versions.tf                   required_version, provider version pins
  providers.tf                  aws provider block
  variables.tf                  all root inputs
  main.tf                       wires modules/quota-monitor and modules/live-refresh
  outputs.tf                    surfaces module outputs
  terraform.tfvars.example      copy to terraform.tfvars and fill in
  modules/
    quota-monitor/               port of ../connect-quota-monitor-cfn.yaml
    live-refresh/                port of ../live-refresh/template.yaml
```
