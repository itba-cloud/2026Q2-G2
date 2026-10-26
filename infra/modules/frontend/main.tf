# The portal's bucket: the built app (Deploy syncs app/frontend/dist into it)
# and the configuration it reads when it starts. Served over HTTPS by S3's
# REST endpoint (https://<bucket>.s3.<region>.amazonaws.com/index.html), so
# the tokens never travel over plain HTTP; the website endpoint only speaks
# HTTP. The portal routes with a HashRouter, so index.html is the only page
# S3 ever has to serve.
#
# Not versioned: the portal's versions are in git. The bucket itself is not
# here either, for the same reason as the data bucket's: `make apply` creates
# it with the CLI (scripts/buckets.sh) because the Learner Lab denies a read
# the provider makes on every aws_s3_bucket
# (docs/adr/0002-bucket-de-datos-fuera-de-terraform.md).

locals {
  bucket_arn = format("arn:aws:s3:::%s", var.bucket)
}

# The objects are public: it is a website. Listing is not.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = var.bucket

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "this" {
  bucket = var.bucket

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicRead"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = format("%s/*", local.bucket_arn)
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.bucket_arn, format("%s/*", local.bucket_arn)]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })

  # A public policy is rejected until the block above allows it.
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# What the portal reads before it renders: where the API and the Hosted UI
# are. Generated here, so the same build works in any account; Deploy's sync
# leaves it alone.
resource "aws_s3_object" "config" {
  bucket        = var.bucket
  key           = "config.json"
  content       = jsonencode(var.config)
  content_type  = "application/json"
  cache_control = "no-cache"

  depends_on = [aws_s3_bucket_policy.this]
}
