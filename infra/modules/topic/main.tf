# One SNS topic with its subscriptions, each with its own filter policy: the
# fan-out. Adding a consumer is adding a subscription, not touching who
# publishes.

# Not encrypted: the Learner Lab cannot create a KMS key, and the AWS-managed
# aws/sns key would need KMS permissions on the publishers that no deploy has
# tried. Every publish already goes over TLS (the policy below).
#tfsec:ignore:aws-sns-enable-topic-encryption
resource "aws_sns_topic" "this" {
  name = var.name
}

# A resource policy, not IAM: the Learner Lab allows it. Within the account,
# its roles' own permissions are enough to publish and subscribe; this only
# keeps anyone from publishing without TLS. Publish, not sns:*: a topic
# policy takes no wildcard action.
resource "aws_sns_topic_policy" "this" {
  arn = aws_sns_topic.this.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.this.arn
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

# An SQS subscription gets the message as published (raw delivery), with its
# attributes as the SQS message's: its consumer reads the event, not SNS's
# envelope. An email one waits for its owner to confirm it from the email
# SNS sends, and until then gets nothing.
resource "aws_sns_topic_subscription" "this" {
  for_each = var.subscriptions

  topic_arn            = aws_sns_topic.this.arn
  protocol             = each.value.protocol
  endpoint             = each.value.endpoint
  filter_policy        = each.value.filter_policy
  raw_message_delivery = each.value.protocol == "sqs"
}
