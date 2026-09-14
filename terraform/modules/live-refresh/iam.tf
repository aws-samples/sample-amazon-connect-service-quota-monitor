# Mirrors the auto-generated SAM execution role plus the inline `Policies`
# block on MetricsFunction in the SAM template.
resource "aws_iam_role" "lambda_execution" {
  name = "${var.name_prefix}-live-refresh-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "metrics_inline" {
  name = "LiveRefreshMetricsPolicy"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect   = "Allow"
          Action   = ["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics"]
          Resource = "*"
        },
        {
          # ListServiceQuotas only; the code batches quota lookups instead of
          # calling GetServiceQuota per quota.
          Effect   = "Allow"
          Action   = ["servicequotas:ListServiceQuotas"]
          Resource = "*"
        },
        {
          # Read-only discovery APIs the bundled resource-mapper calls on the
          # scheduled run to build the full consolidated-report dashboard. These
          # match connect-resource-mapper.py's actual calls. Resource "*" because
          # these are account/instance-wide List/Describe/Get APIs.
          Effect = "Allow"
          Action = [
            "connect:ListContactFlows",
            "connect:DescribeContactFlow",
            "connect:ListPhoneNumbersV2",
            "connect:ListLambdaFunctions",
            "connect:ListTrafficDistributionGroups",
            "connect:GetTrafficDistribution",
            "connect:ListBots",
            "lambda:GetFunction",
            "lambda:ListProvisionedConcurrencyConfigs",
            "lex:ListBots",
          ]
          Resource = "*"
        },
      ],
      local.has_s3_config ? [
        {
          Effect   = "Allow"
          Action   = ["s3:GetObject"]
          Resource = "arn:${local.partition}:s3:::${var.line_config_s3_bucket}/${var.line_config_s3_key}"
        }
      ] : [],
      local.has_report_bucket ? [
        {
          Effect   = "Allow"
          Action   = ["s3:PutObject", "s3:GetObject"]
          Resource = "arn:${local.partition}:s3:::${local.effective_report_bucket}/*"
        },
        {
          # Required by the history endpoint's list_objects_v2 call over the
          # archive/ prefix; without it GET /quota?history=1h|1d silently
          # returns empty.
          Effect   = "Allow"
          Action   = ["s3:ListBucket"]
          Resource = "arn:${local.partition}:s3:::${local.effective_report_bucket}"
        }
      ] : [],
    )
  })
}
