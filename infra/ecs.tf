resource "aws_ecs_cluster" "main" {
  name = var.project

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# Private DNS: the worker and the receiver reach Temporal at
# temporal.<project>.internal:7233.
resource "aws_service_discovery_private_dns_namespace" "internal" {
  name = "${var.project}.internal"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "temporal" {
  name = "temporal"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.internal.id
    dns_records {
      type = "A"
      ttl  = 10
    }
  }
}

locals {
  temporal_address = "temporal.${aws_service_discovery_private_dns_namespace.internal.name}:7233"
  db_password      = "${aws_db_instance.temporal.master_user_secret[0].secret_arn}:password::"
  db_environment = [
    { name = "DB", value = "postgres12" },
    { name = "DB_PORT", value = "5432" },
    { name = "POSTGRES_SEEDS", value = aws_db_instance.temporal.address },
    { name = "POSTGRES_USER", value = aws_db_instance.temporal.username },
    { name = "SQL_TLS_ENABLED", value = "true" }, # RDS Postgres 16 forces TLS
    { name = "SQL_TLS_DISABLE_HOST_VERIFICATION", value = "true" },
  ]
  logs = { for name, group in aws_cloudwatch_log_group.service : name => {
    logDriver = "awslogs"
    options   = { awslogs-group = group.name, awslogs-region = var.region, awslogs-stream-prefix = name }
  } }
}

# --- Temporal server ----------------------------------------------------------

resource "aws_ecs_task_definition" "temporal" {
  family                   = "${var.project}-temporal"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.temporal.arn

  container_definitions = jsonencode([{
    name             = "temporal"
    image            = "temporalio/server:${var.temporal_version}"
    essential        = true
    portMappings     = [{ containerPort = 7233, protocol = "tcp" }]
    environment      = concat(local.db_environment, [{ name = "BIND_ON_IP", value = "0.0.0.0" }])
    secrets          = [{ name = "POSTGRES_PWD", valueFrom = local.db_password }]
    logConfiguration = local.logs["temporal"]
  }])
}

resource "aws_ecs_service" "temporal" {
  name            = "temporal"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.temporal.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.temporal.id]
  }

  service_registries {
    registry_arn = aws_service_discovery_service.temporal.arn
  }
}

# One-off admin task: creates and migrates the schema before the first start,
# and after each Temporal upgrade. Run it with `aws ecs run-task` (see README);
# the same definition with a command override creates the namespace.
resource "aws_ecs_task_definition" "temporal_admin" {
  family                   = "${var.project}-temporal-admin"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([{
    name        = "admin"
    image       = "temporalio/admin-tools:${var.temporal_version}"
    essential   = true
    environment = concat(local.db_environment, [{ name = "TEMPORAL_ADDRESS", value = local.temporal_address }])
    secrets     = [{ name = "SQL_PASSWORD", valueFrom = local.db_password }]
    entryPoint  = ["sh", "-c"]
    command = [join(" && ", [
      for db in ["temporal", "temporal_visibility"] : join(" && ", [
        "temporal-sql-tool --plugin postgres12 --ep $POSTGRES_SEEDS -p 5432 -u $POSTGRES_USER --tls --tls-disable-host-verification --db ${db} create-database || true",
        "temporal-sql-tool --plugin postgres12 --ep $POSTGRES_SEEDS -p 5432 -u $POSTGRES_USER --tls --tls-disable-host-verification --db ${db} setup-schema -v 0.0 || true",
        "temporal-sql-tool --plugin postgres12 --ep $POSTGRES_SEEDS -p 5432 -u $POSTGRES_USER --tls --tls-disable-host-verification --db ${db} update-schema -d /etc/temporal/schema/postgresql/v12/${db == "temporal" ? "temporal" : "visibility"}/versioned",
      ])
    ])]
    logConfiguration = local.logs["temporal-admin"]
  }])
}

# --- Worker -------------------------------------------------------------------

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.images["worker"].repository_url}:${var.image_tag}"
    essential = true
    environment = [
      { name = "ADAPTERS", value = "real" },
      { name = "SANDBOX", value = "fargate" },
      { name = "TEMPORAL_ADDRESS", value = local.temporal_address },
      { name = "TEMPORAL_NAMESPACE", value = "default" },
      { name = "TASK_QUEUE", value = "tickets" },
      { name = "JIRA_BASE_URL", value = var.jira_base_url },
      { name = "GITHUB_REPOSITORY", value = var.github_repository },
      { name = "REVIEWER", value = var.reviewer },
      { name = "LANGFUSE_BASE_URL", value = "https://us.cloud.langfuse.com" },
      { name = "SANDBOX_CLUSTER", value = aws_ecs_cluster.main.arn },
      { name = "SANDBOX_TASK_DEFINITION", value = aws_ecs_task_definition.sandbox.family },
      { name = "SANDBOX_SUBNETS", value = join(",", aws_subnet.private[*].id) },
      { name = "SANDBOX_SECURITY_GROUP", value = aws_security_group.sandbox.id },
      { name = "WORKSPACES_BUCKET", value = aws_s3_bucket.workspaces.bucket },
    ]
    secrets = [
      { name = "JIRA_EMAIL", valueFrom = aws_secretsmanager_secret.app["jira_email"].arn },
      { name = "JIRA_API_TOKEN", valueFrom = aws_secretsmanager_secret.app["jira_api_token"].arn },
      { name = "GITHUB_TOKEN", valueFrom = aws_secretsmanager_secret.app["github_token"].arn },
      { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.app["anthropic_api_key"].arn },
      { name = "LANGFUSE_PUBLIC_KEY", valueFrom = aws_secretsmanager_secret.app["langfuse_public_key"].arn },
      { name = "LANGFUSE_SECRET_KEY", valueFrom = aws_secretsmanager_secret.app["langfuse_secret_key"].arn },
    ]
    logConfiguration = local.logs["worker"]
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.worker.id]
  }
}
