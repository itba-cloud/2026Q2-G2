variable "function_name" {
  description = "Unique name for your Lambda Function."
  type        = string
  nullable    = false
}

variable "description" {
  description = "Description of what your Lambda Function does."
  type        = string
  default     = null
}

variable "filename" {
  description = "Path to the function's deployment package (a zip built by `make lambdas`)."
  type        = string
  nullable    = false
}

variable "handler" {
  description = "Function entry point in your code."
  type        = string
  nullable    = false
}

variable "runtime" {
  description = "Identifier of the function's runtime."
  type        = string
  default     = "python3.12"
  nullable    = false
}

variable "role" {
  description = "Amazon Resource Name (ARN) of the function's execution role."
  type        = string
  nullable    = false
}

variable "memory_size" {
  description = "Amount of memory in MB your Lambda Function can use at runtime."
  type        = number
  default     = 256
  nullable    = false
}

variable "timeout" {
  description = "Amount of time your Lambda Function has to run in seconds."
  type        = number
  default     = 30
  nullable    = false
}

variable "ephemeral_storage_size" {
  description = "The size of the function's /tmp directory in MB."
  type        = number
  default     = 512
  nullable    = false
}

variable "environment_variables" {
  description = "Map of environment variables that are accessible from the function code during execution."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "vpc_config" {
  description = "Subnets and security groups to run the function inside a VPC. Null runs it outside, with direct internet access."
  type = object({
    subnet_ids         = list(string)
    security_group_ids = list(string)
  })
  default = null
}

variable "retention_in_days" {
  description = "Specifies the number of days you want to retain log events in the function's log group."
  type        = number
  default     = 7
  nullable    = false
}

variable "schedule_expression" {
  description = "The scheduling expression of an EventBridge rule that invokes the function, for example cron(0 4 * * ? *) (in UTC). Null creates no rule."
  type        = string
  default     = null
}

variable "maximum_retry_attempts" {
  description = "Maximum number of times to retry when the function returns an error on an asynchronous invocation, between 0 and 2."
  type        = number
  default     = 2
  nullable    = false

  validation {
    condition     = var.maximum_retry_attempts >= 0 && var.maximum_retry_attempts <= 2
    error_message = "maximum_retry_attempts must be between 0 and 2."
  }
}

variable "sqs_trigger" {
  description = "The SQS queue whose messages invoke the function: its ARN, how many messages each invocation gets at most (batch_size, 10 by default) and how many invocations the queue keeps running at once (maximum_concurrency, from 2). The queue's visibility timeout must be longer than the function's timeout. Null creates no event source mapping."
  type = object({
    queue_arn           = string
    batch_size          = optional(number, 10)
    maximum_concurrency = optional(number, 2)
  })
  default = null

  validation {
    condition     = try(var.sqs_trigger.maximum_concurrency >= 2 && var.sqs_trigger.maximum_concurrency <= 1000, true)
    error_message = "sqs_trigger.maximum_concurrency must be between 2 and 1000."
  }

  validation {
    condition     = try(var.sqs_trigger.batch_size >= 1 && var.sqs_trigger.batch_size <= 10, true)
    error_message = "sqs_trigger.batch_size must be between 1 and 10."
  }
}
