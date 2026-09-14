output "dashboard_url" {
  description = "Public HTTPS dashboard URL. When this module creates its own dashboard bucket, this is the CloudFront distribution domain (served automatically, no manual step, no public bucket). Empty when you supply your own report_bucket_name (host it yourself)."
  value       = local.create_dashboard ? "https://${aws_cloudfront_distribution.dashboard[0].domain_name}/" : ""
}

output "dashboard_bucket_name" {
  description = "Name of the private dashboard origin bucket this module created (empty when a bucket was supplied via report_bucket_name)."
  value       = local.create_dashboard ? aws_s3_bucket.dashboard[0].id : ""
}

output "api_endpoint" {
  description = "History API (used by the dashboard for the 7-day trend). Ends in /prod/quota."
  value       = local.api_endpoint
}

output "function_arn" {
  description = "Lambda function ARN."
  value       = aws_lambda_function.metrics.arn
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID for the dashboard login gate. Empty when enable_auth is false."
  value       = local.enable_auth ? aws_cognito_user_pool.dashboard[0].id : ""
}

output "cognito_hosted_ui_domain" {
  description = "Cognito hosted-UI base URL (https://<prefix>.auth.<region>.amazoncognito.com). Empty when enable_auth is false."
  value       = local.cognito_hosted_ui_domain
}

output "test_user_password_command" {
  description = "Run this once to set the bootstrap test user's permanent password. Empty when no test_user_email was provided."
  value = (local.enable_auth && var.test_user_email != "") ? join(" ", [
    "aws cognito-idp admin-set-user-password",
    "--user-pool-id ${aws_cognito_user_pool.dashboard[0].id}",
    "--username ${var.test_user_email}",
    "--password '<CHOOSE_A_STRONG_PASSWORD>'",
    "--permanent",
    "--region ${local.region}",
  ]) : ""
}
