# infra: the production design in Terraform

> **Not applied.** This is the design in [ADR 0005](../docs/adr/0005-production-on-aws.md)
> written as code and checked with `terraform validate`. The demo runs
> locally (see the main README).

## What it creates

| File | Resources |
|---|---|
| `network.tf` | VPC, 2 public subnets (NAT only), 2 private subnets (everything else), one security group per service |
| `database.tf` | RDS Postgres 16 for Temporal; RDS keeps the password in Secrets Manager |
| `ecs.tf` | Cluster, private DNS, the Temporal server service, a one-off admin task (schema), the worker service |
| `sandbox.tf` | The sandbox task definition (one Fargate task per step) and the S3 bucket for working copies |
| `receiver.tf` | API Gateway HTTP API → Lambda (the FastAPI receiver via the Lambda Web Adapter) |
| `iam.tf` | Execution role; worker role (RunTask on the sandbox only, PassRole on its roles only, the workspace bucket); sandbox role with no permissions |
| `secrets.tf` | Empty secrets for the tokens and keys; values are set outside Terraform |
| `registry.tf` | ECR repositories for the worker, receiver and sandbox images |
| `observability.tf` | Log groups (7 days) and a monthly budget filtered by the Project tag |

## Code still to write before applying

- A Fargate sandbox adapter: `ecs:RunTask` instead of `docker run`, with the
  working copy passed as a tarball through pre-signed S3 URLs, `startedBy`
  set to the ticket key, and `StopTask` in a `finally`.
- The receiver reading its webhook secret from `JIRA_WEBHOOK_SECRET_ARN`.

## Order, if it's ever applied

```bash
cp terraform.tfvars.example terraform.tfvars    # git-ignored
terraform init && terraform apply
# push the three images to the ECR repositories in the outputs
# set every secret listed in the `secrets` output
aws ecs run-task --cluster ticket-to-pr --launch-type FARGATE \
  --task-definition ticket-to-pr-temporal-admin \
  --network-configuration 'awsvpcConfiguration={subnets=[<private>],securityGroups=[<temporal sg>]}'
# then the same task with a command override:
#   temporal operator namespace create --namespace default
# point the Jira webhook at the jira_webhook_url output
terraform destroy                                # when the demo is over
```
