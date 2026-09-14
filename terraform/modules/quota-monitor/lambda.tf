# Packages the real monitoring code directly into the deployment zip. Unlike
# the CFN template (which ships a placeholder inline ZipFile and relies on
# deploy.sh to upload the real code to S3 and call UpdateFunctionCode after
# stack create), Terraform's archive_file data source bundles
# lambda_function.py and quota_definitions.json from the repo root at plan
# time, so `terraform apply` alone produces a fully working function. No
# deploy.sh equivalent step is needed.
data "archive_file" "quota_monitor" {
  type        = "zip"
  output_path = "${path.module}/build/quota_monitor.zip"

  source {
    content  = file("${var.repo_root}/lambda_function.py")
    filename = "lambda_function.py"
  }

  source {
    content  = file("${var.repo_root}/quota_definitions.json")
    filename = "quota_definitions.json"
  }
}

# Mirrors QuotaMonitorLambda in the CFN template.
resource "aws_lambda_function" "quota_monitor" {
  function_name = "${var.name_prefix}-EnhancedConnectQuotaMonitor"
  description   = "Enhanced Amazon Connect Service Quota Monitor"

  filename         = data.archive_file.quota_monitor.output_path
  source_code_hash = data.archive_file.quota_monitor.output_base64sha256

  handler = "lambda_function.main"
  runtime = "python3.13"
  role    = aws_iam_role.lambda_execution.arn

  timeout                        = var.lambda_timeout
  memory_size                    = var.lambda_memory
  reserved_concurrent_executions = 1
  kms_key_arn                    = aws_kms_key.lambda.arn

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  dynamic "vpc_config" {
    for_each = local.use_vpc ? [1] : []

    content {
      security_group_ids = [aws_security_group.lambda[0].id]
      subnet_ids         = var.subnet_ids
    }
  }

  environment {
    variables = {
      ALERT_SNS_TOPIC_ARN  = aws_sns_topic.alerts.arn
      THRESHOLD_PERCENTAGE = tostring(var.threshold_percentage)
      S3_BUCKET            = var.use_s3_storage ? aws_s3_bucket.metrics[0].bucket : ""
      USE_DYNAMODB         = var.use_dynamodb ? "true" : "false"
      DYNAMODB_TABLE       = var.use_dynamodb ? aws_dynamodb_table.metrics[0].name : ""
      USE_S3_STORAGE       = var.use_s3_storage ? "true" : "false"
      # Controls the TTL value the Lambda writes on each DynamoDB item; the
      # table's ttl attribute (dynamodb.tf) is what actually expires it.
      DYNAMODB_TTL_DAYS       = tostring(var.dynamodb_ttl_days)
      SEND_ALLCLEAR_HEARTBEAT = var.send_allclear_heartbeat ? "true" : "false"
    }
  }

  tags = merge(local.common_tags, {
    Purpose   = "EnhancedConnectQuotaMonitor"
    Version   = "2.0"
    ManagedBy = "Terraform"
  })

  depends_on = [
    aws_iam_role_policy.quota_monitor_inline,
    aws_iam_role_policy_attachment.basic_execution,
  ]
}

# A failed/timed-out invocation should not fan out into async retries that
# storm the shared Service Quotas rate limit; the next scheduled hourly
# invocation is the retry.
resource "aws_lambda_function_event_invoke_config" "quota_monitor" {
  function_name = aws_lambda_function.quota_monitor.function_name

  maximum_retry_attempts = 0
}
