module "network" {
  source = "./modules/network"

  name                        = local.name_prefix
  region                      = data.aws_region.current.region
  cidr                        = var.vpc_cidr
  azs                         = local.azs
  app_subnets                 = local.subnet_cidrs.app
  batch_subnets               = local.subnet_cidrs.batch
  data_subnets                = local.subnet_cidrs.data
  gateway_endpoint_services   = local.gateway_endpoint_services
  interface_endpoint_services = local.interface_endpoint_services
}

module "database" {
  source = "./modules/database"

  name                 = format("%s-db", local.name_prefix)
  vpc_id               = module.network.vpc_id
  vpc_subnet_ids       = module.network.data_subnet_ids
  db_subnet_group_name = module.network.database_subnet_group_name
  role_arn             = data.aws_iam_role.lab.arn
  instance_class       = var.db_instance_class
  db_name              = local.name_prefix
  username             = local.name_prefix
  read_replica_enabled = var.read_replica_enabled
  retention_in_days    = var.log_retention_days
}

# Applies the schema. migration.tf invokes it during the apply.
module "db_migrator_function" {
  source = "./modules/lambda_function"

  function_name     = local.db_migrator_name
  description       = "LGDC schema migrator: applies the whole schema, idempotently."
  filename          = "${path.root}/../build/db_migrator.zip"
  handler           = "main.handler"
  role              = data.aws_iam_role.lab.arn
  timeout           = 300
  retention_in_days = var.log_retention_days

  environment_variables = merge(local.db_environment, {
    DB_HOST = module.database.instance_address
  })

  vpc_config = {
    subnet_ids         = module.network.app_subnet_ids
    security_group_ids = [module.network.app_security_group_id]
  }
}

module "api_function" {
  source = "./modules/lambda_function"

  function_name     = local.api_name
  description       = "LGDC API: the FastAPI app behind API Gateway."
  filename          = "${path.root}/../build/api.zip"
  handler           = "main.handler"
  role              = data.aws_iam_role.lab.arn
  memory_size       = 512
  timeout           = 30
  retention_in_days = var.log_retention_days

  environment_variables = merge(local.db_environment, {
    DB_HOST              = module.database.proxy_endpoint
    INVITE_FUNCTION_NAME = module.invite_function.function_name
    # POST /admin/neighbors/refresh runs there, asynchronously.
    DAY_CLOSE_FUNCTION_NAME = module.day_close_function.function_name
    # Where the portal's uploads go: the API signs their POST, and reads
    # their rejected rows back.
    S3_DATA_BUCKET = module.data_lake.bucket_name
    # The tablero and Mis Precios read the precompute, and queue it for a
    # branch that has none yet.
    COMPARISON_TABLE     = module.serving.table_name
    PRECOMPUTE_QUEUE_URL = module.precompute_queue.queue_url
  })

  vpc_config = {
    subnet_ids         = module.network.app_subnet_ids
    security_group_ids = [module.network.app_security_group_id]
  }
}

# Every route but these and the callback asks for a Cognito JWT.
module "api" {
  source = "./modules/api"

  name                   = local.api_name
  lambda_function_name   = module.api_function.function_name
  lambda_invoke_arn      = module.api_function.invoke_arn
  public_route_keys      = local.api_public_route_keys
  jwt_issuer             = module.auth.user_pool_issuer
  jwt_audiences          = [module.auth.user_pool_client_id]
  callback_function_name = module.auth_callback_function.function_name
  callback_invoke_arn    = module.auth_callback_function.invoke_arn
}

module "auth" {
  source = "./modules/auth"

  name          = local.name_prefix
  domain        = local.auth_domain
  callback_urls = [module.api.callback_url]
  logout_urls   = [local.portal_url]
  portal_url    = local.portal_url
}

# The invitation's Cognito half: creates the invited member's user, and
# Cognito emails it a temporary password. Outside the VPC, like the
# callback: a user pool with a domain takes no request through a VPC
# endpoint (docs/adr/0003). The API invokes it through Lambda's endpoint.
module "invite_function" {
  source = "./modules/lambda_function"

  function_name     = local.invite_name
  description       = "LGDC invitations: creates the invited member's Cognito user."
  filename          = "${path.root}/../build/invite.zip"
  handler           = "main.handler"
  role              = data.aws_iam_role.lab.arn
  timeout           = 10
  retention_in_days = var.log_retention_days

  environment_variables = {
    USER_POOL_ID = module.auth.user_pool_id
  }
}

# Trades the Hosted UI's code for the tokens: the only holder of the
# client's secret. Outside the VPC, since Cognito's token endpoint is on the
# internet.
module "auth_callback_function" {
  source = "./modules/lambda_function"

  function_name     = local.auth_callback_name
  description       = "LGDC login callback: trades Cognito's code for the tokens and hands them to the portal."
  filename          = "${path.root}/../build/auth_callback.zip"
  handler           = "main.handler"
  role              = data.aws_iam_role.lab.arn
  timeout           = 10
  retention_in_days = var.log_retention_days

