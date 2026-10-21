#!/usr/bin/env bash
# Puts the platform admin in Cognito: the user BOOTSTRAP_EMAIL, with the
# permanent password BOOTSTRAP_PASSWORD, in the platform-admin group.
#
#   BOOTSTRAP_EMAIL=admin@example.com BOOTSTRAP_PASSWORD=... \
#     scripts/bootstrap-platform-admin.sh
#
# Deploy runs it after the apply (make bootstrap-platform-admin), with the
# secrets of the same name. Without BOOTSTRAP_EMAIL it does nothing. Running
# it again is safe: an existing user is kept, its password goes back to
# BOOTSTRAP_PASSWORD (the secrets are what the admin logs in with after any
# Deploy), and the group already having it changes nothing.
#
# A script and not a Terraform resource: aws_cognito_user would keep the
# password in the state, and any plan without the secrets (Validate's, a
# local make plan) would plan to delete the admin.
set -euo pipefail

email="$(printf '%s' "${BOOTSTRAP_EMAIL:-}" | tr '[:upper:]' '[:lower:]')"
password="${BOOTSTRAP_PASSWORD:-}"

if [[ -z "$email" ]]; then
  echo "BOOTSTRAP_EMAIL is empty: no platform admin"
  exit 0
fi
if [[ -z "$password" ]]; then
  echo "BOOTSTRAP_EMAIL is set but BOOTSTRAP_PASSWORD is empty" >&2
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
region="${AWS_REGION:-us-east-1}"
pool="$(terraform -chdir="$root/infra" output -raw cognito_user_pool_id)"
# cognito <operation> [options]: one call about the admin.
cognito() {
  aws cognito-idp "$1" --region "$region" --user-pool-id "$pool" --username "$email" "${@:2}"
}

if found="$(cognito admin-get-user 2>&1)"; then
  echo "$email is already in Cognito"
elif [[ "$found" == *UserNotFoundException* ]]; then
  # Confirmed and with no email: the password is the secret, not a
  # temporary one Cognito would send.
  cognito admin-create-user --message-action SUPPRESS \
    --user-attributes Name=email,Value="$email" Name=email_verified,Value=true >/dev/null
  echo "created $email in Cognito"
else
  echo "$found" >&2
  exit 1
fi

cognito admin-set-user-password --password "$password" --permanent
cognito admin-add-user-to-group --group-name platform-admin
echo "$email is a platform admin"
