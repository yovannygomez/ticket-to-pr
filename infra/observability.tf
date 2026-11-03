resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(["temporal", "temporal-admin", "worker", "sandbox", "receiver"])
  name              = "/${var.project}/${each.key}"
  retention_in_days = 7
}

# Alerts at 50 % and 100 % of the monthly budget. It filters on the Project
# tag, so the account's other resources don't count; the tag must be
# activated once as a cost allocation tag in the Billing console.
resource "aws_budgets_budget" "project" {
  name         = var.project
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$${var.project}"]
  }

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }
}
