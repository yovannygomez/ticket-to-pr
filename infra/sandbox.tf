# Working copies travel through S3 for the length of one run.
resource "aws_s3_bucket" "workspaces" {
  bucket_prefix = "${var.project}-workspaces-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "workspaces" {
  bucket                  = aws_s3_bucket.workspaces.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "workspaces" {
  bucket = aws_s3_bucket.workspaces.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 1
    }
  }
}

# The same image as locally (sandbox/Dockerfile). The worker starts one task
# per step with RunTask, passing the step's command, a pre-signed URL pair
# and the ticket key as startedBy, so a retry can find and stop an orphan.
resource "aws_ecs_task_definition" "sandbox" {
  family                   = "${var.project}-sandbox"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.sandbox.arn

  ephemeral_storage {
    size_in_gib = 21
  }

  container_definitions = jsonencode([{
    name      = "sandbox"
    image     = "${aws_ecr_repository.images["sandbox"].repository_url}:${var.image_tag}"
    essential = true
    secrets = [
      { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.app["anthropic_api_key"].arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.service["sandbox"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "sandbox"
      }
    }
  }])
}
