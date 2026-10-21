output "bucket_name" {
  description = "The name of the bucket."
  value       = aws_s3_bucket_versioning.this.bucket
}

output "bucket_arn" {
  description = "The ARN of the bucket."
  value       = local.bucket_arn
}
