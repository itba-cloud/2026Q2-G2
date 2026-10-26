variable "name" {
  description = "Prefix for the name of every resource in the network."
  type        = string
  nullable    = false
}

variable "region" {
  description = "Region of the VPC, used to build the endpoint service names."
  type        = string
  nullable    = false
}

variable "cidr" {
  description = "The IPv4 CIDR block for the VPC."
  type        = string
  nullable    = false
}

variable "azs" {
  description = "Availability zones of the subnets, one subnet per band in each."
  type        = list(string)
  nullable    = false
}

variable "app_subnets" {
  description = "CIDRs of the app band (the Lambdas inside the VPC and the interface endpoints), one per AZ."
  type        = list(string)
  nullable    = false
}

variable "batch_subnets" {
  description = "CIDRs of the batch band (the Fargate workers), one per AZ."
  type        = list(string)
  nullable    = false
}

variable "data_subnets" {
  description = "CIDRs of the data band (RDS and RDS Proxy), one per AZ."
  type        = list(string)
  nullable    = false
}

variable "gateway_endpoint_services" {
  description = "AWS services reached through a gateway endpoint (s3, dynamodb), as the suffix of com.amazonaws.<region>.<service>."
  type        = set(string)
  nullable    = false
}

variable "interface_endpoint_services" {
  description = "AWS services reached through an interface endpoint, as the suffix of com.amazonaws.<region>.<service>."
  type        = set(string)
  nullable    = false
}
