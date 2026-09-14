# --- quota-monitor module -----------------------------------------------------

output "lambda_function_name" {
  description = "Enhanced Lambda function that monitors Connect quotas."
  value       = module.quota_monitor.lambda_function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN."
  value       = module.quota_monitor.lambda_function_arn
}

output "sns_topic_arn" {
  description = "SNS topic ARN for consolidated alerts."
  value       = module.quota_monitor.sns_topic_arn
}

output "s3_bucket_name" {
  description = "S3 bucket for metrics and reports storage."
  value       = module.quota_monitor.s3_bucket_name
}

output "dynamodb_table_name" {
  description = "DynamoDB table for metrics and reports storage."
  value       = module.quota_monitor.dynamodb_table_name
}

output "dead_letter_queue_url" {
  description = "Dead Letter Queue URL for failed Lambda executions."
  value       = module.quota_monitor.dead_letter_queue_url
}

# --- live-refresh module -------------------------------------------------------

output "live_refresh_api_endpoint" {
  description = "History API endpoint for the live-refresh dashboard (ends in /prod/quota). Empty when deploy_live_refresh is false."
  value       = try(module.live_refresh[0].api_endpoint, "")
}

output "live_refresh_dashboard_url" {
  description = "Public HTTPS dashboard URL (the CloudFront distribution domain). Populated when the module creates its own dashboard bucket, i.e. when report_bucket_name is EMPTY. Empty when you supply your own report_bucket_name or when deploy_live_refresh is false. In the two-phase auth apply, read this after phase 1 and feed the host into cloudfront_domain for phase 2."
  value       = try(module.live_refresh[0].dashboard_url, "")
}

output "live_refresh_function_arn" {
  description = "Live-refresh metrics Lambda function ARN. Empty when deploy_live_refresh is false."
  value       = try(module.live_refresh[0].function_arn, "")
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID for the dashboard login gate. Empty when auth is disabled or deploy_live_refresh is false."
  value       = try(module.live_refresh[0].cognito_user_pool_id, "")
}

output "cognito_hosted_ui_domain" {
  description = "Cognito hosted-UI base URL for the dashboard login gate. Empty when auth is disabled or deploy_live_refresh is false."
  value       = try(module.live_refresh[0].cognito_hosted_ui_domain, "")
}

output "live_refresh_test_user_password_command" {
  description = "Run this once to set the bootstrap Cognito test user's password. Empty when no test_user_email was provided or deploy_live_refresh is false."
  value       = try(module.live_refresh[0].test_user_password_command, "")
}
