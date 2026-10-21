output "table_name" {
  description = "The name of the table, which the precompute writes and the API reads."
  value       = aws_dynamodb_table.this.name
}
