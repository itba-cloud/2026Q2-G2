output "vpc_id" {
  description = "ID of the VPC."
  value       = module.vpc.vpc_id
}

output "app_subnet_ids" {
  description = "IDs of the app band's subnets."
  value       = module.vpc.private_subnets
}

output "batch_subnet_ids" {
  description = "IDs of the batch band's subnets."
  value       = module.vpc.intra_subnets
}

output "data_subnet_ids" {
  description = "IDs of the data band's subnets."
  value       = module.vpc.database_subnets
}

output "database_subnet_group_name" {
  description = "Name of the DB subnet group over the data band."
  value       = module.vpc.database_subnet_group_name
}

output "app_security_group_id" {
  description = "ID of the security group of the Lambdas inside the VPC."
  value       = aws_security_group.app.id
}

output "endpoints_security_group_id" {
  description = "ID of the security group of the interface endpoints."
  value       = aws_security_group.endpoints.id
}

output "gateway_prefix_list_ids" {
  description = "Prefix list of each gateway endpoint, by service (s3, dynamodb): what a security group allows to reach the service through it."
  value       = { for service, endpoint in aws_vpc_endpoint.gateway : service => endpoint.prefix_list_id }
}
