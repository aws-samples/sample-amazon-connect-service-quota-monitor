data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  has_s3_config = var.line_config_s3_bucket != ""

  # Create a dedicated public dashboard bucket only when the caller did not
  # supply their own report bucket.
  create_dashboard = var.report_bucket_name == "" && var.create_dashboard_bucket

  # The bucket the Lambda actually reads/writes: a caller-supplied bucket if
  # given, else the dashboard bucket this module creates, else none.
  effective_report_bucket = var.report_bucket_name != "" ? var.report_bucket_name : (local.create_dashboard ? aws_s3_bucket.dashboard[0].id : "")

  has_report_bucket = local.effective_report_bucket != ""

  stage_name = "prod"

  # Constructed without any dependency on the Lambda function or the stage
  # deployment, so setting this as the Lambda's API_ENDPOINT env var does not
  # create a Function<->Api circular dependency (unlike the SAM template,
  # which requires a manual two-pass deploy via the HistoryApiUrl parameter).
  constructed_api_endpoint = "https://${aws_api_gateway_rest_api.dashboard.id}.execute-api.${local.region}.amazonaws.com/${local.stage_name}/quota"

  # Same-origin path once the dashboard is served through CloudFront and a
  # domain is known: the browser's in-page trend fetch then hits
  # https://<cloudfront_domain>/quota, which the /quota ordered_cache_behavior
  # (cloudfront.tf) routes to the API Gateway origin behind the same
  # Lambda@Edge login gate that protects the dashboard, so the session cookie
  # covers it and the edge Lambda injects the Bearer token. var.cloudfront_domain
  # is a plain string variable (never a resource reference), so this introduces
  # no dependency cycle. Falls back to the cross-origin execute-api URL when
  # there is no dashboard bucket (nothing for CloudFront to front) or the
  # domain is not yet known (phase 1 of the two-phase apply).
  same_origin_api_endpoint = local.create_dashboard && var.cloudfront_domain != "" ? "https://${var.cloudfront_domain}/quota" : local.constructed_api_endpoint

  api_endpoint = var.history_api_url != "" ? var.history_api_url : local.same_origin_api_endpoint

  common_tags = merge(
    var.tags,
    {
      Name = var.name_prefix
    }
  )
}
