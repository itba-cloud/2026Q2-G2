# Who the portal's users are. Anyone can sign up on the Hosted UI with an
# email and a password; Cognito confirms the email with a code before the
# first login. The org and its roles (owner, member) live in Postgres; the
# platform's own role is this pool's platform-admin group, which travels in
# the token.
resource "aws_cognito_user_pool" "this" {
  name = var.name

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Open registration: no admin in between. An owner's invitation creates
  # the member's user (app/invite), and Cognito sends it this email with a
  # temporary password; the Hosted UI asks for a new one on the first login.
  admin_create_user_config {
    allow_admin_create_user_only = false

    invite_message_template {
      email_subject = "Te invitaron a LGDC"
      email_message = format("Te invitaron a una organización de LGDC. Entrá en %s con tu email, {username}, y la contraseña temporal {####}. La primera vez te va a pedir una nueva. La temporal vence en 7 días.", var.portal_url)
      sms_message   = "LGDC: tu usuario es {username} y tu contraseña temporal, {####}."
    }
  }

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Destroy has to be able to delete it.
  deletion_protection = "INACTIVE"
}

# The Hosted UI, at https://<domain>.auth.<region>.amazoncognito.com. The
# classic one (version 1): the managed login (version 2) needs a branding
# resource and serves the same forms.
resource "aws_cognito_user_pool_domain" "this" {
  domain                = var.domain
  user_pool_id          = aws_cognito_user_pool.this.id
  managed_login_version = 1
}

# The portal's client. Confidential: the code is traded for the tokens by the
# callback Lambda, the only place that knows the secret, so the browser never
# sees it. ADMIN_USER_PASSWORD_AUTH lets the checks (and Deploy's platform
# admin) get a token without a browser; it needs AWS credentials, so a
# browser cannot use it.
resource "aws_cognito_user_pool_client" "this" {
  name            = var.name
  user_pool_id    = aws_cognito_user_pool.this.id
  generate_secret = true

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  # A working day: the portal keeps no refresh token, so the user logs in
  # again after it.
  id_token_validity     = 8
  access_token_validity = 8
  token_validity_units {
    id_token     = "hours"
    access_token = "hours"
  }

  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_group" "platform_admin" {
  name         = "platform-admin"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "LGDC's platform admins: /admin/* and the platform's alarms."
}
