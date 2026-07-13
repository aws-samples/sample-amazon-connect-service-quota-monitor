# IAM Setup Guide

## For the CLI tool

Attach `cli-user-policy.json` to your IAM user or the role you assume.

Before attaching, edit the file and replace:
- `YOUR_ACCOUNT_ID` — your 12-digit AWS account ID
- `YOUR_INSTANCE_ID` — your Connect instance ID

```bash
aws iam put-user-policy \
  --user-name YOUR_USER \
  --policy-name ConnectDashboardCLI \
  --policy-document file://iam/cli-user-policy.json
```

## For the Lambda (automated monitoring)

### Step 1: Create the execution role

```bash
aws iam create-role \
  --role-name ConnectQuotaDashboardRole \
  --assume-role-policy-document file://iam/trust-policy.json
```

### Step 2: Attach permissions

Edit `lambda-permission-policy.json` first — replace `YOUR_BUCKET` and `YOUR_ACCOUNT_ID`.

```bash
aws iam put-role-policy \
  --role-name ConnectQuotaDashboardRole \
  --policy-name ConnectQuotaDashboardPolicy \
  --policy-document file://iam/lambda-permission-policy.json
```

### Step 3: Use the role ARN when deploying

```bash
aws iam get-role --role-name ConnectQuotaDashboardRole --query 'Role.Arn' --output text
```

Pass this ARN to the SAM template or Lambda creation command.

## What each file does

| File | Purpose |
|------|---------|
| `trust-policy.json` | Allows Lambda service to assume the role. Ready to use, no edits needed. |
| `lambda-permission-policy.json` | Connect read + S3 write + CloudWatch Logs. Edit bucket and account ID. |
| `cli-user-policy.json` | Connect read + Quotas + CloudWatch. Scoped to your specific instance. Edit account and instance ID. |

## Security notes

- All policies are read-only for Connect. No `Put*`, `Create*`, `Delete*`, or `Update*` actions.
- The Lambda only writes to your specified S3 bucket and CloudWatch Logs.
- Connect permissions are scoped to your instance ARN (CLI policy). The Lambda policy uses wildcards for simplicity but can be scoped the same way.
