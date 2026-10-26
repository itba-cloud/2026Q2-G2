output "queue_name" {
  description = "The name of the queue."
  value       = aws_sqs_queue.this.name
}

output "queue_arn" {
  description = "The ARN of the queue."
  value       = aws_sqs_queue.this.arn
}

output "queue_url" {
  description = "The URL of the queue."
  value       = aws_sqs_queue.this.url
}

output "dlq_name" {
  description = "The name of the dead-letter queue."
  value       = aws_sqs_queue.dlq.name
}

output "dlq_arn" {
  description = "The ARN of the dead-letter queue."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_url" {
  description = "The URL of the dead-letter queue."
  value       = aws_sqs_queue.dlq.url
}

output "max_receive_count" {
  description = "How many receives a message gets before it moves to the DLQ, for consumers that report their last attempt."
  value       = var.max_receive_count
}
