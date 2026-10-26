# The schema is applied during the apply, with no manual step: Terraform
# invokes the migrator once the database and the function exist, and the
# apply fails if the migration does. It runs again only when the migrator's
# code changes or the instance is replaced; the SQL is idempotent anyway, so
# running it twice changes nothing.
resource "aws_lambda_invocation" "db_migration" {
  function_name = module.db_migrator_function.function_name
  input         = jsonencode({})

  triggers = {
    source_code_hash     = module.db_migrator_function.source_code_hash
    instance_resource_id = module.database.instance_resource_id
  }

  # Nothing in the input says so, but the function can only reach the
  # instance once these rules exist.
  depends_on = [
    aws_vpc_security_group_egress_rule.app_db_instance,
    aws_vpc_security_group_ingress_rule.db_instance_app,
  ]
}
