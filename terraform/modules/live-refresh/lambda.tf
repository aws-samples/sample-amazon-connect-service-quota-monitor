# Packages the live-refresh handler AND the resource-mapper modules flatly into
# /var/task so the scheduled run generates the FULL consolidated-report
# dashboard rather than the lightweight fallback.
#
# mapper_bridge.py imports connect-resource-mapper.py; it resolves the mapper in
# its own directory when co-located (the fix applied to mapper_bridge.py), so
# bundling all four files flat here makes `collect_all` + the consolidated
# report importable inside the Lambda. The heavy resource crawl only runs on the
# scheduled (EventBridge) invocation; the public API path stays read-only.
data "archive_file" "live_refresh" {
  type        = "zip"
  output_path = "${path.module}/build/live_refresh.zip"

  source {
    content  = file("${var.repo_root}/lambda_function.py")
    filename = "lambda_function.py"
  }

  source {
    content  = file("${var.repo_root}/mapper_bridge.py")
    filename = "mapper_bridge.py"
  }

  source {
    content  = file("${var.repo_root_top}/connect-resource-mapper.py")
    filename = "connect-resource-mapper.py"
  }

  source {
    content  = file("${var.repo_root_top}/consolidated_report.py")
    filename = "consolidated_report.py"
  }
}

# Mirrors MetricsFunction in the SAM template.
resource "aws_lambda_function" "metrics" {
  function_name = "${var.name_prefix}-live-refresh-metrics"
  description   = "Serves live CloudWatch metrics for Connect Operations Dashboard"

  filename         = data.archive_file.live_refresh.output_path
  source_code_hash = data.archive_file.live_refresh.output_base64sha256

  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["arm64"]
  role          = aws_iam_role.lambda_execution.arn

  timeout     = 300
  memory_size = 512

  # Caps concurrent invocations at 1. A scheduled run (events.tf) and an
  # overlapping API Gateway request both read-modify-write the same S3 peak
  # file; without this cap, two concurrent invocations can race and one's
  # peak update silently clobbers the other's. Matches the quota-monitor
  # module's Lambda (modules/quota-monitor/lambda.tf), which sets the same
  # value for the analogous reason.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      CONNECT_INSTANCE_ID   = var.connect_instance_id
      LINE_CONFIG_JSON      = var.line_config_json
      LINE_CONFIG_S3_BUCKET = var.line_config_s3_bucket
      LINE_CONFIG_S3_KEY    = var.line_config_s3_key
      S3_REPORT_BUCKET      = local.effective_report_bucket
      API_ENDPOINT          = local.api_endpoint
    }
  }

  tags = merge(local.common_tags, { ManagedBy = "Terraform" })

  depends_on = [
    aws_iam_role_policy.metrics_inline,
    aws_iam_role_policy_attachment.basic_execution,
  ]
}

# A failed/timed-out scheduled run should not fan out into async retries; the
# next scheduled hourly invocation is the retry. (The API Gateway invocation
# path is synchronous and unaffected by this setting.)
resource "aws_lambda_function_event_invoke_config" "metrics" {
  function_name = aws_lambda_function.metrics.function_name

  maximum_retry_attempts = 0
}
