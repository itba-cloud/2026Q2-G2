variable "bucket" {
  description = "Name of the portal's bucket, which must already exist (scripts/buckets.sh creates it)."
  type        = string
  nullable    = false
}

variable "config" {
  description = "What the portal reads at startup, written to config.json: the API's URL and the Hosted UI's."
  type        = map(string)
  nullable    = false
}
