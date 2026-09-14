# Lambda@Edge that enforces the Cognito login gate at CloudFront viewer-request
# time. Created only in phase 2 (local.enable_edge_auth: auth on AND the
# CloudFront domain is known). See edge-auth/index.js for the handler.
#
# Lambda@Edge constraint: the function must live in us-east-1. This module's
# provider region is var.region (default us-east-1); deploying the edge auth in
# any other region will fail at apply. Keep this stack in us-east-1.

# Renders config.json from Cognito outputs and zips it alongside index.js. No
# env vars are used (Lambda@Edge forbids them), so config travels in the bundle.
data "archive_file" "edge_auth" {
  count       = local.enable_edge_auth ? 1 : 0
  type        = "zip"
  output_path = "${path.module}/build/edge_auth.zip"

  source {
    content  = file("${path.module}/edge-auth/index.js")
    filename = "index.js"
  }

  source {
    content = templatefile("${path.module}/edge-auth/config.json.tftpl", {
      user_pool_id      = aws_cognito_user_pool.dashboard[0].id
      client_id         = aws_cognito_user_pool_client.dashboard[0].id
      client_secret     = aws_cognito_user_pool_client.dashboard[0].client_secret
      cognito_domain    = local.cognito_hosted_ui_domain
      region            = local.region
      issuer            = local.cognito_issuer
      cloudfront_domain = var.cloudfront_domain
    })
    filename = "config.json"
  }
}

# Execution role assumable by BOTH lambda.amazonaws.com and
# edgelambda.amazonaws.com, as required for replicated edge functions.
resource "aws_iam_role" "edge_auth" {
  count = local.enable_edge_auth ? 1 : 0

  name = "${var.name_prefix}-edge-auth-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = ["lambda.amazonaws.com", "edgelambda.amazonaws.com"]
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "edge_auth_basic" {
  count      = local.enable_edge_auth ? 1 : 0
  role       = aws_iam_role.edge_auth[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "edge_auth" {
  count = local.enable_edge_auth ? 1 : 0

  function_name = "${var.name_prefix}-edge-auth"
  description   = "Cognito login gate (viewer-request) for the dashboard CloudFront distribution"

  filename         = data.archive_file.edge_auth[0].output_path
  source_code_hash = data.archive_file.edge_auth[0].output_base64sha256

  handler = "index.handler"
  runtime = "nodejs20.x"
  role    = aws_iam_role.edge_auth[0].arn

  # Edge functions cannot use env vars and are limited to 5s at viewer-request.
  timeout = 5

  # Required so CloudFront can reference a specific (non-$LATEST) version.
  publish = true

  tags = merge(local.common_tags, { ManagedBy = "Terraform" })

  depends_on = [aws_iam_role_policy_attachment.edge_auth_basic]
}