  environment_variables = {
    COGNITO_URL   = module.auth.hosted_ui_url
    CLIENT_ID     = module.auth.user_pool_client_id
    CLIENT_SECRET = module.auth.user_pool_client_secret
    REDIRECT_URI  = module.api.callback_url
    PORTAL_URL    = local.portal_url
  }
}

module "frontend" {
  source = "./modules/frontend"

  bucket = local.portal_bucket_name

  config = {
    apiUrl      = module.api.api_endpoint
    authUrl     = module.auth.hosted_ui_url
    clientId    = module.auth.user_pool_client_id
    redirectUri = module.api.callback_url
    logoutUri   = local.portal_url
  }
}

module "data_lake" {
  source = "./modules/data_lake"

  bucket               = local.data_bucket_name
  lifecycle_rules      = local.data_lifecycle_rules
  cors_allowed_origins = [local.portal_origin]
}

# Downloads the SEPA's daily archive through the group's proxy and stores one
# file per chain in raw/, which makes the jobs. Outside the VPC: the proxy is
# on the internet, and the VPC has no way out. A daily cron and run-ingest
# (make ingest) invoke it, both asynchronously. 330 MB through the tunnel
# take minutes, and land on /tmp before going to S3 chain by chain.
module "ingest_function" {
  source = "./modules/lambda_function"

  function_name          = local.ingest_name
  description            = "LGDC SEPA ingestion: stores the day's file of each chain in raw/."
  filename               = "${path.root}/../build/ingest.zip"
  handler                = "main.handler"
  role                   = data.aws_iam_role.lab.arn
  memory_size            = 1024
  timeout                = 900
  ephemeral_storage_size = 2048
  retention_in_days      = var.log_retention_days
  schedule_expression    = var.ingest_schedule_expression

  # A failed run is not retried: retrying would not bring a tunnel back up,
  # and run-ingest reports the first failure. The next cron, or run-ingest,
  # tries again; a run stores only what is not there yet.
  maximum_retry_attempts = 0

  # An empty SEPA_API_URL fails only the ingestion, with a message that
  # names the proxy: the apply does not depend on the tunnel.
  environment_variables = {
    S3_DATA_BUCKET = module.data_lake.bucket_name
    SEPA_API_URL   = var.sepa_api_url
  }
}

# One message per new object in raw/: the jobs. notifications.tf wires the
# bucket to it.
module "jobs_queue" {
  source = "./modules/queue"

  name = format("%s-jobs", local.name_prefix)

  # Longer than the biggest chain takes, so no second worker takes a job the
  # first is still loading. A worker that dies gives its job back after it.
  visibility_timeout_seconds = 900

  allowed_senders = {
    "s3.amazonaws.com" = module.data_lake.bucket_arn
  }
}

module "batch" {
  source = "./modules/batch"

  name         = local.worker_name
  region       = data.aws_region.current.region
  vpc_id       = module.network.vpc_id
  subnet_ids   = module.network.batch_subnet_ids
  role_arn     = data.aws_iam_role.lab.arn
  image_tag    = var.worker_image_tag
  queue_name   = module.jobs_queue.queue_name
  max_capacity = var.worker_max_capacity

  retention_in_days = var.log_retention_days

  # Straight to the instance, like the migrator: a worker holds one
  # connection for its whole life and loads through temporary tables and
  # COPY, which would pin a Proxy connection anyway.
  environment_variables = merge(local.db_environment, {
    DB_HOST           = module.database.instance_address
    AWS_REGION        = data.aws_region.current.region
    S3_DATA_BUCKET    = module.data_lake.bucket_name
    JOBS_QUEUE_URL    = module.jobs_queue.queue_url
    MAX_RECEIVE_COUNT = tostring(module.jobs_queue.max_receive_count)
    # Where it announces each chain it processed, through its outbox.
    CHAIN_PROCESSED_TOPIC_ARN = module.chain_processed_topic.topic_arn
  })
}

# Every chain a worker processes, done or failed, is announced here once:
# the fan-out to the alerts, the day close and the admin's email
# (local.chain_processed_subscriptions says who gets what).
module "chain_processed_topic" {
  source = "./modules/topic"

  name          = format("%s-cadena-procesada", local.name_prefix)
  subscriptions = local.chain_processed_subscriptions
}

# The chains that loaded with price changes, for the alert-matcher. Six
# times the function's timeout, as AWS advises for a Lambda's queue, so a
# batch being matched is not handed out again.
module "alerts_queue" {
  source = "./modules/queue"

  name                       = format("%s-alerts", local.name_prefix)
  visibility_timeout_seconds = 6 * local.alert_matcher_timeout

  allowed_senders = {
    "sns.amazonaws.com" = module.chain_processed_topic.topic_arn
  }
}

