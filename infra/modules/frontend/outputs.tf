output "bucket_name" {
  description = "The name of the bucket."
  value       = aws_s3_bucket_policy.this.bucket
}
