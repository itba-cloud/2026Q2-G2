# Credentials come from AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and
# AWS_SESSION_TOKEN, never from the code.
provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}
