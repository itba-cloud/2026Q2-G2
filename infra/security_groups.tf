# Rules between security groups of different modules. They live here, not in
# either module, so no module has to know about the other.

# The Lambdas inside the VPC reach the database: the API through the Proxy,
# the migrator straight to the instance (DDL needs no pooling).
resource "aws_vpc_security_group_egress_rule" "app_db_proxy" {
  security_group_id            = module.network.app_security_group_id
  description                  = "Postgres to the RDS Proxy"
  referenced_security_group_id = module.database.proxy_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = module.database.port
  to_port                      = module.database.port
}

resource "aws_vpc_security_group_ingress_rule" "db_proxy_app" {
  security_group_id            = module.database.proxy_security_group_id
  description                  = "Postgres from the Lambdas inside the VPC"
  referenced_security_group_id = module.network.app_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = module.database.port
  to_port                      = module.database.port
}

resource "aws_vpc_security_group_egress_rule" "app_db_instance" {
  security_group_id            = module.network.app_security_group_id
  description                  = "Postgres to the RDS instances"
  referenced_security_group_id = module.database.instance_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = module.database.port
  to_port                      = module.database.port
}

resource "aws_vpc_security_group_ingress_rule" "db_instance_app" {
  security_group_id            = module.database.instance_security_group_id
  description                  = "Postgres from the Lambdas inside the VPC"
  referenced_security_group_id = module.network.app_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = module.database.port
  to_port                      = module.database.port
}

# The Proxy reads the credentials from Secrets Manager, through its interface
# endpoint: there is no other way out of the VPC.
resource "aws_vpc_security_group_egress_rule" "db_proxy_endpoints" {
  security_group_id            = module.database.proxy_security_group_id
  description                  = "HTTPS to the interface endpoints (Secrets Manager)"
  referenced_security_group_id = module.network.endpoints_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# The workers reach AWS through the endpoints: ECR (api and dkr), Logs, SQS
# and Secrets Manager through the interface ones; the image's layers, raw/
# and curated/ through S3's gateway.
resource "aws_vpc_security_group_egress_rule" "batch_endpoints" {
  security_group_id            = module.batch.security_group_id
  description                  = "HTTPS to the interface endpoints"
  referenced_security_group_id = module.network.endpoints_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "batch_gateway" {
  for_each = module.network.gateway_prefix_list_ids

  security_group_id = module.batch.security_group_id
  description       = format("HTTPS to %s through its gateway endpoint", each.key)
  prefix_list_id    = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Straight to the instance: see module "batch" in main.tf.
resource "aws_vpc_security_group_egress_rule" "batch_db_instance" {
  security_group_id            = module.batch.security_group_id
  description                  = "Postgres to the RDS instances"
  referenced_security_group_id = module.database.instance_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = module.database.port
  to_port                      = module.database.port
}

resource "aws_vpc_security_group_ingress_rule" "db_instance_batch" {
  security_group_id            = module.database.instance_security_group_id
  description                  = "Postgres from the Fargate workers"
  referenced_security_group_id = module.batch.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = module.database.port
  to_port                      = module.database.port
}
