# One SQS queue with its dead-letter queue and its access policy. A message
# received max_receive_count times without being deleted moves to the DLQ,
# where it waits for someone to look at it.

resource "aws_sqs_queue" "dlq" {
  name                      = format("%s-dlq", var.name)
  message_retention_seconds = var.dlq_message_retention_seconds
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "this" {
  name                       = var.name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  receive_wait_time_seconds  = var.receive_wait_time_seconds
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Only this queue may move messages into its DLQ.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.this.arn]
  })
}

# A resource policy, not IAM: the Learner Lab allows it. Each service in
# allowed_senders may send, but only on behalf of its one source ARN; and no
# one may talk to the queue without TLS, which also keeps the policy from
# being empty when nothing outside the account sends.
resource "aws_sqs_queue_policy" "this" {
  queue_url = aws_sqs_queue.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "DenyInsecureTransport"
          Effect    = "Deny"
          Principal = "*"
          Action    = "sqs:*"
          Resource  = aws_sqs_queue.this.arn
          Condition = { Bool = { "aws:SecureTransport" = "false" } }
        },
      ],
      [
        for service, source_arn in var.allowed_senders : {
          Sid       = format("AllowSendFrom%s", upper(split(".", service)[0]))
          Effect    = "Allow"
          Principal = { Service = service }
          Action    = "sqs:SendMessage"
          Resource  = aws_sqs_queue.this.arn
          Condition = { ArnEquals = { "aws:SourceArn" = source_arn } }
        }
      ],
    )
  })
}
