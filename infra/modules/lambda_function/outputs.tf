output "function_name" {
  description = "Name of the Lambda Function."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "Amazon Resource Name (ARN) identifying the Lambda Function."
  value       = aws_lambda_function.this.arn
}

output "invoke_arn" {
  description = "ARN to be used for invoking the Lambda Function from API Gateway."
  value       = aws_lambda_function.this.invoke_arn
}

output "source_code_hash" {
  description = "Base64-encoded SHA256 of the deployment package: changes only when the code does."
  value       = aws_lambda_function.this.source_code_hash
}
