# Mirrors LambdaSecurityGroup in the CFN template. Only created when var.vpc_id
# is set (equivalent to the UseVPC condition).
resource "aws_security_group" "lambda" {
  count = local.use_vpc ? 1 : 0

  name_prefix = "${var.name_prefix}-lambda-sg-"
  description = "Security group for Connect Quota Monitor Lambda function"
  vpc_id      = var.vpc_id

  egress {
    description = "HTTPS outbound for AWS API calls"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name    = "${var.name_prefix}-Lambda-SG"
    Purpose = "ConnectQuotaMonitor"
  })

  lifecycle {
    create_before_destroy = true

    # Fail fast: with vpc_id set but subnet_ids empty, the vpc_config block on
    # aws_lambda_function.quota_monitor (lambda.tf) would otherwise reach AWS
    # with an empty subnet list and fail with an opaque API error mid-apply,
    # after this security group and other resources already exist.
    precondition {
      condition     = length(var.subnet_ids) > 0
      error_message = "subnet_ids must be non-empty when vpc_id is set. The quota-monitor Lambda's vpc_config requires at least one subnet to attach to."
    }
  }
}
