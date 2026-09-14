# Mirrors the ScheduledRefresh Schedule event source on MetricsFunction in
# the SAM template. This is the only path that writes/crawls; the API path
# (GET /quota) is read-only and performs an on-demand collection per request.
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.name_prefix}-live-refresh-schedule"
  description         = "Periodic metrics collection, archive write, and peak tracking"
  schedule_expression = var.schedule_expression
  state               = "ENABLED"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "metrics" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  arn       = aws_lambda_function.metrics.arn
  target_id = "LiveRefreshMetricsTarget"
}

resource "aws_lambda_permission" "allow_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.metrics.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
