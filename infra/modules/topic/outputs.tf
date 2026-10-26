output "topic_name" {
  description = "The name of the topic."
  value       = aws_sns_topic.this.name
}

output "topic_arn" {
  description = "The ARN of the topic, where its publishers publish."
  value       = aws_sns_topic.this.arn
}

output "subscription_arns" {
  description = "The ARN of each subscription, by its name in var.subscriptions. An email subscription has one even while it waits for its confirmation."
  value       = { for name, subscription in aws_sns_topic_subscription.this : name => subscription.arn }
}
