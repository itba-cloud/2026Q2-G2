variable "name" {
  description = "Name of the repository, the cluster, the service, the task definition's family and their security group."
  type        = string
  nullable    = false
}

variable "region" {
  description = "Region the tasks send their logs to."
  type        = string
  nullable    = false
}

variable "vpc_id" {
  description = "ID of the VPC the security group belongs to."
  type        = string
  nullable    = false
}

variable "subnet_ids" {
  description = "Subnets associated with the task or service (the batch band)."
  type        = list(string)
  nullable    = false
}

variable "role_arn" {
  description = "ARN of the role the tasks run as and that pulls their image and writes their logs."
  type        = string
  nullable    = false
}

variable "image_tag" {
  description = "Tag of the image in the repository the tasks run."
  type        = string
  nullable    = false
}

variable "images_kept" {
  description = "How many images the repository keeps; older ones expire."
  type        = number
  default     = 5
  nullable    = false
}

variable "cpu" {
  description = "Number of cpu units used by the task (1024 is one vCPU)."
  type        = number
  default     = 1024
  nullable    = false
}

variable "memory" {
  description = "Amount (in MiB) of memory used by the task."
  type        = number
  default     = 4096
  nullable    = false
}

variable "environment_variables" {
  description = "Map of environment variables of the worker container."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "queue_name" {
  description = "Name of the SQS queue whose messages scale the service."
  type        = string
  nullable    = false
}

variable "max_capacity" {
  description = "The most worker tasks that run at once."
  type        = number
  default     = 4
  nullable    = false

  validation {
    condition     = var.max_capacity >= 1
    error_message = "max_capacity must be at least 1."
  }
}

variable "idle_minutes" {
  description = "Minutes with no job waiting or in flight before the service goes back to 0 tasks."
  type        = number
  default     = 5
  nullable    = false
}

variable "retention_in_days" {
  description = "Specifies the number of days you want to retain log events in the workers' log group."
  type        = number
  default     = 7
  nullable    = false
}
