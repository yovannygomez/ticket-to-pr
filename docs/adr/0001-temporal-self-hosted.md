# 0001. Temporal, self-hosted, for the ticket workflow

**Status:** accepted

## Context

A ticket's trip to a pull request lasts hours or days and spends most of
that time waiting on people: answers to questions, a spec approval, a CI
run. Along the way it calls flaky things (an LLM, Jira, GitHub) and runs
steps that take minutes. A queue plus a state table in a database could do
it, but then retries, timeouts, "which step is ticket 123 on", reminders
and resuming after a crash are all code to write and get right.

## Decision

One Temporal workflow per ticket, with the ticket key as the workflow ID,
so a duplicate webhook can't start a second run. Everything with side
effects is an activity with its own timeout and retry policy; long steps
heartbeat, so a dead worker is noticed in two minutes, not an hour. People
are signals (`ready_for_agent`, `spec_approved`) and every wait for them is
a durable timer of seven days.

Self-hosted (server + Postgres, the same shape locally and on AWS) rather
than Temporal Cloud: it keeps the history in our own account and shows the
whole system. The code doesn't depend on the choice: moving to Cloud is a
different address and credentials.

## Consequences

- Workflow code must be deterministic: no clock, randomness or I/O in it;
  the workflow is replayed from its history after a crash.
- Activities must be idempotent, because they can run twice: moving a
  ticket to the status it's already in is a no-op, an open PR for the
  branch is returned instead of a new one, the tests step reuses an
  already pushed tests commit, a container left by a dead worker is removed
  before its step runs again.
- Operating Postgres and the server is ours; on Cloud it wouldn't be.