# Every processed chain, for the day close. Six times the function's
# timeout, like the alerts queue.
module "day_close_queue" {
  source = "./modules/queue"

  name                       = local.day_close_name
  visibility_timeout_seconds = 6 * local.day_close_timeout

  allowed_senders = {
    "sns.amazonaws.com" = module.chain_processed_topic.topic_arn
  }
}

# Matches a chain's new price changes against every org's rules and stores
# the alerts the portal shows. Inside the VPC, through the Proxy like the
# API: a short query per notice, which a pooled connection suits. At most two
# at a time, to spare the Proxy's connections.
module "alert_matcher_function" {
  source = "./modules/lambda_function"

  function_name     = local.alert_matcher_name
  description       = "LGDC alert-matcher: turns a chain's price changes into the alerts of the rules that watch them."
  filename          = "${path.root}/../build/alert_matcher.zip"
  handler           = "main.handler"
  role              = data.aws_iam_role.lab.arn
  timeout           = local.alert_matcher_timeout
  retention_in_days = var.log_retention_days

  sqs_trigger = {
    queue_arn           = module.alerts_queue.queue_arn
    maximum_concurrency = 2
  }

  environment_variables = merge(local.db_environment, {
    DB_HOST = module.database.proxy_endpoint
  })

  vpc_config = {
    subnet_ids         = module.network.app_subnet_ids
    security_group_ids = [module.network.app_security_group_id]
  }
}

# One entry per followed branch and radius whose comparison has to be
# computed again: what the day close queues when a day closes, or when a
# merchant's upload loads, and the API when it answers a branch with no
# precompute yet (or deletes an upload). Six times the function's timeout,
# like the other queues a Lambda consumes.
module "precompute_queue" {
  source = "./modules/queue"

  name                       = local.precompute_name
  visibility_timeout_seconds = 6 * local.precompute_timeout
}

# The comparison of each followed branch and radius, precomputed: what the
# tablero and Mis Precios read.
module "serving" {
  source = "./modules/serving"

  name = format("%s-comparison", local.name_prefix)
}

# Computes one followed branch and radius per invocation and writes it to the
# serving table: the rows, the summary, and last META, the day that is
# complete. At most two at a time, through the event source mapping's
# maximum_concurrency (the Learner Lab does not let a function reserve
# concurrency), so it reads straight from the replica, or the primary without
# one, and not through the Proxy. Inside the VPC: DynamoDB through its
# gateway endpoint, the secret through its interface endpoint.
module "precompute_function" {
  source = "./modules/lambda_function"

  function_name     = local.precompute_name
  description       = "LGDC precompute: stores a followed branch's comparison in DynamoDB, a whole day at a time."
  filename          = "${path.root}/../build/precompute.zip"
  handler           = "main.handler"
  role              = data.aws_iam_role.lab.arn
  memory_size       = 512
  timeout           = local.precompute_timeout
  retention_in_days = var.log_retention_days

  # One entry per invocation: a dense branch takes seconds to query and to
  # write, and each gets the whole timeout.
  sqs_trigger = {
    queue_arn           = module.precompute_queue.queue_arn
    batch_size          = 1
    maximum_concurrency = 2
  }

  environment_variables = merge(local.db_environment, {
    DB_HOST          = module.database.instance_address
    DB_HOST_RO       = module.database.replica_address
    COMPARISON_TABLE = module.serving.table_name
  })

  vpc_config = {
    subnet_ids         = module.network.app_subnet_ids
    security_group_ids = [module.network.app_security_group_id]
  }
}

# Closes each day once its last chain is in: the catalogue maintenance and
# the precompute entries, once. Fed by the day-close queue, and invoked by a
# cron at 10:00 in Buenos Aires as a backup, which closes the day with
# whatever chains are in. Inside the VPC, through the Proxy like the
# alert-matcher; it lists raw/ through S3's gateway endpoint and queues
# through SQS's interface endpoint.
module "day_close_function" {
  source = "./modules/lambda_function"

  function_name       = local.day_close_name
  description         = "LGDC day close: once a day's last chain is in, rebuilds the neighbors and the catalogue and queues the precompute."
  filename            = "${path.root}/../build/day_close.zip"
  handler             = "main.handler"
  role                = data.aws_iam_role.lab.arn
  timeout             = local.day_close_timeout
  retention_in_days   = var.log_retention_days
  schedule_expression = var.day_close_schedule_expression

  sqs_trigger = {
    queue_arn           = module.day_close_queue.queue_arn
    maximum_concurrency = 2
  }

  environment_variables = merge(local.db_environment, {
    DB_HOST              = module.database.proxy_endpoint
    S3_DATA_BUCKET       = module.data_lake.bucket_name
    PRECOMPUTE_QUEUE_URL = module.precompute_queue.queue_url
  })

  vpc_config = {
    subnet_ids         = module.network.app_subnet_ids
    security_group_ids = [module.network.app_security_group_id]
  }
}
