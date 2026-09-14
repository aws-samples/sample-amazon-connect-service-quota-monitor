# KMS key for SNS topic encryption (mirrors SNSKMSKey / SNSKMSKeyAlias in the CFN template).
resource "aws_kms_key" "sns" {
  description         = "KMS key for SNS topic encryption - Connect Quota Monitor"
  enable_key_rotation = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Enable IAM User Permissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "Allow SNS service"
        Effect    = "Allow"
        Principal = { Service = "sns.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_kms_alias" "sns" {
  name          = "alias/${var.name_prefix}-sns-key"
  target_key_id = aws_kms_key.sns.key_id
}

# KMS key for DynamoDB table encryption (mirrors DynamoDBKMSKey / DynamoDBKMSKeyAlias).
resource "aws_kms_key" "dynamodb" {
  count = var.use_dynamodb ? 1 : 0

  description         = "KMS key for DynamoDB table encryption"
  enable_key_rotation = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Enable IAM User Permissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "Allow DynamoDB service"
        Effect    = "Allow"
        Principal = { Service = "dynamodb.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_kms_alias" "dynamodb" {
  count = var.use_dynamodb ? 1 : 0

  name          = "alias/${var.name_prefix}-dynamodb-key"
  target_key_id = aws_kms_key.dynamodb[0].key_id
}

# KMS key for Lambda environment variable encryption (mirrors LambdaKMSKey / LambdaKMSKeyAlias).
resource "aws_kms_key" "lambda" {
  description         = "KMS key for Lambda environment variables encryption"
  enable_key_rotation = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Enable IAM User Permissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "Allow Lambda service"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_kms_alias" "lambda" {
  name          = "alias/${var.name_prefix}-lambda-key"
  target_key_id = aws_kms_key.lambda.key_id
}
