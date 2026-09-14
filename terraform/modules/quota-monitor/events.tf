# Mirrors ScheduledRule / LambdaPermission in the CFN template.
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.name_prefix}-Schedule"
  description         = "Scheduled rule to trigger Connect Quota Monitor Lambda"
  schedule_expression = var.schedule_expression
  state               = "ENABLED"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "quota_monitor" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  arn       = aws_lambda_function.quota_monitor.arn
  target_id = "ConnectQuotaMonitorTarget"
}

resource "aws_lambda_permission" "allow_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.quota_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
