variable "name" {
  description = "Name of the user pool and of its client."
  type        = string
  nullable    = false
}

variable "domain" {
  description = "For custom domains, this is the fully-qualified domain name. For Amazon Cognito prefix domains, this is the prefix alone. Prefixes are global: it must be unique across AWS."
  type        = string
  nullable    = false
}

variable "callback_urls" {
  description = "List of allowed callback URLs for the identity providers: where Cognito sends the code."
  type        = list(string)
  nullable    = false
}

variable "portal_url" {
  description = "URL of the portal, which the invitation email links to."
  type        = string
  nullable    = false
}

variable "logout_urls" {
  description = "List of allowed logout URLs for the identity providers: where Cognito sends the browser after a logout."
  type        = list(string)
  nullable    = false
}
