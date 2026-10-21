# The precomputed comparison: one partition per followed branch and radius,
# written by the precompute Lambda and read by the API (app/shared/precomputed.py
# has the keys). The TTL deletes the old days instead of a job. Encrypted at
# rest with the AWS-managed aws/dynamodb key.
#
# A derived read layer, rebuilt from Postgres at will: no backups, so no
# point-in-time recovery.
#tfsec:ignore:aws-dynamodb-enable-recovery
resource "aws_dynamodb_table" "this" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  # Epoch seconds: app/shared/precomputed.py sets it on every item but META.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
