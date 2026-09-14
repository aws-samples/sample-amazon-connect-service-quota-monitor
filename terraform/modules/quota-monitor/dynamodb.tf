# Mirrors MetricsDynamoDBTable in the CFN template.
resource "aws_dynamodb_table" "metrics" {
  count = var.use_dynamodb ? 1 : 0

  name         = "${var.name_prefix}-metrics"
  billing_mode = "PAY_PER_REQUEST"

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  attribute {
    name = "instance_id"
    type = "S"
  }

  hash_key  = "id"
  range_key = "timestamp"

  # NOTE: the source CFN template sets ProvisionedThroughput (5/5) on this GSI
  # while the table BillingMode is PAY_PER_REQUEST. The DynamoDB API rejects
  # ProvisionedThroughput on any index when the table is PAY_PER_REQUEST, so
  # that combination in the CFN would fail at stack-create time. This port
  # intentionally omits read/write capacity here to produce a table that
  # actually deploys; see the module README "Parity notes".
  global_secondary_index {
    name            = "InstanceIdIndex"
    hash_key        = "instance_id"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.dynamodb[0].arn
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, { Purpose = "ConnectQuotaMonitor" })
}
