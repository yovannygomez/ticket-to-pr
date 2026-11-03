output "jira_webhook_url" {
  description = "Paste into Jira: Settings > System > Webhooks, with the same secret as jira_webhook_secret."
  value       = "${aws_apigatewayv2_api.webhooks.api_endpoint}/webhooks/jira"
}

output "ecr_repositories" {
  value = { for name, repo in aws_ecr_repository.images : name => repo.repository_url }
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "private_subnets" {
  value = aws_subnet.private[*].id
}

output "temporal_security_group" {
  value = aws_security_group.temporal.id
}

output "secrets" {
  description = "Set each value once with aws secretsmanager put-secret-value."
  value       = [for secret in aws_secretsmanager_secret.app : secret.name]
}
