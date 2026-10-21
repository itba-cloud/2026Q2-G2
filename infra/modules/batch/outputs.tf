output "repository_name" {
  description = "The name of the repository."
  value       = aws_ecr_repository.this.name
}

output "repository_url" {
  description = "The URL of the repository (<account>.dkr.ecr.<region>.amazonaws.com/<name>)."
  value       = aws_ecr_repository.this.repository_url
}

output "cluster_name" {
  description = "The name of the ECS cluster."
  value       = aws_ecs_cluster.this.name
}

output "service_name" {
  description = "The name of the ECS service."
  value       = aws_ecs_service.this.name
}

output "security_group_id" {
  description = "ID of the workers' security group."
  value       = aws_security_group.this.id
}
