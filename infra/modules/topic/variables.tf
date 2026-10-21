variable "name" {
  description = "The name of the topic."
  type        = string
  nullable    = false
}

variable "subscriptions" {
  description = "The topic's subscriptions, by name: the protocol (sqs or email), the endpoint (a queue's ARN or an email address) and, optionally, the filter policy as JSON, on the message's attributes. A subscription without one gets every message."
  type = map(object({
    protocol      = string
    endpoint      = string
    filter_policy = optional(string)
  }))
  default  = {}
  nullable = false

  validation {
    condition     = alltrue([for subscription in values(var.subscriptions) : contains(["sqs", "email"], subscription.protocol)])
    error_message = "Each subscription's protocol must be sqs or email."
  }
}
