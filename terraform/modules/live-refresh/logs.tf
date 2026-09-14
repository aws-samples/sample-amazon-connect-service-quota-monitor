# The SAM template relies on Lambda's implicit, unmanaged log group (no
# retention set, logs kept forever). Declaring it explicitly here with
# retention is a deliberate improvement over the template's default, not a
# parity gap: an unbounded log group is an unbounded cost with no offsetting
# benefit.
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.metrics.function_name}"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}
