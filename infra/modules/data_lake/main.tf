# The data bucket: raw/ (what arrives, one file per chain), curated/ (the
# validated prices as Parquet, the history) and rejected/ (what could not be
# loaded, and why). Versioned, so a file overwritten by a reprocess is still
# there, and every version has its own S3 event: the workers' inbox keys on
# it (<key>#<versionId>).
#
# The bucket itself is not here: `make apply` creates it with the CLI
# (scripts/buckets.sh) and `make destroy` deletes it. The Learner Lab
# denies s3:GetBucketObjectLockConfiguration, which the provider's
# aws_s3_bucket reads on every refresh, and so does the Registry's
# s3-bucket module (docs/adr/0002-bucket-de-datos-fuera-de-terraform.md).
# Everything else about the bucket is managed here.

locals {
  bucket_arn = format("arn:aws:s3:::%s", var.bucket)
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = var.bucket

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = var.bucket

  dynamic "rule" {
    for_each = var.lifecycle_rules

    content {
      id     = trimsuffix(rule.key, "/")
      status = "Enabled"

      filter {
        prefix = rule.key
      }

      abort_incomplete_multipart_upload {
        days_after_initiation = 1
      }

      dynamic "expiration" {
        for_each = rule.value.expiration_days[*]

        content {
          days = expiration.value
        }
      }

      dynamic "transition" {
        for_each = rule.value.transition_days[*]

        content {
          days          = transition.value
          storage_class = "STANDARD_IA"
        }
      }

      noncurrent_version_expiration {
        noncurrent_days = rule.value.noncurrent_version_expiration_days
      }
    }
  }

  # The rules on noncurrent versions need versioning on first.
  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = var.bucket

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The portal uploads a merchant's price list straight here, with a presigned
# POST: a cross-origin request, whose answer the browser hands the portal
# only if the bucket's CORS allows the portal's origin. The signed policy is
# what authorizes the write; the origin only lets the portal see it worked.
resource "aws_s3_bucket_cors_configuration" "this" {
  bucket = var.bucket

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["*"]
    max_age_seconds = 3600
  }
}

# Nothing reaches the bucket without TLS.
resource "aws_s3_bucket_policy" "this" {
  bucket = var.bucket

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [local.bucket_arn, format("%s/*", local.bucket_arn)]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  # Both change the bucket's policy state; S3 rejects them at once.
  depends_on = [aws_s3_bucket_public_access_block.this]
}
