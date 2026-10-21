variable "name" {
  description = "Name of the instance, the Proxy, the secret and their security groups."
  type        = string
  nullable    = false
}

variable "vpc_id" {
  description = "ID of the VPC the security groups belong to."
  type        = string
  nullable    = false
}

variable "vpc_subnet_ids" {
  description = "One or more VPC subnet IDs to associate with the new proxy (the data band)."
  type        = list(string)
  nullable    = false
}

variable "db_subnet_group_name" {
  description = "Name of DB subnet group. DB instance will be created in the VPC associated with the DB subnet group."
  type        = string
  nullable    = false
}

variable "role_arn" {
  description = "The Amazon Resource Name (ARN) of the IAM role that the proxy uses to access secrets in AWS Secrets Manager."
  type        = string
  nullable    = false
}

variable "engine_version" {
  description = "The engine version to use. Its major version picks the parameter group family (postgresNN)."
  type        = string
  default     = "17"
  nullable    = false
}

variable "instance_class" {
  description = "The instance type of the RDS instance and of its read replica."
  type        = string
  default     = "db.t3.micro"
  nullable    = false
}

variable "allocated_storage" {
  description = "The allocated storage in gibibytes."
  type        = number
  default     = 20
  nullable    = false
}

variable "max_allocated_storage" {
  description = "The upper limit, in gibibytes, to which RDS can automatically scale the storage of the DB instance."
  type        = number
  default     = 100
  nullable    = false
}

variable "db_name" {
  description = "The name of the database to create when the DB instance is created."
  type        = string
  nullable    = false
}

variable "username" {
  description = "Username for the master DB user."
  type        = string
  nullable    = false
}

variable "read_replica_enabled" {
  description = "Whether to create a read replica of the instance."
  type        = bool
  default     = true
  nullable    = false
}

variable "retention_in_days" {
  description = "Specifies the number of days you want to retain log events in the RDS Proxy's log group."
  type        = number
  default     = 7
  nullable    = false
}
