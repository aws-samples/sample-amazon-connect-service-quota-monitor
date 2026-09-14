# IAM Setup Guide

## For the CLI tool

Attach `cli-user-policy.json` to your IAM user or the role you assume.

Before attaching, edit the file and replace:
- `YOUR_ACCOUNT_ID`: your 12-digit AWS account ID
- `YOUR_INSTANCE_ID`: your Connect instance ID

```bash
aws iam put-user-policy \
  --user-name YOUR_USER \
  --policy-name ConnectDashboardCLI \
  --policy-document file://iam/cli-user-policy.json
```

## For the Lambda (automated monitoring)

You do not set this up by hand. The Terraform modules in `terraform/` create and
manage the Lambda execution role and its policy for you (see the IAM section of
`terraform/modules/quota-monitor/README.md` and
`terraform/modules/live-refresh/README.md`). The `trust-policy.json` and
`lambda-permission-policy.json` files here are kept only as a reference for the
permissions the Lambda needs; you do not need to apply them for a Terraform
deployment.

## What each file does

| File | Purpose |
|------|---------|
| `cli-user-policy.json` | The one you attach by hand: Connect read + Quotas + CloudWatch for running `connect-resource-mapper.py` locally. Scoped to your specific instance. Edit account and instance ID. |
| `trust-policy.json` | Reference only. Trust policy allowing the Lambda service to assume an execution role. Terraform builds the equivalent. |
| `lambda-permission-policy.json` | Reference only. The Lambda's Connect read + S3 write + CloudWatch Logs permissions. Terraform builds the equivalent, scoped to the resources it creates. |

## Security notes

- All Connect access is read-only: the `List*` and `Describe*` families plus the single read action `connect:GetTrafficDistribution`. No `Put*`, `Create*`, `Delete*`, or `Update*` actions. The broad `connect:Get*` wildcard is intentionally NOT used, because it would also grant `connect:GetFederationToken` (which mints a signed-in session into the Connect admin/agent UI) — not something a quota monitor needs. If you extend this policy, enumerate specific `Get` actions rather than re-adding the wildcard.
- The Lambda only writes to your specified S3 bucket and CloudWatch Logs.
- Connect permissions are scoped to your instance ARN (CLI policy). The Lambda policy uses wildcards for simplicity but can be scoped the same way.
