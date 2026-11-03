terraform {
  required_version = ">= 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.71"
    }
  }

  # Remote state for a real deployment: an S3 bucket with versioning and
  # native locking. Left commented so `terraform init` works without one.
  # backend "s3" {
  #   bucket       = "<state-bucket>"
  #   key          = "ticket-to-pr/terraform.tfstate"
  #   region       = "us-east-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = var.project
    }
  }
}
