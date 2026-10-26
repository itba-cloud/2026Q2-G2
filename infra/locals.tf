locals {
  name_prefix = "lgdc"

  tags = merge(
    {
      Project   = local.name_prefix
      ManagedBy = "terraform"
      Course    = "82.08"
    },
    var.tags,
  )

  azs = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)

  # Three private bands, one /20 per AZ. Each band starts four slots after the
  # previous one, so adding an AZ never renumbers an existing subnet.
  subnet_cidrs = {
    app   = [for index, az in local.azs : cidrsubnet(var.vpc_cidr, 4, index)]
    batch = [for index, az in local.azs : cidrsubnet(var.vpc_cidr, 4, index + 4)]
    data  = [for index, az in local.azs : cidrsubnet(var.vpc_cidr, 4, index + 8)]
  }

  alert_matcher_name = format("%s-alert-matcher", local.name_prefix)
  api_name           = format("%s-api", local.name_prefix)
  auth_callback_name = format("%s-auth-callback", local.name_prefix)
  day_close_name     = format("%s-day-close", local.name_prefix)
  db_migrator_name   = format("%s-db-migrator", local.name_prefix)
  ingest_name        = format("%s-ingest", local.name_prefix)
  invite_name        = format("%s-invite", local.name_prefix)
  precompute_name    = format("%s-precompute", local.name_prefix)
  worker_name        = format("%s-worker", local.name_prefix)

  # Seconds the alert-matcher has per batch; its queue's visibility timeout
  # is derived from it.
  alert_matcher_timeout = 60

  # Seconds the day close has per batch: the winner runs the catalogue
  # maintenance, a full rebuild of the neighbors and the catalogue.
  day_close_timeout = 300

  # Seconds the precompute has per entry: the comparison of a dense branch
  # (seconds against Postgres) and its thousands of rows to DynamoDB.
  precompute_timeout = 300

  # Buckets and the Hosted UI's domain carry the account ID: their names are
  # global. scripts/buckets.sh builds the buckets' names the same way.
  data_bucket_name   = format("%s-data-%s", local.name_prefix, data.aws_caller_identity.current.account_id)
  portal_bucket_name = format("%s-portal-%s", local.name_prefix, data.aws_caller_identity.current.account_id)
  auth_domain        = format("%s-%s", local.name_prefix, data.aws_caller_identity.current.account_id)

  # The portal over HTTPS: its index.html on S3's REST endpoint (the website
  # endpoint only speaks HTTP). Built from the name alone, since Cognito's
  # logout URL and the portal's own config.json need it, and the data
  # bucket's CORS its origin.
  portal_origin = format("https://%s.s3.%s.amazonaws.com", local.portal_bucket_name, data.aws_region.current.region)
  portal_url    = format("%s/index.html", local.portal_origin)

  # The API's routes that answer without a token: the probes, and the CORS
  # preflights, which a browser sends without one (the API answers them 204
  # and API Gateway adds the headers). Everything else, the portal's reads
  # included, asks for a Cognito JWT.
  api_public_route_keys = toset([
    "GET /api/health",
    "GET /api/ready",
    "OPTIONS /{proxy+}",
  ])

  # What each prefix of the data bucket keeps. raw/ is what the workers load
  # and can reload, for 90 days; curated/
  # is the history, so it is never deleted, only moved to a cheaper class;
  # rejected/ is evidence for a few weeks. Overwritten versions (a reprocess)
  # go after a week everywhere.
  data_lifecycle_rules = {
    "raw/" = {
      expiration_days                    = 90
      noncurrent_version_expiration_days = 7
    }
    "curated/" = {
      transition_days                    = 30
      noncurrent_version_expiration_days = 7
    }
    "rejected/" = {
      expiration_days                    = 30
      noncurrent_version_expiration_days = 7
    }
  }

  # How every component inside the VPC reaches Postgres, minus the host: the
  # API goes through the Proxy, the migrator straight to the instance. The
  # password is not here: the code reads it from the secret.
  db_environment = {
    DB_PORT       = tostring(module.database.port)
    DB_NAME       = module.database.db_name
    DB_USER       = module.database.username
    DB_SECRET_ARN = module.database.secret_arn
  }

  # Nothing inside the VPC reaches the internet: it talks to AWS through
  # endpoints. S3 and DynamoDB have gateway endpoints, which are free; the
  # rest need an interface endpoint. Lambda's is for the API, which invokes
  # the invite Lambda: Cognito itself takes no request through an endpoint
  # while the user pool has a domain (docs/adr/0003).
  gateway_endpoint_services = toset(["dynamodb", "s3"])

  interface_endpoint_services = toset([
    "ecr.api",
    "ecr.dkr",
    "lambda",
    "logs",
    "secretsmanager",
    "sns",
    "sqs",
  ])

  # Who hears about each processed chain, and which notices each one gets,
  # filtered by SNS on the attributes the worker publishes them with. The
  # alerts only care about a chain that loaded with price changes; the day
  # close, about every chain, done or failed; the admin, about failures. The
  # email only with an address to send to: whether there is one is the only
  # thing about it the plan may show (nonsensitive), not the address.
  chain_processed_subscriptions = merge(
    {
      alerts = {
        protocol = "sqs"
        endpoint = module.alerts_queue.queue_arn
        filter_policy = jsonencode({
          status        = ["done"]
          price_changes = [{ numeric = [">", 0] }]
        })
      }
      day_close = {
        protocol = "sqs"
        endpoint = module.day_close_queue.queue_arn
      }
    },
    {
      for name, subscription in {
        admin_email = {
          protocol      = "email"
          endpoint      = var.admin_email
          filter_policy = jsonencode({ status = ["failed"] })
        }
      } : name => subscription if nonsensitive(var.admin_email != "")
    },
  )
}
