variable "name_prefix" {
  description = "Prefix applied to every resource name in this module."
  type        = string
}

variable "notification_email" {
  description = "Optional email address subscribed to the alert SNS topic. Leave empty to skip the subscription."
  type        = string
  default     = ""
}

variable "threshold_percentage" {
  description = "Percentage threshold for quota utilization alerts (1-99)."
  type        = number
  default     = 80
}

variable "schedule_expression" {
  description = "EventBridge schedule expression that triggers the quota-monitor Lambda."
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
  description = "Lambda function timeout in seconds (60-900)."
  type        = number
  default     = 300
}

variable "lambda_memory" {
  description = "Lambda function memory in MB (256-10240)."
  type        = number
  default     = 512
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
  default     = 30
}

variable "vpc_id" {
  description = "Optional VPC ID to deploy the Lambda into. Leave empty for no VPC."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnet IDs for the Lambda when vpc_id is set."
  type        = list(string)
  default     = []
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete S3 buckets that still contain objects."
  type        = bool
  default     = false
}

variable "dynamodb_ttl_days" {
  description = "Number of days before a DynamoDB metrics item expires via the table's ttl attribute (dynamodb.tf). Passed to the Lambda as DYNAMODB_TTL_DAYS."
  type        = number
  default     = 90
}

variable "send_allclear_heartbeat" {
  description = "When true, the Lambda publishes an SNS notification even when no quota is over threshold, so the absence of alerts is confirmed rather than assumed. Passed to the Lambda as SEND_ALLCLEAR_HEARTBEAT."
  type        = bool
  default     = false
}

variable "repo_root" {
  description = "Absolute path to the repository root that contains lambda_function.py and quota_definitions.json (the source files this module packages into the Lambda deployment zip)."
  type        = string
}

variable "tags" {
  description = "Common tags merged onto every resource."
  type        = map(string)
  default     = {}
}
