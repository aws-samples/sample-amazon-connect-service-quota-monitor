variable "name_prefix" {
  description = "Prefix applied to every resource name in this module."
  type        = string
}

variable "connect_instance_id" {
  description = "Amazon Connect instance ID."
  type        = string
  default     = ""
}

variable "line_config_json" {
  description = "Inline JSON line configuration (for small configs). Leave empty to use S3 or no line config."
  type        = string
  default     = ""
}

variable "line_config_s3_bucket" {
  description = "S3 bucket containing line-config.json (alternative to inline). Leave empty if not used."
  type        = string
  default     = ""
}

variable "line_config_s3_key" {
  description = "S3 key for line-config.json."
  type        = string
  default     = "line-config.json"
}

variable "report_bucket_name" {
  description = "Existing S3 bucket for dashboard reports, archives, and peak files. Leave empty to have this module create its own dashboard bucket (see create_dashboard_bucket) - a PRIVATE bucket served to viewers via CloudFront + Origin Access Control, never a public bucket - or to disable report storage entirely by also setting create_dashboard_bucket = false."
  type        = string
  default     = ""
}

variable "create_dashboard_bucket" {
  description = "When report_bucket_name is empty, create a dedicated private S3 bucket for the dashboard, served over HTTPS by CloudFront via Origin Access Control (no public bucket, compatible with account-level S3 Block Public Access), so the system serves itself with no manual steps. Ignored when report_bucket_name is set."
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete the dashboard S3 bucket even if it still contains objects."
  type        = bool
  default     = false
}

variable "schedule_expression" {
  description = "How frequently the metrics Lambda runs on its own schedule (independent of the quota-monitor schedule)."
  type        = string
  default     = "rate(1 hour)"
}

variable "history_api_url" {
  description = "Optional absolute URL of this API's /quota endpoint. Leave blank to have the module construct it automatically from the REST API ID, region, and stage (no manual second-apply required)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
  default     = 30
}

variable "repo_root" {
  description = "Absolute path to the live-refresh/ directory that contains lambda_function.py and mapper_bridge.py (the source files this module packages into the Lambda deployment zip)."
  type        = string
}

variable "repo_root_top" {
  description = "Absolute path to the top-level repo root that contains connect-resource-mapper.py and consolidated_report.py, which are bundled into the Lambda zip so the scheduled run generates the full consolidated-report dashboard instead of the lightweight fallback."
  type        = string
}

variable "enable_auth" {
  description = "Create the Cognito login gate (user pool, hosted-UI domain, app client, API authorizer) and protect the dashboard + GET /quota. When true but cloudfront_domain is still empty (phase 1), everything is created EXCEPT the Lambda@Edge and its CloudFront association, so CloudFront can come up and emit its domain. Set false to leave the dashboard and API open (no auth)."
  type        = bool
  default     = true
}

variable "cloudfront_domain" {
  description = "The dashboard CloudFront distribution domain (e.g. dxxxx.cloudfront.net) used to build the Cognito app-client callback/logout URLs. This is a plain string, not a resource reference, so it creates no dependency cycle. Leave empty on the first apply (phase 1); after CloudFront is up, read the dashboard domain from output and set it here for the second apply (phase 2) to wire up real callbacks and attach the Lambda@Edge auth."
  type        = string
  default     = ""
}

variable "test_user_email" {
  description = "Optional email address for a bootstrap Cognito user created with message_action SUPPRESS (no invite email). Leave empty to skip. Set the password afterward with the command in the test_user_password_command output."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Common tags merged onto every resource."
  type        = map(string)
  default     = {}
}

variable "alert_sns_topic_arn" {
  description = "SNS topic ARN to notify on Errors/Throttles alarms for the live-refresh metrics Lambda (alarms.tf). The root module wires this to the quota-monitor module's alert topic so both Lambdas page through one topic instead of standing up a second one. Leave empty to create the alarms with no action (visible in the console, but nothing pages)."
  type        = string
  default     = ""
}
