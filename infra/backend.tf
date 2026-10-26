# Partial configuration: the bucket (lgdc-tfstate-<account_id>) depends on the
# account, so `make init` creates it if missing and passes it with
# -backend-config. use_lockfile is S3's native lock, no DynamoDB table.
terraform {
  backend "s3" {
    key          = "terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
