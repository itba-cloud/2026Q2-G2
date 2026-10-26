# PostgreSQL on RDS, the source of truth, behind an RDS Proxy. The master
# credentials live only in Secrets Manager: the Proxy reads them from there,
# and so does the code (DB_SECRET_ARN).

locals {
  # Postgres' port, and the only one an RDS Proxy for PostgreSQL listens on.
  port = 5432
}

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "this" {
  name        = var.name
  description = "Master credentials of the LGDC database, read by the RDS Proxy and the Lambdas."

  # No recovery window, so a Destroy followed by a Deploy can reuse the name.
  recovery_window_in_days = 0
}

# The shape the RDS Proxy expects: username and password.
resource "aws_secretsmanager_secret_version" "this" {
  secret_id = aws_secretsmanager_secret.this.id
  secret_string = jsonencode({
    username = var.username
    password = random_password.master.result
  })
}

# pg_cron rolls the partitions of `price` from inside the database; it has to
# be preloaded from the first boot. work_mem is the comparison's: the query
# sorts a dense radius' worth of prices, past the 4 MB default (app/api/README.md).
resource "aws_db_parameter_group" "this" {
  name   = var.name
  family = format("postgres%s", split(".", var.engine_version)[0])

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_cron"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "cron.database_name"
    value        = var.db_name
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "work_mem"
    value = "65536"
  }
}

resource "aws_security_group" "instance" {
  name        = format("%s-instance", var.name)
  description = "RDS instances: Postgres from the Proxy and from what the root module allows."
  vpc_id      = var.vpc_id

  tags = {
    Name = format("%s-instance", var.name)
  }
}

resource "aws_security_group" "proxy" {
  name        = format("%s-proxy", var.name)
  description = "RDS Proxy: Postgres from what the root module allows."
  vpc_id      = var.vpc_id

  tags = {
    Name = format("%s-proxy", var.name)
  }
}

resource "aws_vpc_security_group_egress_rule" "proxy_instance" {
  security_group_id            = aws_security_group.proxy.id
  description                  = "Postgres to the instances"
  referenced_security_group_id = aws_security_group.instance.id
  ip_protocol                  = "tcp"
  from_port                    = local.port
  to_port                      = local.port
}

resource "aws_vpc_security_group_ingress_rule" "instance_proxy" {
  security_group_id            = aws_security_group.instance.id
  description                  = "Postgres from the RDS Proxy"
  referenced_security_group_id = aws_security_group.proxy.id
  ip_protocol                  = "tcp"
  from_port                    = local.port
  to_port                      = local.port
}

# Password authentication: the Proxy and every component that connects read
# the password from Secrets Manager, so IAM authentication would go unused.
#tfsec:ignore:aws-rds-enable-iam-auth
resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.username
  password = random_password.master.result
  port     = local.port

  db_subnet_group_name   = var.db_subnet_group_name
  vpc_security_group_ids = [aws_security_group.instance.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false

  # A read replica needs automated backups on its source. A week of them, which
  # the destroy deletes with the instance.
  backup_retention_period = 7
  apply_immediately       = true

  # Destroy has to leave the lab account clean.
  #tfsec:ignore:aws-rds-enable-deletion-protection
  deletion_protection = false
  skip_final_snapshot = true
}

# The same engine and parameters as the primary; it inherits the subnets, the
# storage and the credentials from it.
resource "aws_db_instance" "replica" {
  count = var.read_replica_enabled ? 1 : 0

  identifier          = format("%s-replica", var.name)
  replicate_source_db = aws_db_instance.this.identifier
  instance_class      = var.instance_class
  storage_encrypted   = true

  # Inherited from the primary anyway; declared so a plan does not drop it,
  # and so the replica's storage grows as the primary's does.
  max_allocated_storage = var.max_allocated_storage

  vpc_security_group_ids = [aws_security_group.instance.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false

  apply_immediately = true

  # Destroy has to leave the lab account clean.
  #tfsec:ignore:aws-rds-enable-deletion-protection
  deletion_protection = false
  skip_final_snapshot = true
}

# The Proxy writes its logs here. Left to RDS, the group is created on the
# Proxy's first log line, with no retention, and outlives the destroy.
resource "aws_cloudwatch_log_group" "proxy" {
  name              = format("/aws/rds/proxy/%s", var.name)
  retention_in_days = var.retention_in_days
}

# The Lambdas open and close connections at will; the Proxy pools them so a
# burst of invocations does not exhaust the instance's max_connections.
resource "aws_db_proxy" "this" {
  name           = var.name
  engine_family  = "POSTGRESQL"
  role_arn       = var.role_arn
  vpc_subnet_ids = var.vpc_subnet_ids
  require_tls    = true

  vpc_security_group_ids = [aws_security_group.proxy.id]

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = aws_secretsmanager_secret.this.arn
  }

  # The Proxy reads the secret as soon as it exists: it needs a value. And
  # its log group goes first, or RDS creates one Terraform does not own.
  depends_on = [aws_secretsmanager_secret_version.this, aws_cloudwatch_log_group.proxy]
}

resource "aws_db_proxy_default_target_group" "this" {
  db_proxy_name = aws_db_proxy.this.name

  connection_pool_config {
    max_connections_percent = 90
  }
}

resource "aws_db_proxy_target" "this" {
  db_proxy_name          = aws_db_proxy.this.name
  target_group_name      = aws_db_proxy_default_target_group.this.name
  db_instance_identifier = aws_db_instance.this.identifier
}
