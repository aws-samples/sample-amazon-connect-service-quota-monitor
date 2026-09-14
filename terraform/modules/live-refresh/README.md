# live-refresh module

Terraform port of `../../../live-refresh/template.yaml` (the AWS SAM template at
the repo root). Deploys a Lambda
function behind an API Gateway REST API that serves real-time CloudWatch and
Service Quotas metrics to the Connect Operations Dashboard, plus a scheduled
run that writes a snapshot and regenerates the dashboard page.

## What it deploys

- Lambda function (`lambda_function.lambda_handler`, Python 3.13, arm64)
  packaged from `live-refresh/lambda_function.py` and
  `live-refresh/mapper_bridge.py` via `data.archive_file`.
- API Gateway REST API with a `/quota` resource:
  - `GET /quota`, `AWS_PROXY` integration to the Lambda.
  - `OPTIONS /quota`, `MOCK` integration returning the CORS preflight
    response (`Access-Control-Allow-Origin: *`,
    `Access-Control-Allow-Methods: GET,OPTIONS`,
    `Access-Control-Allow-Headers: Content-Type`).
  - `prod` stage with stage-level throttling (rate 10, burst 20).
  - No API key or usage plan. This is intentional: the endpoint returns
    non-sensitive quota-utilization metrics fetched directly by a browser
    dashboard, and an API key embedded in static JavaScript protects nothing.
    Abuse and runaway cost are bounded by the throttle above. Layer IAM
    (`AWS_IAM`) or a Cognito authorizer in front of this if you need
    authenticated access.
- EventBridge rule on `var.schedule_expression` (default `rate(1 hour)`) that
  triggers the same Lambda on a schedule; this is the only path that writes
  the snapshot/archive/dashboard HTML. The API path is read-only and performs
  an on-demand collection per request.
- CloudWatch log group with `var.log_retention_days` retention.
- IAM execution role scoped to CloudWatch metrics reads, `ListServiceQuotas`,
  and (conditionally) the report bucket and line-config object.

## Parity notes (deliberate differences from the SAM template)

1. **No Function<->Api circular dependency / no two-pass deploy.** The SAM
   template's `README.md` documents a manual two-deploy workflow: deploy once
   with `HistoryApiUrl` blank, note the `ApiEndpoint` output, then redeploy
   with that value fed back in as a parameter, because the template avoids
   `!Ref`-ing the API from the function to prevent a circular dependency.
   Terraform does not need this dance. `local.constructed_api_endpoint` in
   `data.tf` builds the URL from the REST API's `id`, the region, and the
   stage name, none of which depend on the Lambda function, so the Lambda's
   `API_ENDPOINT` environment variable can reference it directly with no
   cycle. Setting `var.history_api_url` explicitly still overrides the
   constructed value if you want to pin it.
2. **The full consolidated-report dashboard is generated (not the lightweight
   fallback).** The `data.archive_file` bundles `connect-resource-mapper.py`
   and `consolidated_report.py` flatly alongside `mapper_bridge.py` in the
   Lambda zip, and `mapper_bridge.py` resolves the mapper in its own directory
   first, so the import succeeds inside the deployed Lambda. On the scheduled
   run, `_write_latest` runs the full resource crawl and writes the complete
   consolidated report as `index.html`. `lambda_function.py` still wraps the
   import in `try`/`except ImportError` and falls back to a lightweight page if
   the mapper modules are ever missing, but with this module's packaging they
   are present. The heavy crawl runs only on the scheduled invocation; the
   public API path stays read-only.
3. **Explicit log group with retention.** The SAM template relies on
   Lambda's implicit, unmanaged log group (no retention, kept forever). This
   module declares the log group explicitly with `var.log_retention_days`.
   This is a cost/hygiene improvement, not a parity gap: an unbounded log
   group has no offsetting benefit.
4. **The report bucket itself is not created here.** Neither the SAM
   template nor this module creates the S3 bucket used for
   `latest.json`/`archive/`/`peaks/`/`index.html`. `ReportBucket` in the SAM
   template, and `var.report_bucket_name` here, both name an existing bucket
   you provide. `report_bucket_name` is optional. If you leave it empty, the
   API and scheduled function still deploy and run, but `GET /quota` returns
   HTTP 503 until you supply a bucket, since there is nowhere to write a
   snapshot or read history from. Leave `report_bucket_name` empty (with
   `create_dashboard_bucket = true`, the default) to have the module create a
   private dashboard bucket served over HTTPS by CloudFront via Origin Access
   Control, no public bucket, compatible with account-level S3 Block Public
   Access. Do not reuse the quota-monitor module's metrics bucket for this:
   that bucket keeps
   `block_public_acls`/`block_public_policy`/`restrict_public_buckets` enabled
   and also stores operational quota data, so pointing the dashboard flow at
   it either fails or tempts you into weakening its public access block.

## Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name_prefix` | string | (required) | Prefix for every resource name. |
| `connect_instance_id` | string | `""` | Amazon Connect instance ID. |
| `line_config_json` | string | `""` | Inline JSON line configuration. |
| `line_config_s3_bucket` | string | `""` | S3 bucket for `line-config.json` (alternative to inline). |
| `line_config_s3_key` | string | `"line-config.json"` | S3 key for the line config object. |
| `report_bucket_name` | string | `""` | Existing S3 bucket for reports/archives/peaks. Empty makes the module create its own private dashboard bucket (see `create_dashboard_bucket`). |
| `create_dashboard_bucket` | bool | `true` | When `report_bucket_name` is empty, create a private dashboard bucket served over HTTPS by CloudFront via OAC (no public bucket). Ignored when `report_bucket_name` is set. |
| `force_destroy` | bool | `false` | Allow `terraform destroy` to delete the dashboard bucket even if it still holds objects. |
| `schedule_expression` | string | `"rate(1 hour)"` | EventBridge schedule for the metrics Lambda. |
| `history_api_url` | string | `""` | Override for the constructed API endpoint URL. |
| `log_retention_days` | number | `30` | CloudWatch Logs retention. |
| `enable_auth` | bool | `true` | Create the Cognito login gate and protect the dashboard + `GET /quota`. See "Login gate" below. |
| `cloudfront_domain` | string | `""` | CloudFront domain used to build the Cognito callback/logout URLs (plain string, no cycle). Empty in phase 1; set in phase 2. |
| `test_user_email` | string | `""` | Optional bootstrap Cognito user (no invite email). Empty to skip. |
| `repo_root` | string | (required) | Absolute path to the `live-refresh/` directory. |
| `repo_root_top` | string | (required) | Absolute path to the top-level repo root (bundles `connect-resource-mapper.py` and `consolidated_report.py` into the Lambda zip). |
| `tags` | map(string) | `{}` | Tags merged onto every resource. |

## Outputs

See `outputs.tf`: `dashboard_url`, `dashboard_bucket_name`, `api_endpoint`,
`function_arn`, `cognito_user_pool_id`, `cognito_hosted_ui_domain`,
`test_user_password_command`.

## Login gate (Cognito + Lambda@Edge): two-phase apply

When `enable_auth = true` (the default), the module puts a Cognito login gate
in front of both the dashboard and the API:

- **Dashboard (CloudFront):** a Lambda@Edge viewer-request handler
  (`edge-auth/index.js`, vanilla Node.js 20, no npm deps) runs the OAuth2
  authorization-code flow against the Cognito hosted UI. Unauthenticated
  viewers are 302'd to the hosted UI; after login the `/callback` request
  exchanges the code for tokens and sets an httpOnly, Secure, SameSite=Lax
  cookie carrying the Cognito `id_token`. Subsequent requests verify that token
  (RS256 via the pool JWKS, `exp`/`iss`/`aud` checked) and pass through.
- **API (`GET /quota`):** protected by a `COGNITO_USER_POOLS` authorizer.
  `OPTIONS` stays open (`NONE`) for CORS preflight.

### Why two phases

The Cognito app-client callback URL needs the CloudFront domain; CloudFront
needs the Lambda@Edge; the edge Lambda needs the Cognito config (and cannot use
env vars, so its config is baked into the bundle). That is a dependency cycle.
The module breaks it with a plain-string variable `cloudfront_domain` (never a
reference to the CloudFront resource) plus the gate
`enable_edge_auth = enable_auth && cloudfront_domain != ""`:

- **Phase 1 (`cloudfront_domain = ""`):** creates CloudFront, the Cognito pool,
  hosted-UI domain, app client (with a placeholder callback), and the API
  authorizer. The Lambda@Edge and its CloudFront association are NOT created, so
  CloudFront comes up cleanly and its domain becomes known.

  ```bash
  terraform apply
  terraform output live_refresh_dashboard_url   # -> https://dXXXX.cloudfront.net/
  ```

- **Phase 2 (`cloudfront_domain = "dXXXX.cloudfront.net"`)** (host only, no
  scheme/trailing slash): rewrites the app-client callback/logout URLs to the
  real domain, creates the Lambda@Edge (config.json rendered from the Cognito
  outputs), and attaches it to CloudFront as the viewer-request function.

  ```bash
  terraform apply
  ```

> The in-page 7-day trend fetch (`${API_ENDPOINT}?history=7d`) now works
> same-origin behind the login gate once `cloudfront_domain` is set (phase 2):
> `GET /quota` is routed through the same CloudFront distribution via an
> `ordered_cache_behavior` to the API Gateway origin, gated by the same
> Lambda@Edge viewer-request function. A verified session cookie gets the
> Cognito `id_token` injected as an `Authorization: Bearer` header before the
> request reaches the `COGNITO_USER_POOLS` authorizer, so the browser's
> same-origin `fetch("/quota")` is covered by the cookie with no token in page
> JavaScript. An unauthenticated request to `/quota` gets the same 302 to the
> Cognito hosted UI as the dashboard. `local.api_endpoint` (`data.tf`) points
> at `https://<cloudfront_domain>/quota` once both the dashboard bucket and
> `cloudfront_domain` are set (phase 2); it falls back to the cross-origin
> execute-api URL in phase 1 or when no dashboard bucket exists. The dashboard
> handler still tolerates a failed fetch, so the trend widget degrades
> gracefully if either path breaks.

### Set the test user's password

If you set `test_user_email`, the user is created with `message_action =
SUPPRESS` (no invite email). Set a permanent password with:

```bash
eval "$(terraform output -raw live_refresh_test_user_password_command)"
# edit the placeholder password in that command first
```

The password must be at least 12 characters and include an uppercase letter, a
lowercase letter, a number, and a symbol.

### Lambda@Edge region

Lambda@Edge functions must be created in `us-east-1`. This module inherits the
root provider region (`var.region`, default `us-east-1`); keep the stack in
`us-east-1` or phase 2 will fail at apply.

## API

```
GET /quota                -> latest snapshot (fresh collection)
GET /quota?history=1h     -> last hour, minute-by-minute (from the archive)
GET /quota?history=1d     -> last 24 hours, hourly
GET /quota?history=7d     -> last 7 days, daily peaks
GET /quota?history=trend  -> last 30 days, daily peaks
```
