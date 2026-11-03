# Created empty: Terraform never sees the values. Set each one once with
#   aws secretsmanager put-secret-value --secret-id <name> --secret-string '...'

locals {
  secrets = {
    jira_api_token      = "Jira API token of the agent's account"
    jira_email          = "Email of the agent's Jira account"
    jira_webhook_secret = "Shared secret Jira signs the webhook with"
    github_token        = "Token for the target repository (a GitHub App token in production)"
    anthropic_api_key   = "Model key, from a workspace with a spend limit"
    langfuse_public_key = "LangFuse public key"
    langfuse_secret_key = "LangFuse secret key"
  }
}

resource "aws_secretsmanager_secret" "app" {
  for_each                = local.secrets
  name                    = "${var.project}/${each.key}"
  description             = each.value
  recovery_window_in_days = 0 # demo: destroy removes them immediately
}
