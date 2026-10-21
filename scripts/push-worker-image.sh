#!/usr/bin/env bash
# Pushes the workers' image to ECR, only if ECR does not have that tag yet.
#
#   scripts/push-worker-image.sh <repository> <tag>
#   scripts/push-worker-image.sh lgdc-worker 3f2a9c01b7de
#
# The tag is a hash of the image's sources (see the Makefile), so a tag that
# is already there is the same image: nothing to build. In an empty account
# the repository does not exist before the first apply, and the apply needs
# the image's tag; a targeted apply creates just the repository first.
set -euo pipefail

repository="$1"
tag="$2"

root="$(cd "$(dirname "$0")/.." && pwd)"
region="${AWS_REGION:-us-east-1}"
account="$(aws sts get-caller-identity --query Account --output text)"
registry="$account.dkr.ecr.$region.amazonaws.com"

if ! aws ecr describe-repositories --region "$region" \
    --repository-names "$repository" >/dev/null 2>&1; then
  echo "creating the ECR repository $repository"
  terraform -chdir="$root/infra" apply -input=false -auto-approve \
    -target=module.batch.aws_ecr_repository.this
fi

if aws ecr describe-images --region "$region" --repository-name "$repository" \
    --image-ids imageTag="$tag" >/dev/null 2>&1; then
  echo "$repository:$tag is already in ECR, nothing to push"
  exit 0
fi

make -C "$root" worker-image WORKER_IMAGE="$repository"
docker tag "$repository:$tag" "$registry/$repository:$tag"
aws ecr get-login-password --region "$region" \
  | docker login --username AWS --password-stdin "$registry"
docker push "$registry/$repository:$tag"
