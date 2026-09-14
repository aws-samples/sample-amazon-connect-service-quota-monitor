# Mirrors LambdaLogGroup in the CFN template (retention only; the CFN template
# does not KMS-encrypt this log group either, so parity is kept as-is).
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.quota_monitor.function_name}"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}
