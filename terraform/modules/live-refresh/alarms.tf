# The live-refresh metrics Lambda previously had no CloudWatch alarms and no
# DLQ: a failed or throttled scheduled run (events.tf) silently skips a
# metrics collection/peak-update cycle with no signal anywhere. These two
# alarms give it the same Errors/Throttles coverage the quota-monitor module
# has (see modules/quota-monitor/alarms.tf), wired to the same alert topic
# via var.alert_sns_topic_arn so this module does not need to stand up its
# own SNS topic.
#
# No DLQ is added here: the Lambda is invoked both by EventBridge (async) and
# synchronously by API Gateway. A DLQ only captures the async path, and the
# module's aws_lambda_function_event_invoke_config (lambda.tf) already
# disables retries for that path in favor of "wait for the next scheduled
# run" - matching the quota-monitor module's stated retry policy. Adding a
# DLQ without a consumer for it would be dead infrastructure, so it is
# intentionally left out; flag if a consumer/runbook is wanted later.

locals {
  # alarm_actions must be [] (not [""]) when no topic is configured, or the
  # AWS provider rejects the alarm with an invalid ARN.
  live_refresh_alarm_actions = var.alert_sns_topic_arn != "" ? [var.alert_sns_topic_arn] : []
}

resource "aws_cloudwatch_metric_alarm" "metrics_errors" {
  alarm_name        = "${var.name_prefix}-LiveRefreshErrors"
  alarm_description = "The live-refresh metrics Lambda is failing"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.metrics.function_name
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  alarm_actions = local.live_refresh_alarm_actions

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "metrics_throttles" {
  alarm_name        = "${var.name_prefix}-LiveRefreshThrottles"
  alarm_description = "The live-refresh metrics Lambda is being throttled; scheduled collection/peak-update runs may be dropped"

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.metrics.function_name
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  alarm_actions = local.live_refresh_alarm_actions

  tags = local.common_tags
}
