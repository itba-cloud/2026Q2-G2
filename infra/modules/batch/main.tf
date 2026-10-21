# The workers: an ECR repository for their image, and a Fargate service that
# runs it, scaled between 0 and max_capacity tasks by the messages in the
# jobs queue. The tasks run in subnets with no way out to the internet: the
# image comes from ECR and its layers from S3, both through endpoints.

resource "aws_ecr_repository" "this" {
  name = var.name

  # The tag is a hash of the image's sources (see the Makefile): a tag never
  # points at two different images.
  image_tag_mutability = "IMMUTABLE"

  # Destroy has to leave the lab account clean, images included.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Every code change pushes a new image; the old ones are only a rollback.
resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = format("Keep the last %d images", var.images_kept)
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.images_kept
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "this" {
  name = var.name
}

resource "aws_cloudwatch_log_group" "this" {
  name              = format("/ecs/%s", var.name)
  retention_in_days = var.retention_in_days
}

# Egress only, and only what the root module allows: the endpoints and the
# database live in other modules.
resource "aws_security_group" "this" {
  name        = var.name
  description = "Fargate workers: no ingress, egress to what the root module allows."
  vpc_id      = var.vpc_id

  tags = {
    Name = var.name
  }
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory

  # The Learner Lab does not allow creating roles: LabRole pulls the image
  # and writes the logs (execution), and is what the code runs as (task).
  execution_role_arn = var.role_arn
  task_role_arn      = var.role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = format("%s:%s", aws_ecr_repository.this.repository_url, var.image_tag)
    essential = true

    environment = [
      for name, value in var.environment_variables : { name = name, value = value }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  launch_type     = "FARGATE"

  # Zero until there is work: the autoscaling below owns the count.
  desired_count = 0

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = false
  }

  # Without this, every apply would put the count back to 0 and stop the
  # workers in the middle of a job.
  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "this" {
  service_namespace  = "ecs"
  resource_id        = format("service/%s/%s", aws_ecs_cluster.this.name, aws_ecs_service.this.name)
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = var.max_capacity
}

# Out: while messages wait in the queue with nobody to take them, one more
# worker per minute (two with a long backlog), up to max_capacity. An alarm
# with an Auto Scaling action invokes it again every minute it stays in
# ALARM, so the policy keeps adding until the queue is being drained.
resource "aws_appautoscaling_policy" "scale_out" {
  name               = format("%s-scale-out", var.name)
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 9
      scaling_adjustment          = 1
    }

    step_adjustment {
      metric_interval_lower_bound = 9
      scaling_adjustment          = 2
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "backlog" {
  alarm_name          = format("%s-backlog", var.name)
  alarm_description   = "Jobs waiting in the queue: add workers."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.queue_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.scale_out.arn]
}

# In: only to zero, and only once no message has been waiting or in flight
# for idle_minutes. A message being processed is in flight, so a worker in
# the middle of a job is never scaled away; scaling in one task at a time
# could stop a busy one, since ECS picks which task to stop.
resource "aws_appautoscaling_policy" "scale_in" {
  name               = format("%s-scale-in", var.name)
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "idle" {
  alarm_name          = format("%s-idle", var.name)
  alarm_description   = "No job waiting or in flight: stop every worker."
  evaluation_periods  = var.idle_minutes
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  # A queue with no traffic may stop reporting: that is idle too.
  treat_missing_data = "breaching"
  alarm_actions      = [aws_appautoscaling_policy.scale_in.arn]

  metric_query {
    id          = "pending"
    expression  = "visible + in_flight"
    label       = "Jobs waiting or in flight"
    return_data = true
  }

  metric_query {
    id = "visible"

    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = var.queue_name }
      stat        = "Maximum"
      period      = 60
    }
  }

  metric_query {
    id = "in_flight"

    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      dimensions  = { QueueName = var.queue_name }
      stat        = "Maximum"
      period      = 60
    }
  }
}
