data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# The Learner Lab does not allow creating IAM roles: every Lambda, task and
# service runs as this one.
data "aws_iam_role" "lab" {
  name = "LabRole"
}
