# Every variable has a default: the course staff deploys without a tfvars.

variable "region" {
  description = "AWS region where every resource is created."
  type        = string
  default     = "us-east-1"
  nullable    = false
}

variable "tags" {
  description = "Extra tags merged into the default tags of every resource."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "vpc_cidr" {
  description = "The IPv4 CIDR block for the VPC. Each subnet takes a sixteenth of it (a /20 of the default /16)."
  type        = string
  default     = "10.0.0.0/16"
  nullable    = false

  validation {
    condition     = can(cidrsubnet(var.vpc_cidr, 8, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block of /24 or larger."
  }
}

variable "availability_zone_count" {
  description = "How many availability zones the subnets span."
  type        = number
  default     = 2
  nullable    = false

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 4
    error_message = "availability_zone_count must be between 2 and 4: each band reserves four /20 slots."
  }
}

variable "log_retention_days" {
  description = "Specifies the number of days you want to retain log events in each Lambda's log group."
  type        = number
  default     = 7
  nullable    = false
}

variable "read_replica_enabled" {
  description = "Whether to create a read replica of the database. Off saves its cost and the precompute reads from the primary."
  type        = bool
  default     = true
  nullable    = false
}

variable "db_instance_class" {
  description = "The instance type of the RDS instance and of its read replica. The Learner Lab denies the larger classes."
  type        = string
  default     = "db.t3.micro"
  nullable    = false
}

variable "worker_image_tag" {
  description = "Tag of the workers' image in ECR. The Makefile always passes a hash of the image's sources, the tag `make worker-push` pushes; the default is a placeholder that no push ever creates."
  type        = string
  default     = "latest"
  nullable    = false
}

variable "worker_max_capacity" {
  description = "The most Fargate workers that run at once."
  type        = number
  default     = 4
  nullable    = false
}

variable "sepa_api_url" {
  description = "Base URL of the SEPA proxy's tunnel (tools/sepa-proxy), without /v1/sepa/precios: where the ingestion downloads the daily archive. Deploy passes the optional SEPA_API_URL secret; empty, the ingestion fails with a message that names the proxy and nothing else does."
  type        = string
  default     = ""
  sensitive   = true
  nullable    = false
}

variable "ingest_schedule_expression" {
  description = "When EventBridge invokes the ingestion, in UTC. The default is 04:00 UTC, 01:00 in Buenos Aires."
  type        = string
  default     = "cron(0 4 * * ? *)"
  nullable    = false
}

variable "day_close_schedule_expression" {
  description = "When EventBridge invokes the day close as a backup, in UTC: it closes that day (in Buenos Aires) with whatever chains are in, in case one never sent its notice. The default is 13:00 UTC, 10:00 in Buenos Aires."
  type        = string
  default     = "cron(0 13 * * ? *)"
  nullable    = false
}

variable "admin_email" {
  description = "Email of the platform admin, which SNS emails when a chain fails to process. Deploy passes the BOOTSTRAP_EMAIL secret; SNS first sends it an email to confirm the subscription. Empty creates no email subscription."
  type        = string
  default     = ""
  sensitive   = true
  nullable    = false
}
