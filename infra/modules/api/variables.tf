variable "name" {
  description = "Name of the API."
  type        = string
  nullable    = false
}

variable "lambda_function_name" {
  description = "Name of the API's Lambda Function, which serves every route but the callback's."
  type        = string
  nullable    = false
}

variable "lambda_invoke_arn" {
  description = "Invoke ARN of the API's Lambda Function."
  type        = string
  nullable    = false
}

variable "public_route_keys" {
  description = "Route keys of the API's routes that answer without a JWT (\"GET /api/health\"). Every other route asks for one."
  type        = set(string)
  default     = []
  nullable    = false
}

variable "jwt_issuer" {
  description = "The base domain of the identity provider that issues JSON Web Tokens: the user pool's issuer URL."
  type        = string
  nullable    = false
}

variable "jwt_audiences" {
  description = "A list of the intended recipients of the JWT. A valid JWT must provide an aud that matches at least one entry in this list: the user pool's client IDs."
  type        = list(string)
  nullable    = false
}

variable "callback_function_name" {
  description = "Name of the Lambda Function that trades the login's code for the tokens."
  type        = string
  nullable    = false
}

variable "callback_invoke_arn" {
  description = "Invoke ARN of the callback's Lambda Function."
  type        = string
  nullable    = false
}
