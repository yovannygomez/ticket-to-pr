# Execution roles pull images, write logs and inject secrets. Task roles
# are what the code itself can do: the worker's is deliberately narrow.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.project}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [for secret in aws_secretsmanager_secret.app : secret.arn],
      [aws_db_instance.temporal.master_user_secret[0].secret_arn],
    )
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# --- The worker: start and stop the sandbox, and hand it the working copy ---

resource "aws_iam_role" "worker" {
  name               = "${var.project}-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "StartOnlyTheSandbox"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.sandbox.family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }

  statement {
    sid       = "WatchAndStopTasksInThisCluster"
    actions   = ["ecs:DescribeTasks", "ecs:StopTask", "ecs:ListTasks", "ecs:TagResource"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }

  statement {
    sid       = "PassOnlyTheSandboxRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.sandbox.arn]
  }

  statement {
    sid       = "WorkingCopies"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.workspaces.arn}/*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# --- The sandbox: no AWS permissions at all ----------------------------------
# It downloads the working copy and uploads the result through pre-signed
# S3 URLs the worker creates per run, so its role needs nothing.

resource "aws_iam_role" "sandbox" {
  name               = "${var.project}-sandbox"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "temporal" {
  name               = "${var.project}-temporal"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_caller_identity" "current" {}
