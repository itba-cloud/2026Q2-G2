# One Lambda with its log group and, optionally, what invokes it: an
# EventBridge cron, an SQS queue, or both. The log group is created here rather than by Lambda on its
# first run, so the retention applies and Destroy deletes it.
resource "aws_cloudwatch_log_group" "this" {
  name              = format("/aws/lambda/%s", var.function_name)
  retention_in_days = var.retention_in_days
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  description      = var.description
  filename         = var.filename
  source_code_hash = filebase64sha256(var.filename)
  handler          = var.handler
  runtime          = var.runtime
  role             = var.role
  memory_size      = var.memory_size
  timeout          = var.timeout

  ephemeral_storage {
    size = var.ephemeral_storage_size
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.this.name
  }

  dynamic "environment" {
    for_each = length(var.environment_variables) > 0 ? [var.environment_variables] : []

    content {
      variables = environment.value
    }
  }

  dynamic "vpc_config" {
    for_each = var.vpc_config[*]

    content {
      subnet_ids         = vpc_config.value.subnet_ids
      security_group_ids = vpc_config.value.security_group_ids
    }
  }
}

# How many times Lambda retries an asynchronous invocation that failed, like
# the cron's and run-ingest's.
resource "aws_lambda_function_event_invoke_config" "this" {
  function_name          = aws_lambda_function.this.function_name
  maximum_retry_attempts = var.maximum_retry_attempts
}

# The cron: a rule on EventBridge's default bus, the function as its target,
# and the permission for EventBridge to invoke it. All three or none: a
# schedule_expression is what asks for them, so a null one is the switch (a
# separate boolean could only disagree with it).
resource "aws_cloudwatch_event_rule" "this" {
  count = var.schedule_expression != null ? 1 : 0

  name                = var.function_name
  description         = format("Invokes %s on a schedule.", var.function_name)
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "this" {
  count = var.schedule_expression != null ? 1 : 0

  rule = aws_cloudwatch_event_rule.this[0].name
  arn  = aws_lambda_function.this.arn
}

resource "aws_lambda_permission" "this" {
  count = var.schedule_expression != null ? 1 : 0

  statement_id  = "AllowEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this[0].arn
}

# The queue: Lambda polls it and invokes the function with batches of its
# messages. The function reports which messages of a batch failed, so only
# those come back (and, after the queue's max_receive_count, go to its DLQ),
# not the whole batch. A null sqs_trigger is the switch, like the cron's.
resource "aws_lambda_event_source_mapping" "this" {
  count = var.sqs_trigger != null ? 1 : 0

  event_source_arn        = var.sqs_trigger.queue_arn
  function_name           = aws_lambda_function.this.arn
  batch_size              = var.sqs_trigger.batch_size
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.sqs_trigger.maximum_concurrency
  }
}
