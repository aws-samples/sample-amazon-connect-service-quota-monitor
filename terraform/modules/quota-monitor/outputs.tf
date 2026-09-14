output "lambda_function_name" {
  description = "Enhanced Lambda function that monitors Connect quotas."
  value       = aws_lambda_function.quota_monitor.function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN."
  value       = aws_lambda_function.quota_monitor.arn
}

output "sns_topic_arn" {
  description = "SNS topic ARN for consolidated alerts."
  value       = aws_sns_topic.alerts.arn
}

output "s3_bucket_name" {
  description = "S3 bucket for metrics and reports storage. Empty string when use_s3_storage is false."
  value       = var.use_s3_storage ? aws_s3_bucket.metrics[0].bucket : ""
}

output "dynamodb_table_name" {
  description = "DynamoDB table for metrics and reports storage. Empty string when use_dynamodb is false."
  value       = var.use_dynamodb ? aws_dynamodb_table.metrics[0].name : ""
}

output "dead_letter_queue_url" {
  description = "Dead Letter Queue URL for failed Lambda executions."
  value       = aws_sqs_queue.dlq.url
}

output "dead_letter_queue_arn" {
  description = "Dead Letter Queue ARN for failed Lambda executions."
  value       = aws_sqs_queue.dlq.arn
}
