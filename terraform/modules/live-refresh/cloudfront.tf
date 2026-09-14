# Serves the private dashboard bucket publicly over HTTPS via CloudFront with
# Origin Access Control (OAC) - the AWS-standard pattern for static content
# under account-level S3 Block Public Access (no public bucket required).
resource "aws_cloudfront_origin_access_control" "dashboard" {
  count                             = local.create_dashboard ? 1 : 0
  name                              = "${var.name_prefix}-dashboard-oac"
  description                       = "OAC for the Connect quota dashboard bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}


# Security headers applied to every response from the dashboard distribution.
# HSTS pins browsers to HTTPS for future visits; the other headers close off
# MIME-sniffing, clickjacking, and cross-site script/style injection. The CSP
# allows the inline styles/scripts the static dashboard build uses today and
# fetch() calls to the API Gateway execute-api endpoint (any region/account,
# since this module can be deployed into either).
resource "aws_cloudfront_response_headers_policy" "dashboard" {
  count = local.create_dashboard ? 1 : 0
  name  = "${var.name_prefix}-dashboard-security-headers"

  security_headers_config {
    strict_transport_security {
      override                   = true
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      preload                    = true
    }

    content_type_options {
      override = true
    }

    frame_options {
      override     = true
      frame_option = "DENY"
    }

    referrer_policy {
      override        = true
      referrer_policy = "strict-origin-when-cross-origin"
    }

    content_security_policy {
      override                = true
      content_security_policy = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' https:"
    }
  }
}

resource "aws_cloudfront_distribution" "dashboard" {
  count               = local.create_dashboard ? 1 : 0
  enabled             = true
  default_root_object = "index.html"
  comment             = "${var.name_prefix} Connect quota dashboard"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.dashboard[0].bucket_regional_domain_name
    origin_id                = "s3-dashboard"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard[0].id
  }

  # Second origin: the REST API's execute-api endpoint, so the browser can call
  # GET /quota same-origin on the CloudFront domain (cookie-covered) instead of
  # cross-origin straight to execute-api. No OAC here - the Cognito
  # COGNITO_USER_POOLS authorizer on the method (apigateway.tf) is the auth
  # boundary for this origin, not CloudFront/OAC.
  origin {
    domain_name = "${aws_api_gateway_rest_api.dashboard.id}.execute-api.${local.region}.amazonaws.com"
    origin_id   = "apigw-quota"
    origin_path = "/${local.stage_name}"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-dashboard"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    # Managed "CachingDisabled" policy: the dashboard is regenerated hourly and
    # its objects carry no-cache headers, so serve fresh rather than cache.
    cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard[0].id

    # Cognito login gate at viewer-request. Present only in phase 2
    # (local.enable_edge_auth); absent in phase 1 so CloudFront comes up with no
    # edge dependency and emits its domain for the second apply.
    dynamic "lambda_function_association" {
      for_each = local.enable_edge_auth ? [1] : []
      content {
        event_type   = "viewer-request"
        lambda_arn   = aws_lambda_function.edge_auth[0].qualified_arn
        include_body = false
      }
    }
  }

  # /quota routed to API Gateway instead of the S3 origin, so the same
  # Lambda@Edge viewer-request handler below gates it and (once the session
  # cookie verifies) injects the Cognito id_token as an Authorization: Bearer
  # header before the request reaches the COGNITO_USER_POOLS authorizer.
  ordered_cache_behavior {
    path_pattern           = "/quota"
    target_origin_id       = "apigw-quota"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]

    # Managed "CachingDisabled": /quota is per-viewer, auth-gated data and must
    # never be served from the edge cache.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"

    # Managed "AllViewerExceptHostHeader": forwards every query string and
    # header - including the Authorization header the edge Lambda injects -
    # except Host, since API Gateway's custom-origin domain does not expect
    # the viewer's Host header.
    origin_request_policy_id   = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard[0].id

    # Same Cognito login gate as the dashboard behavior: verifies the session
    # cookie and, on success, injects the Bearer token (see edge-auth/index.js).
    # An unauthenticated request is 302'd to the Cognito hosted UI exactly as
    # it is for the dashboard, since it is the same Lambda@Edge function.
    dynamic "lambda_function_association" {
      for_each = local.enable_edge_auth ? [1] : []
      content {
        event_type   = "viewer-request"
        lambda_arn   = aws_lambda_function.edge_auth[0].qualified_arn
        include_body = false
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = merge(local.common_tags, { Purpose = "dashboard-cdn" })
}
