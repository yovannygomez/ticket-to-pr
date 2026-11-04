# ticket-to-pr

A durable pipeline that takes a Jira ticket to a reviewed pull request.
**Temporal** orchestrates, **Claude Code** writes the tests and the code in a
throwaway sandbox, **deterministic gates** and the repository's **CI** decide
what gets through, and **LangFuse** shows what every step cost. People stay
in charge at two points: they approve the spec, and they review the PR.

## Demo

**[Watch the demo (14 min)](docs/demo.mp4)**: two Jira tickets run in parallel, one
stops to ask questions before its spec, and each ends with its own pull
request with CI green: [#5](https://github.com/yovannygomez/listings-api/pull/5)
and [#6](https://github.com/yovannygomez/listings-api/pull/6).

| Time | What you see |
|---|---|
| 0:00 | The idea, and the tickets on the Jira board |
| 1:00 | LIST-9 starts: its Temporal run and event history |
| 2:30 | The agent asks questions instead of guessing |
| 3:00 | LIST-10 starts in parallel |
| 5:00 | Specs ready for review, approved in Jira |
| 7:30 | Tests, then code, in the sandbox: pending activities and heartbeats |
| 8:30 | The progress checklist on the ticket |
| 10:00 | The pull request: tests commit, code commit, CI green, assigned |
| 11:30 | LangFuse: every model call and the cost per ticket |
| 13:30 | One pull request per ticket |

It works on [listings-api](https://github.com/yovannygomez/listings-api), a
small FastAPI service, and has run real tickets end to end: about **$0.12 to
$0.22 per ticket**, most of it Claude Code, and mostly cache reads.

## The flow

```
Jira ticket ──"Ready for agent"──► receiver (or local poller) ──signal──► Temporal
                                                                            │
TicketWorkflow (one durable run per ticket)                                 │
                                                                            ▼
 1 Definition of ready    not testable yet? questions on the ticket → "Needs info" → wait
 2 Spec                   drafted from the ticket and the real code → "Spec review" → wait
                            "Ready for agent" again = changes requested → redraft
                            "Spec approved"        = go
 3 Tests                  Claude Code writes them from the acceptance tests; they must
                          fail first; pushed and frozen at a SHA          (can't edit app/)
 4 Implementation         Claude Code makes them pass                    (can't edit tests/)
 5 Local gates            ruff · pytest · existing tests unchanged · approved tests
                          unchanged since their SHA · no new skip/xfail · main's tests
                          on this branch            failures go back to the agent, max 2
 6 Draft PR + CI          the repository's CI is the source of truth
                          red → failed job's log back to the agent → push to the same PR
 7 Hand-off               out of draft, assigned to the reviewer → "In review"
```

Every human wait times out after 7 days and says so on the ticket. A
progress checklist on the ticket is edited in place at every step, so
nobody needs to open Temporal to know where a ticket is.

## What each tool shows

| Tool | Where | What you see |
|---|---|---|
| Jira | the ticket | the agent's questions, the spec, the progress checklist, the PR link |
| Temporal UI | http://localhost:8233 | one run per ticket: every step, its input and output, retries, timers, the worker that ran it |
| LangFuse | Sessions → the ticket key | every model call with prompt, output, tokens, cost and latency |
| GitHub | the PR | tests commit, implementation commit, CI, the spec and the gates in the description |

## Run it locally

Needs Python 3.12, Docker, a Jira Cloud site with these statuses (in order):
`To Do`, `Ready for agent`, `Needs info`, `Spec review`, `Spec approved`,
`Agent working`, `In review`, `Done`; a GitHub repository with CI; an
Anthropic API key; and a LangFuse project.

```bash
make install
cp .env.example .env        # fill in the values; .env is git-ignored
make up                     # Temporal + Postgres + UI, and builds the sandbox image
make worker                 # terminal 1: runs the workflow and its activities
make poller                 # terminal 2: watches Jira every 10 s (local stand-in for the webhook)
```

Then move a ticket to **Ready for agent** and watch it go. Without Jira,
`ADAPTERS=fake` runs the same workflow against in-memory adapters, driven by
`python -m pipeline.cli start|approve|stage KEY`.

```bash
make lint test              # the workflow tests run on Temporal's time-skipping server
```

## Decisions

| | |
|---|---|
| [0001](docs/adr/0001-temporal-self-hosted.md) | Temporal, self-hosted, for a process that lasts days and waits on people |
| [0002](docs/adr/0002-sandbox-per-step.md) | A throwaway sandbox per step, with different permissions per step |
| [0003](docs/adr/0003-deterministic-gates-and-ci.md) | Deterministic gates decide; the repository's CI is the source of truth |
| [0004](docs/adr/0004-langfuse-in-activities.md) | LangFuse inside activities, one session per ticket |
| [0005](docs/adr/0005-production-on-aws.md) | How this runs in production on AWS |

## Security in one paragraph

The sandbox gets the ticket's working copy and a model key, nothing else: the
worker does every push, with a token that never touches the clone's config.
The GitHub token is fine-grained to one repository and has no Workflows
permission, and the sandbox denies edits to `.github/`: the agent can trigger
CI and read its logs, but never change the CI that judges it. Each Claude Code
step runs with its own permissions, verified by a probe: in the implement
step an edit to `app/` succeeds and an edit to `tests/` is refused.

## Layout

| Path | What it is |
|---|---|
| `pipeline/workflow.py` | The workflow: order, waits, retries, the progress checklist. Deterministic, no I/O. |
| `pipeline/activities.py` | Every step with side effects, delegating to an adapter; heartbeats on long steps. |
| `pipeline/adapters/` | `base.py` interfaces; `jira.py`, `github.py`, `claude.py`, `sandbox.py`; `fake.py` for tests. |
| `pipeline/receiver.py` | Jira webhook → one Temporal call → 200. |
| `pipeline/poller.py` | Local stand-in for the webhook. |
| `sandbox/` | The sandbox image, its per-step permissions, and the gates. |
| `infra/`, `deploy/` | The AWS design in Terraform (not applied) and the worker and receiver images. |

## Not done yet

- The AWS deployment in [0005](docs/adr/0005-production-on-aws.md) is written in Terraform in [`infra/`](infra/) and validated, not applied; the Fargate sandbox adapter is the code still to write.
- The agent comments with the token owner's Jira account; in a team it gets its own service account.
- Review comments on the PR going back to the agent: today the run ends when the PR is handed to the reviewer. Next: keep the workflow open, turn "changes requested" into a signal, fix on the same PR, and end on merge.
- A Bitbucket adapter (the interfaces are ready for it).

## License

[MIT](LICENSE)
