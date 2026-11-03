# 0005. How this runs in production on AWS

**Status:** proposed (designed, not applied)

## Context

Locally, Jira can't reach the receiver, so a poller stands in for the
webhook, and the sandbox is a local container. For a team, the same code
has to run unattended, with credentials scoped to what each part does.

## Decision

```
Jira webhook ──HTTPS──► API Gateway ──► Lambda: receiver ──signal──► Temporal server
                                       (signature check, no state)   ECS service + RDS Postgres
                                                                       private subnets, no public access
                                                                            ▲ poll (outbound only)
ECS service: worker  ────────────────────────────────────────────────────┘
  IAM role: ecs:RunTask on ONE task definition (the sandbox), Secrets Manager reads
  per step ──RunTask──► Fargate task: sandbox (same image as locally)
                          working copy via a short-lived volume, model key only,
                          egress only to the package index and the model API
```

- **Receiver**: Lambda behind API Gateway; verifies Jira's signature and
  makes one Temporal call. It holds no state.
- **Temporal**: the server as an ECS service on RDS Postgres, in private
  subnets; the UI behind the VPN or an SSO proxy, never public. Temporal
  Cloud is the alternative when operating it isn't worth it.
- **Worker**: an ECS service with a desired count; ECS replaces a dead one,
  Temporal re-queues its activity when the heartbeat stops.
- **Sandbox**: one Fargate task per step, started by the worker with
  `RunTask` and stopped in a `finally`; tagged with the ticket key, so a
  retry can find and stop an orphan.
- **Secrets**: Secrets Manager; the GitHub credential becomes a GitHub App
  with installation tokens of one hour, scoped to the target repositories.
- **Everything** in Terraform, with a budget alarm tagged by project.

## Consequences

- The worker's role can start the sandbox and nothing else; a leaked worker
  credential starts sandboxes, not infrastructure.
- Egress filtering for the sandbox (a proxy or a firewall with an allow
  list) is the piece that needs the most care.
