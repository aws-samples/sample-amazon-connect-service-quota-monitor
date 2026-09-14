locals {
  repo_root = abspath("${path.module}/..")

  common_tags = merge(
    var.tags,
    {
      project = var.name_prefix
    }
  )
}

module "quota_monitor" {
  source = "./modules/quota-monitor"

  name_prefix             = var.name_prefix
  notification_email      = var.notification_email
  threshold_percentage    = var.threshold_percentage
  schedule_expression     = var.schedule_expression
  use_dynamodb            = var.use_dynamodb
  use_s3_storage          = var.use_s3_storage
  lambda_timeout          = var.lambda_timeout
  lambda_memory           = var.lambda_memory
  log_retention_days      = var.log_retention_days
  vpc_id                  = var.vpc_id
  subnet_ids              = var.subnet_ids
  force_destroy           = var.force_destroy
  dynamodb_ttl_days       = var.dynamodb_ttl_days
  send_allclear_heartbeat = var.send_allclear_heartbeat
  repo_root               = local.repo_root
  tags                    = local.common_tags
}

module "live_refresh" {
  count = var.deploy_live_refresh ? 1 : 0

  source = "./modules/live-refresh"

  name_prefix             = var.name_prefix
  connect_instance_id     = var.connect_instance_id
  line_config_json        = var.line_config_json
  line_config_s3_bucket   = var.line_config_s3_bucket
  line_config_s3_key      = var.line_config_s3_key
  report_bucket_name      = var.report_bucket_name
  create_dashboard_bucket = var.create_dashboard_bucket
  force_destroy           = var.force_destroy
  schedule_expression     = var.schedule_expression
  history_api_url         = var.history_api_url
  log_retention_days      = var.log_retention_days
  enable_auth             = var.enable_auth
  cloudfront_domain       = var.cloudfront_domain
  test_user_email         = var.test_user_email
  repo_root               = "${local.repo_root}/live-refresh"
  repo_root_top           = local.repo_root
  tags                    = local.common_tags

  # Reuse the quota-monitor module's alert SNS topic (always created, no
  # count) rather than standing up a second topic just for this module's
  # alarms (modules/live-refresh/alarms.tf).
  alert_sns_topic_arn = module.quota_monitor.sns_topic_arn
}
