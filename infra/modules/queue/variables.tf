variable "name" {
  description = "The name of the queue. The DLQ gets the same name with a -dlq suffix."
  type        = string
  nullable    = false
}

variable "visibility_timeout_seconds" {
  description = "The visibility timeout for the queue: how long a received message stays hidden from other consumers. Longer than its consumer takes on one message."
  type        = number
  default     = 30
  nullable    = false
}

variable "message_retention_seconds" {
  description = "The number of seconds Amazon SQS retains a message."
  type        = number
  default     = 345600
  nullable    = false
}

variable "dlq_message_retention_seconds" {
  description = "The number of seconds Amazon SQS retains a message in the DLQ. Longer than the queue's, so a failure can wait for someone to look at it."
  type        = number
  default     = 1209600
  nullable    = false
}

variable "receive_wait_time_seconds" {
  description = "The time for which a ReceiveMessage call will wait for a message to arrive (long polling)."
  type        = number
  default     = 20
  nullable    = false
}

variable "max_receive_count" {
  description = "How many times a message is received without being deleted before it moves to the DLQ."
  type        = number
  default     = 3
  nullable    = false

  validation {
    condition     = var.max_receive_count >= 1 && var.max_receive_count <= 1000
    error_message = "max_receive_count must be between 1 and 1000."
  }
}

variable "allowed_senders" {
  description = "AWS services allowed to send to the queue, each only on behalf of one resource: service principal => source ARN (for example s3.amazonaws.com => the bucket's ARN)."
  type        = map(string)
  default     = {}
  nullable    = false
}
