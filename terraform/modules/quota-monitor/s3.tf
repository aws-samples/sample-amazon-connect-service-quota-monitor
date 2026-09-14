# --- Access logs bucket -------------------------------------------------------
# Mirrors S3AccessLogsBucket. Created only when S3 storage is enabled, matching
# the CFN's CreateS3Bucket condition.
resource "aws_s3_bucket" "access_logs" {
  count = var.use_s3_storage ? 1 : 0

  bucket        = "${var.name_prefix}-access-logs-${local.account_id}-${local.region}"
  force_destroy = var.force_destroy

  tags = merge(local.common_tags, { Purpose = "ConnectQuotaMonitorAccessLogs" })
}

resource "aws_s3_bucket_versioning" "access_logs" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.access_logs[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.access_logs[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.access_logs[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "access_logs" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.access_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.access_logs[0].arn,
          "${aws_s3_bucket.access_logs[0].arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      }
    ]
  })
}

# --- Metrics/reports bucket ---------------------------------------------------
# Mirrors MetricsS3Bucket.
resource "aws_s3_bucket" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket        = "${var.name_prefix}-metrics-${local.account_id}-${local.region}"
  force_destroy = var.force_destroy

  tags = merge(local.common_tags, { Purpose = "ConnectQuotaMonitor" })
}

resource "aws_s3_bucket_versioning" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.metrics[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.metrics[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.metrics[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_logging" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.metrics[0].id

  target_bucket = aws_s3_bucket.access_logs[0].id
  target_prefix = "access-logs/"
}

resource "aws_s3_bucket_lifecycle_configuration" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.metrics[0].id

  rule {
    id     = "ExpireOldReports"
    status = "Enabled"

    filter {
      prefix = "connect-reports/"
    }

    expiration {
      days = 365
    }
  }

  rule {
    id     = "ExpireOldMetrics"
    status = "Enabled"

    filter {
      prefix = "connect-metrics/"
    }

    expiration {
      days = 365
    }
  }
}

resource "aws_s3_bucket_policy" "metrics" {
  count = var.use_s3_storage ? 1 : 0

  bucket = aws_s3_bucket.metrics[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.metrics[0].arn,
          "${aws_s3_bucket.metrics[0].arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      }
    ]
  })
}
