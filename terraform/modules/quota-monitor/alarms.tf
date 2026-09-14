# Mirrors LambdaErrorAlarm / LambdaDurationAlarm in the CFN template.
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name        = "${var.name_prefix}-LambdaErrors"
  alarm_description = "The quota monitor Lambda is failing"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.quota_monitor.function_name
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}

# A throttled invocation is silently dropped: the scheduled EventBridge
# trigger fires async, and if the Lambda is throttled that run is lost with
# no error captured by the Errors alarm above (Errors counts function
# failures, not throttles). Without this alarm, a dropped hourly quota check
# would fire no alert at all.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name        = "${var.name_prefix}-LambdaThrottles"
  alarm_description = "The quota monitor Lambda is being throttled and scheduled runs may be dropped"

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.quota_monitor.function_name
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  alarm_name        = "${var.name_prefix}-LambdaDuration"
  alarm_description = "The quota monitor Lambda is running longer than expected"

  namespace   = "AWS/Lambda"
  metric_name = "Duration"

  dimensions = {
    FunctionName = aws_lambda_function.quota_monitor.function_name
  }

  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 500000
  comparison_operator = "GreaterThanOrEqualToThreshold"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = local.common_tags
}
