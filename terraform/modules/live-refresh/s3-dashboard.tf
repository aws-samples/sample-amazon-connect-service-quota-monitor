# Private bucket that stores the generated dashboard (index.html) plus
# latest.json / archive / peaks. It is NOT public: account-level S3 Block Public
# Access forbids public bucket policies. The dashboard is served publicly by
# CloudFront via Origin Access Control (see cloudfront.tf), reading from this
# private bucket. Created only when the caller did not supply report_bucket_name.
resource "aws_s3_bucket" "dashboard" {
  count         = local.create_dashboard ? 1 : 0
  bucket        = "${var.name_prefix}-dashboard-${local.account_id}-${local.region}"
  force_destroy = var.force_destroy
  tags          = merge(local.common_tags, { Purpose = "dashboard-origin" })
}

resource "aws_s3_bucket_versioning" "dashboard" {
  count  = local.create_dashboard ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  count  = local.create_dashboard ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Fully private: all public access blocked. CloudFront reaches the objects via
# OAC, not via any public path.
resource "aws_s3_bucket_public_access_block" "dashboard" {
  count                   = local.create_dashboard ? 1 : 0
  bucket                  = aws_s3_bucket.dashboard[0].id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# Seed a placeholder index.html so CloudFront serves something (HTTP 200)
# instead of a 403 in the window before the first scheduled Lambda run writes
# the real dashboard. The scheduled Lambda overwrites this object on its first
# run; lifecycle.ignore_changes stops Terraform from reverting it afterwards.
resource "aws_s3_object" "dashboard_placeholder" {
  count        = local.create_dashboard ? 1 : 0
  bucket       = aws_s3_bucket.dashboard[0].id
  key          = "index.html"
  content_type = "text/html"
  content      = <<-HTML
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Dashboard is being generated</title>
      </head>
      <body>
        <h1>Dashboard is being generated</h1>
        <p>Check back after the first scheduled run.</p>
      </body>
    </html>
  HTML

  lifecycle {
    # The scheduled Lambda is the real owner of this object after the first run.
    # ignore_changes = all: once created, Terraform never re-PUTs this object, so
    # a later apply cannot clobber the rich dashboard the Lambda has written back
    # over the placeholder (ignoring only [content] still re-PUT it when another
    # attribute changed).
    ignore_changes = all
  }
}

# Grants only the CloudFront distribution (via OAC) read access, scoped by
# SourceArn. This is a service-principal policy, not a public policy, so it is
# permitted under account BlockPublicPolicy. Also denies non-TLS access.
resource "aws_s3_bucket_policy" "dashboard" {
  count  = local.create_dashboard ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dashboard[0].arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.dashboard[0].arn
          }
        }
      },
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.dashboard[0].arn,
          "${aws_s3_bucket.dashboard[0].arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}
