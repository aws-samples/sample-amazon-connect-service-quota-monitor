data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  use_vpc = var.vpc_id != ""

  common_tags = merge(
    var.tags,
    {
      Name = var.name_prefix
    }
  )
}
