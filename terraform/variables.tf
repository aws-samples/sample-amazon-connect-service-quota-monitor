variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  # Capped at 20 chars. Two downstream names constrain it:
  #   - Lambda function name "$${name_prefix}-EnhancedConnectQuotaMonitor" - the
  #     28-char suffix against Lambda's 64-char function-name limit leaves 36
  #     usable chars for the prefix.
  #   - S3 bucket name "$${name_prefix}-access-logs-$${account_id}-$${region}" -
  #     the 13-char literal plus a 12-digit account id plus up to a 14-char
  #     region name (e.g. ap-northeast-3), against S3's 63-char bucket-name
  #     limit, leaves as few as ~23 usable chars in long-named regions.
  # 20 stays comfortably under both.
  description = "Prefix applied to every resource name/tag so multiple copies of this solution can coexist in one account. Must be DNS-safe: it is interpolated into S3 bucket names and the Cognito hosted-UI domain. Limited to 20 characters (see comment above) so it never overflows the quota-monitor Lambda function name or the S3 bucket names it feeds."
  type        = string
  default     = "cs360-cqm"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must be 1-20 chars, lowercase letters, digits, and hyphens only. 20 is the hard cap: longer values can overflow the quota-monitor Lambda function name (64-char AWS limit minus the 28-char \"-EnhancedConnectQuotaMonitor\" suffix) and, in long-named regions, the S3 access-logs bucket name (63-char S3 limit minus the account id and region suffix)."
  }
}

variable "notification_email" {
  description = "Optional email address subscribed to the alert SNS topic. Leave empty to skip the subscription."
  type        = string
  default     = ""
}

variable "connect_instance_id" {
  description = "Optional Amazon Connect instance ID used by the live-refresh dashboard API. Leave empty if not deploying the live-refresh module against a specific instance yet."
  type        = string
  default     = ""
}

variable "threshold_percentage" {
  description = "Percentage threshold for quota utilization alerts (1-99)."
  type        = number
  default     = 80

  validation {
    condition     = var.threshold_percentage >= 1 && var.threshold_percentage <= 99
    error_message = "threshold_percentage must be between 1 and 99."
  }
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for the quota monitor and live-refresh Lambda functions."
  type        = string
  default     = "rate(1 hour)"
}

variable "use_dynamodb" {
  description = "Enable DynamoDB storage for metrics and reports."
  type        = bool
  default     = true
}

variable "use_s3_storage" {
  description = "Enable S3 storage for metrics and reports."
  type        = bool
  default     = true
}

variable "lambda_timeout" {
  description = "Quota monitor Lambda function timeout in seconds (60-900)."
  type        = number
  default     = 600

  validation {
    condition     = var.lambda_timeout >= 60 && var.lambda_timeout <= 900
    error_message = "lambda_timeout must be between 60 and 900 seconds."
  }
}

variable "lambda_memory" {
  description = "Quota monitor Lambda function memory in MB (256-10240)."
  type        = number
  default     = 512

  validation {
    condition     = var.lambda_memory >= 256 && var.lambda_memory <= 10240
    error_message = "lambda_memory must be between 256 and 10240 MB."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days for both Lambda functions."
  type        = number
  default     = 30
}

variable "vpc_id" {
  description = "Optional VPC ID to deploy the quota-monitor Lambda into. Leave empty for no VPC."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnet IDs for the quota-monitor Lambda when vpc_id is set. Required if vpc_id is non-empty."
  type        = list(string)
  default     = []
}

variable "dynamodb_ttl_days" {
  description = "Days before a quota-monitor DynamoDB metrics item expires via TTL. Forwarded to the Lambda as DYNAMODB_TTL_DAYS."
  type        = number
  default     = 90
}

variable "send_allclear_heartbeat" {
  description = "When true, the quota-monitor Lambda sends an SNS notification even when nothing is over threshold, confirming the check ran. Forwarded to the Lambda as SEND_ALLCLEAR_HEARTBEAT."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Common tags merged onto every resource."
  type        = map(string)
  default     = {}
}

# --- live-refresh dashboard API inputs ---

variable "deploy_live_refresh" {
  description = "Whether to deploy the live-refresh dashboard API module alongside the quota monitor."
  type        = bool
  default     = true
}

variable "line_config_json" {
  description = "Inline JSON line configuration for the live-refresh dashboard (for small configs). Leave empty to use S3 or no line config."
  type        = string
  default     = ""
}

variable "line_config_s3_bucket" {
  description = "S3 bucket containing line-config.json for the live-refresh dashboard (alternative to inline JSON). Leave empty if not used."
  type        = string
  default     = ""
}

variable "line_config_s3_key" {
  description = "S3 key for line-config.json when line_config_s3_bucket is set."
  type        = string
  default     = "line-config.json"
}

variable "create_dashboard_bucket" {
  description = "When report_bucket_name is empty, have the live-refresh module create its own private dashboard bucket and serve it over HTTPS via CloudFront + Origin Access Control (no public bucket; compatible with account-level S3 Block Public Access) so the dashboard is live right after apply with no manual step."
  type        = bool
  default     = true
}

# --- dashboard authentication (Cognito login gate) ---

variable "enable_auth" {
  description = "Enable the Cognito login gate on the live-refresh dashboard and its GET /quota API. See terraform.tfvars.example for the two-phase apply this drives. Set false to leave the dashboard open."
  type        = bool
  default     = true
}

variable "cloudfront_domain" {
  description = "The dashboard CloudFront domain (e.g. dxxxx.cloudfront.net), used to build the Cognito callback/logout URLs. Leave empty on the first apply (phase 1); after CloudFront exists, read live_refresh_dashboard_url, set this to that host, and apply again (phase 2) to activate Cognito callbacks and the Lambda@Edge gate. Plain string, so it creates no dependency cycle."
  type        = string
  default     = ""
}

variable "test_user_email" {
  description = "Optional email for a bootstrap Cognito dashboard user (created without an invite email). Leave empty to skip. Set the password afterward with the terraform output command."
  type        = string
  default     = ""
}

variable "report_bucket_name" {
  description = "Optional existing S3 bucket name for live-refresh dashboard reports, archives, and peak files. Leave empty to auto-generate a name."
  type        = string
  default     = ""
}

variable "history_api_url" {
  description = "Optional absolute URL of the live-refresh /quota endpoint, used to enable the 7-day trend fetch in the fallback dashboard. Leave blank on first apply; the module also self-populates this from its own API Gateway stage URL, so manual wiring is only needed if you want to pin an external value."
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Set true to allow terraform destroy to delete S3 buckets that still contain objects. Defaults to false to avoid accidental data loss."
  type        = bool
  default     = false
}
