# quota-monitor module

Terraform port of `../../../connect-quota-monitor-cfn.yaml` (the CloudFormation template at the repo root). Deploys the enhanced
Amazon Connect Service Quota Monitor: a scheduled Lambda function that
discovers Connect instances, checks quota utilization against CloudWatch and
Service Quotas, and sends consolidated alerts through SNS.

## What it deploys

- Lambda function (`lambda_function.main`, Python 3.13) packaged directly from
  the repo root's `lambda_function.py` and `quota_definitions.json` via
  `data.archive_file`. Reserved concurrency of 1, a dead-letter queue, and
  KMS-encrypted environment variables.
- SQS dead-letter queue for failed invocations.
- EventBridge rule on `var.schedule_expression` (default `rate(1 hour)`) that
  triggers the Lambda, plus the invoke permission.
- CloudWatch log group with `var.log_retention_days` retention.
- Two CloudWatch alarms (Lambda errors, Lambda duration) that publish to the
  alert SNS topic.
- SNS topic (KMS-encrypted) for alerts, with an optional email subscription
  when `var.notification_email` is set.
- Three KMS keys (SNS, DynamoDB, Lambda), each with a rotation-enabled key and
  an alias.
- DynamoDB table for metrics/report storage when `var.use_dynamodb` is true,
  with a GSI on `instance_id`, TTL, point-in-time recovery, and KMS
  encryption.
- Two S3 buckets: metrics/reports and access logs, each with versioning,
  AES256 encryption, and a public access block.
- IAM execution role scoped to the Connect discovery/list APIs, Service
  Quotas, CloudWatch, SNS, SQS, KMS, and (conditionally) S3/DynamoDB storage.
- Optional security group and Lambda VPC config when `var.vpc_id` is set.

## Parity notes (how this port relates to the CloudFormation template)

The CloudFormation template (`../../../connect-quota-monitor-cfn.yaml`) and this
Terraform port deploy the same solution. Two bugs originally lived in the CFN
template; both are now fixed in the CFN template as well, so the two paths are at
parity. The notes below record the differences that remain by design.

1. **Code packaging.** The CFN template ships a bootstrap `ZipFile` stub and
   relies on `deploy.sh` (at the repo root) to package the real
   `lambda_function.py` + `quota_definitions.json`, upload them to S3, and update
   the function after stack create. Terraform's `data.archive_file` bundles the
   real code into the deployment zip at plan time, so a single `terraform apply`
   produces a working function with no separate upload step. Both paths run the
   same Lambda source.
2. **DynamoDB table name (fixed in both).** Originally the CFN template created
   the table as `${AWS::StackName}-metrics` while the `DYNAMODB_TABLE` env var and
   the IAM policy referenced the `DynamoDBTableName` parameter, so storage
   targeted a table that did not exist. The CFN template now names the table
   `!Ref DynamoDBTableName` so all three agree; this port ties the same three to
   the real `aws_dynamodb_table.metrics`.
3. **DynamoDB GSI throughput (fixed in both).** Originally the CFN template set
   `ProvisionedThroughput` on the `InstanceIdIndex` GSI of a `PAY_PER_REQUEST`
   table, which DynamoDB rejects at create time. Both the CFN template and this
   port now omit provisioned throughput on the GSI.
4. **Lambda runtime is python3.13** in both.

## Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name_prefix` | string | (required) | Prefix for every resource name. |
| `notification_email` | string | `""` | Email subscribed to the alert SNS topic. Empty skips the subscription. |
| `threshold_percentage` | number | `80` | Quota utilization alert threshold (1-99). |
| `schedule_expression` | string | `"rate(1 hour)"` | EventBridge schedule for the monitor Lambda. |
| `use_dynamodb` | bool | `true` | Create the DynamoDB table and its IAM/KMS resources. |
| `use_s3_storage` | bool | `true` | Create the metrics and access-log S3 buckets and their IAM policy. |
| `lambda_timeout` | number | see note below | Lambda timeout in seconds (60-900). |
| `lambda_memory` | number | `512` | Lambda memory in MB (256-10240). |
| `log_retention_days` | number | `30` | CloudWatch Logs retention. |
| `vpc_id` | string | `""` | Optional VPC ID for the Lambda. |
| `subnet_ids` | list(string) | `[]` | Required when `vpc_id` is set. |
| `force_destroy` | bool | `false` | Allow `terraform destroy` to delete non-empty S3 buckets. |
| `repo_root` | string | (required) | Absolute path to the directory containing `lambda_function.py` and `quota_definitions.json`. |
| `tags` | map(string) | `{}` | Tags merged onto every resource. |

This module's own default for `lambda_timeout` is never used when you deploy
through the root Terraform config in this repository, because
`terraform/main.tf` always passes `var.lambda_timeout` into the module
explicitly. The effective value comes from the root `terraform/variables.tf`
(default `600`), or from whatever `terraform.tfvars` sets, since
`terraform.tfvars.example` pins it to `300`. Check `terraform.tfvars` if you
need to know the value a given deployment is actually using.

## Outputs

See `outputs.tf`: `lambda_function_name`, `lambda_function_arn`,
`sns_topic_arn`, `s3_bucket_name`, `dynamodb_table_name`,
`dead_letter_queue_url`, `dead_letter_queue_arn`.

## IAM

The execution role's inline policy grants read-only List/Describe/Get access
only to the services the monitor actually calls: Connect, Connect Cases,
Connect Campaigns, Service Quotas (`ListServiceQuotas` and `ListServices` only,
`GetServiceQuota` is intentionally omitted since the monitor batches applied
limits per service instead of one call per quota), CloudWatch, and STS. These use `Resource: "*"` because they are
account-wide discovery APIs (you cannot scope `ListInstances` to a specific
instance ARN before you have called it). `sns:Publish` is scoped to the
module's own SNS topic, SQS access is scoped to the module's own
dead-letter queue, and KMS access is scoped to the module's own keys.
