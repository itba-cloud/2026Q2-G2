# A VPC with no way out to the internet: no Internet Gateway, no NAT. What
# runs inside talks to AWS through endpoints only.

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.7"

  name = var.name
  cidr = var.cidr
  azs  = var.azs

  private_subnets       = var.app_subnets
  private_subnet_names  = [for az in var.azs : format("%s-app-%s", var.name, az)]
  intra_subnets         = var.batch_subnets
  intra_subnet_names    = [for az in var.azs : format("%s-batch-%s", var.name, az)]
  database_subnets      = var.data_subnets
  database_subnet_names = [for az in var.azs : format("%s-data-%s", var.name, az)]

  create_igw         = false
  enable_nat_gateway = false

  # Private DNS for the interface endpoints needs both.
  enable_dns_hostnames = true
  enable_dns_support   = true
}

locals {
  route_table_ids = distinct(concat(
    module.vpc.private_route_table_ids,
    module.vpc.intra_route_table_ids,
    module.vpc.database_route_table_ids,
  ))
}

resource "aws_vpc_endpoint" "gateway" {
  for_each = var.gateway_endpoint_services

  vpc_id            = module.vpc.vpc_id
  service_name      = format("com.amazonaws.%s.%s", var.region, each.key)
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.route_table_ids

  tags = {
    Name = format("%s-%s", var.name, each.key)
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = var.interface_endpoint_services

  vpc_id              = module.vpc.vpc_id
  service_name        = format("com.amazonaws.%s.%s", var.region, each.key)
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = {
    Name = format("%s-%s", var.name, replace(each.key, ".", "-"))
  }
}

resource "aws_security_group" "endpoints" {
  name        = format("%s-endpoints", var.name)
  description = "Interface endpoints: HTTPS from inside the VPC."
  vpc_id      = module.vpc.vpc_id

  tags = {
    Name = format("%s-endpoints", var.name)
  }
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_https" {
  security_group_id = aws_security_group.endpoints.id
  description       = "HTTPS from anything in the VPC"
  cidr_ipv4         = module.vpc.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# The Lambdas that run inside the VPC. The only way out is AWS itself; rules
# toward resources of other modules (the RDS Proxy) live in the root module.
resource "aws_security_group" "app" {
  name        = format("%s-app", var.name)
  description = "Lambdas inside the VPC."
  vpc_id      = module.vpc.vpc_id

  tags = {
    Name = format("%s-app", var.name)
  }
}

resource "aws_vpc_security_group_egress_rule" "app_endpoints" {
  security_group_id            = aws_security_group.app.id
  description                  = "HTTPS to the interface endpoints"
  referenced_security_group_id = aws_security_group.endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "app_gateway" {
  for_each = aws_vpc_endpoint.gateway

  security_group_id = aws_security_group.app.id
  description       = format("HTTPS to %s through its gateway endpoint", each.key)
  prefix_list_id    = each.value.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
