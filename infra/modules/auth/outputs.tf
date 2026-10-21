output "user_pool_id" {
  description = "ID of the user pool."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_issuer" {
  description = "Issuer of the pool's tokens (the iss claim), which API Gateway's JWT authorizer checks."
  value       = format("https://%s", aws_cognito_user_pool.this.endpoint)
}

output "user_pool_client_id" {
  description = "ID of the portal's client: the aud claim of its ID tokens."
  value       = aws_cognito_user_pool_client.this.id
}

output "user_pool_client_secret" {
  description = "Secret of the portal's client. Only the callback Lambda gets it."
  value       = aws_cognito_user_pool_client.this.client_secret
  sensitive   = true
}

# A pool's ID starts with its region: us-east-1_AbCdEf.
output "hosted_ui_url" {
  description = "Base URL of the Hosted UI and of the OAuth endpoints (/oauth2/authorize, /oauth2/token, /logout)."
  value       = format("https://%s.auth.%s.amazoncognito.com", aws_cognito_user_pool_domain.this.domain, split("_", aws_cognito_user_pool.this.id)[0])
}
