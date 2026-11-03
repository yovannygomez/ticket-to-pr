# Jira's webhook: API Gateway -> Lambda (the receiver's FastAPI app, run
# unchanged through the Lambda Web Adapter) -> one Temporal call. The Lambda
# sits in the private subnets to reach Temporal; it has no inbound rules.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "receiver" {
  name               = "${var.project}-receiver"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "receiver_vpc" {
  role       = aws_iam_role.receiver.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "receiver_secret" {
  name = "webhook-secret"
  role = aws_iam_role.receiver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = aws_secretsmanager_secret.app["jira_webhook_secret"].arn
    }]
  })
}

resource "aws_lambda_function" "receiver" {
  function_name = "${var.project}-receiver"
  role          = aws_iam_role.receiver.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.images["receiver"].repository_url}:${var.image_tag}"
  timeout       = 10
  memory_size   = 512

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.receiver.id]
  }

  environment {
    variables = {
      TEMPORAL_ADDRESS        = local.temporal_address
      TEMPORAL_NAMESPACE      = "default"
      TASK_QUEUE              = "tickets"
      JIRA_WEBHOOK_SECRET_ARN = aws_secretsmanager_secret.app["jira_webhook_secret"].arn
      AWS_LWA_PORT            = "8000"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.service["receiver"].name
  }
}

resource "aws_apigatewayv2_api" "webhooks" {
  name          = "${var.project}-webhooks"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "receiver" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.receiver.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "jira" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /webhooks/jira"
  target    = "integrations/${aws_apigatewayv2_integration.receiver.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhooks.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 5
    throttling_burst_limit = 10
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.receiver.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/*"
}
