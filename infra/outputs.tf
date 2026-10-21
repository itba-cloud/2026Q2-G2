# The contract with the Deploy workflow: it reads api_endpoint, portal_url,
# portal_bucket_name, data_bucket_name and cognito_user_pool_id (for the
# platform admin) after the apply, and run-ingest ingest_function_name. The
# cognito_*, auth_callback_url, db_*, jobs_*, worker_*, chain_processed_*,
# alerts_queue_arn, day_close_queue_arn, alert_matcher_*, day_close_function_name,
# precompute_* and comparison_table_name outputs are for make verify
# (tools/verify); the rest, for whoever looks at the stack.

output "api_endpoint" {
  description = "Base URL of the API. Deploy curls <api_endpoint>/api/health and writes it to the job summary."
  value       = module.api.api_endpoint
}

output "portal_url" {
  description = "HTTPS URL of the portal. Deploy writes it to the job summary."
  value       = local.portal_url
}

output "portal_bucket_name" {
  description = "Name of the portal's bucket. Deploy syncs app/frontend/dist into it, all but config.json, which Terraform writes."
  value       = module.frontend.bucket_name
}

output "auth_callback_url" {
  description = "URL of the login's callback, where Cognito sends the code."
  value       = module.api.callback_url
}

output "cognito_hosted_ui_url" {
  description = "Base URL of Cognito's Hosted UI."
  value       = module.auth.hosted_ui_url
}

output "cognito_user_pool_id" {
  description = "ID of the user pool. Deploy puts the platform admin in it (make bootstrap-platform-admin)."
  value       = module.auth.user_pool_id
}

output "cognito_client_id" {
  description = "ID of the portal's Cognito client."
  value       = module.auth.user_pool_client_id
}

output "cognito_client_secret" {
  description = "Secret of the portal's Cognito client, for the checks that log in without a browser."
  value       = module.auth.user_pool_client_secret
  sensitive   = true
}

output "api_function_name" {
  description = "Name of the API's Lambda function."
  value       = module.api_function.function_name
}

output "ingest_function_name" {
  description = "Name of the ingestion's Lambda function, which make ingest (the run-ingest workflow) invokes."
  value       = module.ingest_function.function_name
}

output "vpc_id" {
  description = "ID of the VPC."
  value       = module.network.vpc_id
}

output "db_proxy_endpoint" {
  description = "Endpoint of the RDS Proxy, the host the API connects to."
  value       = module.database.proxy_endpoint
}

output "db_replica_address" {
  description = "Hostname of the read replica, or empty with read_replica_enabled = false (the precompute then reads from the primary)."
  value       = module.database.replica_address
}

output "db_secret_arn" {
  description = "ARN of the secret with the database's master credentials."
  value       = module.database.secret_arn
}

output "db_migrator_function_name" {
  description = "Name of the schema migrator's Lambda function."
  value       = module.db_migrator_function.function_name
}

output "db_schema_tables" {
  description = "Tables the database has after the migration, as the migrator reported them."
  value       = jsondecode(aws_lambda_invocation.db_migration.result).tables
}

output "data_bucket_name" {
  description = "Name of the data bucket (raw/, curated/, rejected/). Deploy writes it to the job summary."
  value       = module.data_lake.bucket_name
}

output "jobs_queue_url" {
  description = "URL of the jobs queue, which S3 feeds with every new object in raw/."
  value       = module.jobs_queue.queue_url
}

output "jobs_dlq_url" {
  description = "URL of the jobs queue's DLQ: the jobs that failed max_receive_count times."
  value       = module.jobs_queue.dlq_url
}

output "worker_repository_url" {
  description = "URL of the workers' ECR repository, where scripts/push-worker-image.sh pushes the image."
  value       = module.batch.repository_url
}

output "worker_cluster_name" {
  description = "Name of the ECS cluster of the workers."
  value       = module.batch.cluster_name
}

output "worker_service_name" {
  description = "Name of the ECS service of the workers."
  value       = module.batch.service_name
}

output "chain_processed_topic_arn" {
  description = "ARN of the cadena-procesada topic, where the workers announce each chain they processed."
  value       = module.chain_processed_topic.topic_arn
}

output "chain_processed_subscription_arns" {
  description = "ARN of each of the topic's subscriptions, by name: alerts, day_close and, with admin_email, admin_email."
  value       = module.chain_processed_topic.subscription_arns
}

output "alerts_queue_arn" {
  description = "ARN of the alerts queue, which gets the notices of the chains that loaded with price changes."
  value       = module.alerts_queue.queue_arn
}

output "alerts_dlq_url" {
  description = "URL of the alerts queue's DLQ: the notices the alert-matcher failed on max_receive_count times."
  value       = module.alerts_queue.dlq_url
}

output "day_close_queue_arn" {
  description = "ARN of the day-close queue, which gets every notice."
  value       = module.day_close_queue.queue_arn
}

output "day_close_queue_url" {
  description = "URL of the day-close queue."
  value       = module.day_close_queue.queue_url
}

output "alert_matcher_function_name" {
  description = "Name of the alert-matcher's Lambda function."
  value       = module.alert_matcher_function.function_name
}

output "day_close_dlq_url" {
  description = "URL of the day-close queue's DLQ: the notices the day close failed on max_receive_count times."
  value       = module.day_close_queue.dlq_url
}

output "day_close_function_name" {
  description = "Name of the day close's Lambda function, which its backup cron invokes."
  value       = module.day_close_function.function_name
}

output "precompute_queue_url" {
  description = "URL of the precompute queue: one entry per followed branch and radius to compute again."
  value       = module.precompute_queue.queue_url
}

output "precompute_queue_arn" {
  description = "ARN of the precompute queue, which the precompute function consumes."
  value       = module.precompute_queue.queue_arn
}

output "precompute_function_name" {
  description = "Name of the precompute's Lambda function."
  value       = module.precompute_function.function_name
}

output "comparison_table_name" {
  description = "Name of the DynamoDB table with the precomputed comparisons, which the tablero and Mis Precios read."
  value       = module.serving.table_name
}
