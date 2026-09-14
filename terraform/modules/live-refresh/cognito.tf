# Cognito login gate for the dashboard. Created only when var.enable_auth is
# true. The Lambda@Edge viewer-request handler (edge-auth.tf) and the API
# Gateway Cognito authorizer (apigateway.tf) both hang off these resources.
#
# Two-phase apply (see README): the app-client callback/logout URLs need the
# CloudFront domain, but CloudFront needs the edge Lambda, which needs Cognito.
# We break the cycle by having the callback URLs use a PLAIN STRING variable
# (var.cloudfront_domain), never a reference to the CloudFront resource. Phase 1
# leaves it empty (placeholder callback, no edge Lambda, CloudFront comes up and
# emits its domain); phase 2 sets it to that domain (real callbacks + edge auth).

locals {
  # Cognito itself (user pool, client, hosted-UI domain, API authorizer) is
  # created whenever auth is enabled.
  enable_auth = var.enable_auth

  # The Lambda@Edge function and its CloudFront association are created only
  # once we know the CloudFront domain (phase 2). Keeping this false in phase 1
  # is what lets phase 1 apply cleanly with no lambda association on CloudFront.
  enable_edge_auth = var.enable_auth && var.cloudfront_domain != ""

  # Real callback/logout URLs once we know the CloudFront domain; a valid
  # placeholder otherwise so the app-client resource is well-formed in phase 1.
  cognito_callback_urls = var.cloudfront_domain != "" ? ["https://${var.cloudfront_domain}/callback"] : ["https://example.com/callback"]
  cognito_logout_urls   = var.cloudfront_domain != "" ? ["https://${var.cloudfront_domain}/"] : ["https://example.com/"]

  # Full hosted-UI base URL, used both by the edge handler config and outputs.
  cognito_hosted_ui_domain = local.enable_auth ? "https://${aws_cognito_user_pool_domain.dashboard[0].domain}.auth.${local.region}.amazoncognito.com" : ""

  # Standard Cognito OIDC issuer for this pool.
  cognito_issuer = local.enable_auth ? "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.dashboard[0].id}" : ""
}

resource "aws_cognito_user_pool" "dashboard" {
  count = local.enable_auth ? 1 : 0

  name                     = "${var.name_prefix}-dashboard-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Not an open signup: only an administrator can create users. This is a
  # small operator-facing dashboard, not a public sign-up surface.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  tags = local.common_tags

  # Fail fast instead of mid-apply: enable_auth eventually attaches a
  # Lambda@Edge function to the dashboard CloudFront distribution
  # (edge-auth.tf), and Lambda@Edge functions can only be created in
  # us-east-1 - CloudFront rejects an edge Lambda association from any other
  # region. This resource (the Cognito user pool) is always created whenever
  # enable_auth is true, in both phases of the two-phase apply, so it is a
  # reliable place to catch the mismatch before Terraform gets partway
  # through creating Cognito/API Gateway/CloudFront resources.
  lifecycle {
    precondition {
      condition     = local.region == "us-east-1"
      error_message = "enable_auth=true requires the live-refresh module's region to be us-east-1, because it eventually creates a Lambda@Edge function (edge-auth.tf) for the CloudFront viewer-request login gate, and CloudFront only accepts Lambda@Edge associations from functions in us-east-1. Either deploy this stack in us-east-1, or set enable_auth=false to run the dashboard without the login gate in another region."
    }
  }
}

resource "aws_cognito_user_pool_domain" "dashboard" {
  count = local.enable_auth ? 1 : 0

  # Domain prefix must be globally unique across all AWS accounts; the account
  # ID keeps it unique without needing a random suffix.
  domain       = "${var.name_prefix}-dash-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.dashboard[0].id
}

resource "aws_cognito_user_pool_client" "dashboard" {
  count = local.enable_auth ? 1 : 0

  name         = "${var.name_prefix}-dashboard-client"
  user_pool_id = aws_cognito_user_pool.dashboard[0].id

  # A confidential client: the edge Lambda holds the secret and uses it for the
  # Basic-auth token exchange. The secret never reaches the browser.
  generate_secret = true

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = local.cognito_callback_urls
  logout_urls   = local.cognito_logout_urls

  # Token lifetimes: id/access token match the edge cookie Max-Age (1h).
  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 30
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }
}

# Optional bootstrap user so the dashboard is reachable without opening the
# console. Skipped when test_user_email is empty. SUPPRESS keeps Cognito from
# emailing an invite; set the password yourself (see the output below).
resource "aws_cognito_user" "test_user" {
  count = local.enable_auth && var.test_user_email != "" ? 1 : 0

  user_pool_id   = aws_cognito_user_pool.dashboard[0].id
  username       = var.test_user_email
  message_action = "SUPPRESS"

  attributes = {
    email          = var.test_user_email
    email_verified = "true"
  }
}

# --- API Gateway Cognito authorizer ------------------------------------------
# Protects GET /quota (wired in apigateway.tf). Created with the pool so the
# method can reference it whenever auth is enabled.
resource "aws_api_gateway_authorizer" "cognito" {
  count = local.enable_auth ? 1 : 0

  name          = "${var.name_prefix}-cognito-authorizer"
  rest_api_id   = aws_api_gateway_rest_api.dashboard.id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [aws_cognito_user_pool.dashboard[0].arn]
}
