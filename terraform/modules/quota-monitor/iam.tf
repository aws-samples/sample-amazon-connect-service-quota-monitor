# --- Storage policies (mirror S3StoragePolicy / DynamoDBStoragePolicy) ------

resource "aws_iam_policy" "s3_storage" {
  count = var.use_s3_storage ? 1 : 0

  name = "${var.name_prefix}-S3StoragePolicy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.metrics[0].arn,
          "${aws_s3_bucket.metrics[0].arn}/*",
        ]
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_policy" "dynamodb_storage" {
  count = var.use_dynamodb ? 1 : 0

  name = "${var.name_prefix}-DynamoDBStoragePolicy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:DescribeTable",
        ]
        Resource = [
          aws_dynamodb_table.metrics[0].arn,
          "${aws_dynamodb_table.metrics[0].arn}/index/*",
        ]
      }
    ]
  })

  tags = local.common_tags
}

# --- Lambda execution role ----------------------------------------------------
# Mirrors LambdaExecutionRole in the CFN template.

resource "aws_iam_role" "lambda_execution" {
  name = "${var.name_prefix}-EnhancedLambdaRole"

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

resource "aws_iam_role_policy_attachment" "vpc_access" {
  count = local.use_vpc ? 1 : 0

  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "s3_storage" {
  count = var.use_s3_storage ? 1 : 0

  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.s3_storage[0].arn
}

resource "aws_iam_role_policy_attachment" "dynamodb_storage" {
  count = var.use_dynamodb ? 1 : 0

  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.dynamodb_storage[0].arn
}

# Mirrors the inline EnhancedConnectQuotaMonitorPolicy statement block.
# IAM scoping note (carried over from the CFN template): the discovery
# statements below use Resource "*" because they are read-only List/Describe/Get
# APIs that must run before any instance ARN is known (you cannot scope
# ListInstances to a specific instance). sns:Publish and the SQS/KMS statements
# below are scoped to the specific resources this module creates.
resource "aws_iam_role_policy" "quota_monitor_inline" {
  name = "EnhancedConnectQuotaMonitorPolicy"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConnectCorePermissions"
        Effect = "Allow"
        Action = [
          "connect:ListInstances",
          "connect:ListUsers",
          "connect:ListQueues",
          "connect:ListPhoneNumbers",
          "connect:ListPhoneNumbersV2",
          "connect:ListHoursOfOperations",
          "connect:ListContactFlows",
          "connect:ListContactFlowModules",
          "connect:ListRoutingProfiles",
          "connect:ListSecurityProfiles",
          "connect:ListQuickConnects",
          "connect:ListAgentStatuses",
          "connect:ListPrompts",
          "connect:ListTaskTemplates",
          "connect:ListEvaluationForms",
          "connect:ListLambdaFunctions",
          "connect:ListBots",
          "connect:ListIntegrationAssociations",
          "connect:ListPredefinedAttributes",
          "connect:DescribeUserHierarchyStructure",
        ]
        Resource = "*"
      },
      {
        Sid    = "ConnectCasesPermissions"
        Effect = "Allow"
        Action = [
          "cases:ListDomains",
          "cases:ListFields",
          "cases:ListTemplates",
        ]
        Resource = "*"
      },
      {
        Sid      = "ConnectCampaignsPermissions"
        Effect   = "Allow"
        Action   = ["connect-campaigns:ListCampaigns"]
        Resource = "*"
      },
      {
        # GetServiceQuota intentionally omitted: the monitor batches applied
        # limits via ListServiceQuotas (one call per service) instead of one
        # GetServiceQuota call per quota.
        Sid      = "ServiceQuotasPermissions"
        Effect   = "Allow"
        Action   = ["servicequotas:ListServiceQuotas", "servicequotas:ListServices"]
        Resource = "*"
      },
      {
        Sid      = "CloudWatchPermissions"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics", "cloudwatch:GetMetricData"]
        Resource = "*"
      },
      {
        Sid      = "STSPermissions"
        Effect   = "Allow"
        Action   = ["sts:GetCallerIdentity"]
        Resource = "*"
      },
      {
        Sid      = "SNSPermissions"
        Effect   = "Allow"
        Action   = ["sns:Publish", "sns:GetTopicAttributes", "sns:ListSubscriptionsByTopic"]
        Resource = aws_sns_topic.alerts.arn
      },
      {
        Sid      = "SNSListPermissions"
        Effect   = "Allow"
        Action   = ["sns:ListTopics"]
        Resource = "*"
      },
      {
        Sid      = "SQSPermissions"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.dlq.arn
      },
      {
        Sid      = "KMSPermissions"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.lambda.arn, aws_kms_key.sns.arn]
      },
    ]
  })
}

# Mirrors the conditional DynamoDBKMSPolicy inline statement.
resource "aws_iam_role_policy" "dynamodb_kms" {
  count = var.use_dynamodb ? 1 : 0

  name = "DynamoDBKMSPolicy"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBKMSPermissions"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = aws_kms_key.dynamodb[0].arn
      }
    ]
  })
}
