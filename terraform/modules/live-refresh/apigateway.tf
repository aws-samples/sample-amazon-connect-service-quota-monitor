# Mirrors the AWS::Serverless::Api (DashboardApi) implicit expansion plus the
# Api event source on MetricsFunction in the SAM template.
#
# Current security posture: GET /quota is protected by a Cognito user-pool
# authorizer (see aws_api_gateway_authorizer.cognito) whenever var.enable_auth
# is true (the default) - the viewer must present a valid Cognito access token.
# OPTIONS /quota is left open (authorization = NONE) purely to serve the CORS
# preflight response; it returns no data. An API key is deliberately not used:
# a key embedded in static browser JavaScript protects nothing. Abuse and
# runaway cost are additionally bounded by the stage-level throttle below
# (rate 10 / burst 20).
resource "aws_api_gateway_rest_api" "dashboard" {
  name        = "${var.name_prefix}-live-refresh-api"
  description = "Connect Operations Dashboard - Live Metrics API"

  tags = local.common_tags
}

resource "aws_api_gateway_resource" "quota" {
  rest_api_id = aws_api_gateway_rest_api.dashboard.id
  parent_id   = aws_api_gateway_rest_api.dashboard.root_resource_id
  path_part   = "quota"
}

# --- GET /quota (AWS_PROXY to the Lambda) ------------------------------------

# GET /quota is protected by the Cognito user-pool authorizer when
# var.enable_auth is true, and left open (NONE) otherwise. OPTIONS stays NONE
# so the CORS preflight still works. The in-page 7-day trend fetch
# (${API_ENDPOINT}?history=7d) will need the viewer's token once auth is on;
# the dashboard handler already tolerates a failed fetch (see README).
resource "aws_api_gateway_method" "get_quota" {
  rest_api_id   = aws_api_gateway_rest_api.dashboard.id
  resource_id   = aws_api_gateway_resource.quota.id
  http_method   = "GET"
  authorization = local.enable_auth ? "COGNITO_USER_POOLS" : "NONE"
  authorizer_id = local.enable_auth ? aws_api_gateway_authorizer.cognito[0].id : null
}

resource "aws_api_gateway_integration" "get_quota" {
  rest_api_id             = aws_api_gateway_rest_api.dashboard.id
  resource_id             = aws_api_gateway_resource.quota.id
  http_method             = aws_api_gateway_method.get_quota.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.metrics.invoke_arn
}

resource "aws_lambda_permission" "allow_apigateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.metrics.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.dashboard.execution_arn}/*/GET/quota"
}

# --- OPTIONS /quota (MOCK, CORS preflight) -----------------------------------
# The handler returns CORS headers itself for GET; this MOCK integration
# provides the OPTIONS preflight response the SAM template's `Cors:` block on
# DashboardApi generates implicitly.

resource "aws_api_gateway_method" "options_quota" {
  rest_api_id   = aws_api_gateway_rest_api.dashboard.id
  resource_id   = aws_api_gateway_resource.quota.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "options_quota" {
  rest_api_id = aws_api_gateway_rest_api.dashboard.id
  resource_id = aws_api_gateway_resource.quota.id
  http_method = aws_api_gateway_method.options_quota.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "options_quota" {
  rest_api_id = aws_api_gateway_rest_api.dashboard.id
  resource_id = aws_api_gateway_resource.quota.id
  http_method = aws_api_gateway_method.options_quota.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Headers" = true
  }
}

resource "aws_api_gateway_integration_response" "options_quota" {
  rest_api_id = aws_api_gateway_rest_api.dashboard.id
  resource_id = aws_api_gateway_resource.quota.id
  http_method = aws_api_gateway_method.options_quota.http_method
  status_code = aws_api_gateway_method_response.options_quota.status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type'"
  }

  depends_on = [aws_api_gateway_integration.options_quota]
}

# --- Deployment / stage -------------------------------------------------------

resource "aws_api_gateway_deployment" "dashboard" {
  rest_api_id = aws_api_gateway_rest_api.dashboard.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.quota.id,
      aws_api_gateway_method.get_quota.id,
      aws_api_gateway_method.get_quota.authorization,
      local.enable_auth ? aws_api_gateway_authorizer.cognito[0].id : "none",
      aws_api_gateway_integration.get_quota.id,
      aws_api_gateway_method.options_quota.id,
      aws_api_gateway_integration.options_quota.id,
      aws_api_gateway_integration_response.options_quota.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.get_quota,
    aws_api_gateway_integration.options_quota,
    aws_api_gateway_integration_response.options_quota,
  ]
}

resource "aws_api_gateway_stage" "prod" {
  rest_api_id   = aws_api_gateway_rest_api.dashboard.id
  deployment_id = aws_api_gateway_deployment.dashboard.id
  stage_name    = local.stage_name

  tags = local.common_tags
}

# Stage-level throttling (rate 10 / burst 20), matching MethodSettings on
# DashboardApi in the SAM template.
resource "aws_api_gateway_method_settings" "throttle" {
  rest_api_id = aws_api_gateway_rest_api.dashboard.id
  stage_name  = aws_api_gateway_stage.prod.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = 10
    throttling_burst_limit = 20
  }
}
