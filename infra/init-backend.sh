#!/usr/bin/env bash
# Creates the state bucket if it does not exist yet, then runs terraform init
# against it. The bucket carries the account ID, so it is unique and needs no
# configuration: Deploy works on an empty account.
#
#   infra/init-backend.sh
set -euo pipefail

infra="$(cd "$(dirname "$0")" && pwd)"

# The same region backend.tf names.
region="us-east-1"
account="$(aws sts get-caller-identity --query Account --output text)"
bucket="lgdc-tfstate-$account"

if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
  echo "State bucket $bucket already exists."
else
  echo "Creating state bucket $bucket."
  aws s3api create-bucket --bucket "$bucket" --region "$region" >/dev/null
  aws s3api wait bucket-exists --bucket "$bucket"
fi

# Every time, not only on creation: both calls are idempotent, and a run cut
# short right after create-bucket would otherwise leave the bucket without them.
aws s3api put-bucket-versioning --bucket "$bucket" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$bucket" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

terraform -chdir="$infra" init -input=false -backend-config="bucket=$bucket"
