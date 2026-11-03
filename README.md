# ticket-to-pr

A durable pipeline that takes a Jira ticket to a reviewed pull request.
Temporal orchestrates, an agent writes the spec, the tests and the code, and
deterministic gates decide what gets through. People approve at two points:
the spec, and the pull request.

> Status: phase 1 skeleton. The workflow, signals, retries and timeouts run
> for real on a self-hosted Temporal; the Jira, Claude Code and GitHub
> adapters are fakes for now.

## The flow

```
Jira: "Ready for agent"
   │ webhook (signed)
   ▼
receiver (FastAPI) ── signal-with-start ──► Temporal server (history + queues)
                                                 ▲ poll
                                                 │
worker ─ TicketWorkflow ────────────────────────┘
  1 read ticket → definition of ready?  no → questions on the ticket, "Needs info", wait
  2 draft spec → comment on the ticket, "Spec review", wait
        "Ready for agent" again → changes requested → redraft
        "Spec approved"         → continue
  3 "Agent working" → write tests from the acceptance criteria (frozen at a SHA)
  4 implement → gates → failures go back to the agent, at most 2 fixes
  5 open the pull request → link on the ticket → "In review"
```

Every human wait times out after 7 days and says so on the ticket.

## Run it locally

Needs Python 3.12 and Docker.

```bash
make install
make up                                   # Temporal + Postgres + UI on http://localhost:8233
make worker                               # in one terminal
.venv/bin/python -m pipeline.cli start LIST-7      # = ticket moved to "Ready for agent"
.venv/bin/python -m pipeline.cli stage LIST-7      # waiting for spec approval
.venv/bin/python -m pipeline.cli approve LIST-7    # = ticket moved to "Spec approved"
```

Open the UI to see the run's event history.

## Tests

```bash
make lint test
```

The workflow tests run on Temporal's time-skipping test server, so a
seven-day wait for a reviewer finishes in milliseconds.

## Layout

| Path | What it is |
|---|---|
| `pipeline/workflow.py` | The workflow: order, waits, retries. Deterministic, no I/O. |
| `pipeline/activities.py` | Every step with side effects, delegating to an adapter. |
| `pipeline/adapters/` | `base.py` interfaces; `fake.py` in-memory; Jira, GitHub, Claude Code next. |
| `pipeline/receiver.py` | Jira webhook → one Temporal call → 200. |
| `pipeline/worker.py` | Polls the task queue and runs the code. |
