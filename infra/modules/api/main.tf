# An HTTP API in front of two Lambdas: the API (FastAPI, which does its own
# routing) on every route, behind Cognito's JWT, and the login's callback on
# its one public route. API Gateway checks the token's signature, issuer,
# audience and expiry before the API runs, and hands it the claims.
locals {
  callback_path = "/auth/callback"
  endpoint      = trimsuffix(aws_apigatewayv2_stage.this.invoke_url, "/")
}

resource "aws_apigatewayv2_api" "this" {
  name          = var.name
  protocol_type = "HTTP"

  # The portal is served from its bucket and the API from here: two
  # origins. API Gateway puts these headers on every response, the
  # authorizer's 401s included, so the portal sees an expired token as a 401
  # and not as a CORS error. The preflights still need a route of their own
  # without the authorizer (public_route_keys): $default would turn them into
  # 401s. Any origin: the token travels in the Authorization header, never
  # in a cookie, so a hostile page gets what an anonymous curl gets.
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "this" {
  api_id           = aws_apigatewayv2_api.this.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = var.jwt_issuer
    audience = var.jwt_audiences
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "$default"
  target             = format("integrations/%s", aws_apigatewayv2_integration.api.id)
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.this.id
}

# The API's routes that answer without a token, to the same integration. A
# route with its own key wins over $default.
resource "aws_apigatewayv2_route" "public" {
  for_each = var.public_route_keys

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.value
  target    = format("integrations/%s", aws_apigatewayv2_integration.api.id)
}

resource "aws_apigatewayv2_integration" "callback" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.callback_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "callback" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = format("GET %s", local.callback_path)
  target    = format("integrations/%s", aws_apigatewayv2_integration.callback.id)
}

# No access logs: an HTTP API writes them through API Gateway's service-linked
# role, which no deploy in the Learner Lab has tried. Each invocation of the
# Lambdas behind it is still logged to CloudWatch.
#tfsec:ignore:aws-api-gateway-enable-access-logging
resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
}

# Resource policies on the functions, not IAM roles: the Learner Lab allows them.
resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = format("%s/*/*", aws_apigatewayv2_api.this.execution_arn)
}

resource "aws_lambda_permission" "callback" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.callback_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = format("%s/*/*", aws_apigatewayv2_api.this.execution_arn)
}

# Until ticket 05 the API's integration, route and permission were the only
# ones, named `this`. Renamed in place, not recreated.
moved {
  from = aws_apigatewayv2_integration.this
  to   = aws_apigatewayv2_integration.api
}

moved {
  from = aws_apigatewayv2_route.this
  to   = aws_apigatewayv2_route.default
}

moved {
  from = aws_lambda_permission.this
  to   = aws_lambda_permission.api
}
