variable "bucket" {
  description = "Name of the bucket, which must already exist (scripts/buckets.sh creates it)."
  type        = string
  nullable    = false
}

variable "lifecycle_rules" {
  description = "One lifecycle rule per prefix: when current objects expire or move to STANDARD_IA (null for never), and when overwritten versions expire."
  type = map(object({
    expiration_days                    = optional(number)
    transition_days                    = optional(number)
    noncurrent_version_expiration_days = number
  }))
  nullable = false
}

variable "cors_allowed_origins" {
  description = "Origins allowed to POST objects from a browser, with a presigned POST."
  type        = list(string)
  nullable    = false
}
