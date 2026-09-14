# Mirrors AlertSNSTopic / EmailSubscription in the CFN template.
resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-ConnectQuotaAlerts"
  display_name      = "Connect Quota Alerts"
  kms_master_key_id = aws_kms_key.sns.key_id

  tags = merge(local.common_tags, { Purpose = "ConnectQuotaMonitor" })
}

resource "aws_sns_topic_subscription" "email" {
  count = var.notification_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email
}
