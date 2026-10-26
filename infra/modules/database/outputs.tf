output "proxy_endpoint" {
  description = "The endpoint that you can use to connect to the proxy."
  value       = aws_db_proxy.this.endpoint
}

output "instance_address" {
  description = "The hostname of the RDS instance, for what connects directly (the migrator)."
  value       = aws_db_instance.this.address
}

output "instance_resource_id" {
  description = "The RDS Resource ID of the instance: changes only when the instance is replaced."
  value       = aws_db_instance.this.resource_id
}

output "replica_address" {
  description = "The hostname of the read replica, or empty with read_replica_enabled = false."
  value       = try(aws_db_instance.replica[0].address, "")
}

output "port" {
  description = "The port on which the instances and the Proxy accept connections."
  value       = local.port
}

output "db_name" {
  description = "The name of the database."
  value       = aws_db_instance.this.db_name
}

output "username" {
  description = "Username for the master DB user."
  value       = aws_db_instance.this.username
}

output "secret_arn" {
  description = "ARN of the secret with the master credentials (JSON with username and password)."
  value       = aws_secretsmanager_secret.this.arn
}

output "instance_security_group_id" {
  description = "ID of the security group of the instances."
  value       = aws_security_group.instance.id
}

output "proxy_security_group_id" {
  description = "ID of the security group of the RDS Proxy."
  value       = aws_security_group.proxy.id
}
