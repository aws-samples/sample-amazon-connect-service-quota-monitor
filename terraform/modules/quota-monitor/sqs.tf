# Mirrors LambdaDeadLetterQueue in the CFN template.
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-ConnectQuotaMonitor-DLQ"
  message_retention_seconds = 1209600 # 14 days
  kms_master_key_id         = "alias/aws/sqs"

  tags = merge(local.common_tags, { Purpose = "ConnectQuotaMonitorDLQ" })
}
