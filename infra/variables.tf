variable "project" {
  description = "Name prefix and the Project tag on every resource (the budget filters on it)."
  type        = string
  default     = "ticket-to-pr"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "temporal_version" {
  description = "temporalio/server and temporalio/admin-tools image tag."
  type        = string
  default     = "1.32.2"
}

variable "image_tag" {
  description = "Tag of the worker, receiver and sandbox images pushed to ECR."
  type        = string
  default     = "latest"
}

variable "worker_count" {
  type    = number
  default = 1
}

variable "monthly_budget_usd" {
  type    = number
  default = 30
}

variable "budget_email" {
  description = "Who gets the budget alerts."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository the agent works on."
  type        = string
}

variable "reviewer" {
  description = "GitHub login the ready pull requests are assigned to."
  type        = string
}

variable "jira_base_url" {
  type = string
}
