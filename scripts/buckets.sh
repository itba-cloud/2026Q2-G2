#!/usr/bin/env bash
# Creates or deletes the two buckets Terraform does not create:
# lgdc-data-<account_id> (raw/, curated/, rejected/) and
# lgdc-portal-<account_id> (the portal), the names infra/locals.tf builds.
# Terraform manages everything about them but the buckets themselves: the
# Learner Lab denies s3:GetBucketObjectLockConfiguration, which the provider
# reads on every refresh of an aws_s3_bucket
# (docs/adr/0002-bucket-de-datos-fuera-de-terraform.md).
#
#   scripts/buckets.sh create   before the apply; skips a bucket that exists
#   scripts/buckets.sh delete   after the destroy: every version, then the bucket
set -euo pipefail

action="${1:-}"

region="${AWS_REGION:-us-east-1}"
account="$(aws sts get-caller-identity --query Account --output text)"
buckets=("lgdc-data-$account" "lgdc-portal-$account")

exists() {
  aws s3api head-bucket --bucket "$1" 2>/dev/null
}

create() {
  local bucket="$1"
  if exists "$bucket"; then
    echo "Bucket $bucket already exists."
    return
  fi
  echo "Creating bucket $bucket."
  if [ "$region" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$bucket" --region "$region" >/dev/null
  else
    aws s3api create-bucket --bucket "$bucket" --region "$region" \
      --create-bucket-configuration LocationConstraint="$region" >/dev/null
  fi
  aws s3api wait bucket-exists --bucket "$bucket"
}

delete() {
  local bucket="$1" batch
  if ! exists "$bucket"; then
    echo "Bucket $bucket does not exist."
    return
  fi
  echo "Emptying bucket $bucket."
  # The data bucket is versioned: deleting a key only adds a delete marker,
  # so every version and every marker goes, a thousand per call
  # (delete-objects' limit). On the portal's, unversioned, the versions are
  # just the objects.
  while true; do
    batch="$(aws s3api list-object-versions --bucket "$bucket" --max-items 1000 \
      --query '{Objects: [Versions, DeleteMarkers][][].{Key: Key, VersionId: VersionId}, Quiet: `true`}' \
      --output json)"
    if [ "$(echo "$batch" | python3 -c 'import json, sys; print(len(json.load(sys.stdin)["Objects"] or []))')" = 0 ]; then
      break
    fi
    aws s3api delete-objects --bucket "$bucket" --delete "$batch" >/dev/null
  done
  aws s3api delete-bucket --bucket "$bucket"
  echo "Deleted bucket $bucket."
}

case "$action" in
  create | delete)
    for bucket in "${buckets[@]}"; do
      "$action" "$bucket"
    done
    ;;

  *)
    echo "usage: $0 create|delete" >&2
    exit 2
    ;;
esac
