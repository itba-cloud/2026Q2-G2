output "api_endpoint" {
  description = "URL of the API's default stage, with no trailing slash."
  value       = local.endpoint
}

output "callback_url" {
  description = "URL of the login's callback: the redirect URI the portal asks Cognito for."
  value       = format("%s%s", local.endpoint, local.callback_path)
}
