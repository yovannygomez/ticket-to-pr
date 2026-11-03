# 0004. LangFuse inside activities, one session per ticket

**Status:** accepted

## Context

Temporal shows the process: which step, how long, how many retries. It
doesn't show what happened inside a model call: the prompt, the answer, the
tokens, the cost. "Why did the agent do that?" and "what does a ticket
cost?" need that.

## Decision

Every model call is traced in LangFuse, from inside the activities, never
from workflow code: a workflow is replayed after a crash and would log the
same calls twice. Each observation carries the ticket key as its session,
so a session is a ticket.

- Claude Code runs are generations built from its JSON output: tokens
  (including cache reads and writes), the cost it reports, turns and
  duration.
- Anthropic SDK calls (readiness, spec) are traced by LangFuse's own
  instrumentation; our step is a span around it, so the cost is counted
  once.

Without LangFuse keys the tracing is a no-op.

## Consequences

- Real numbers: LIST-5 cost $0.12 (readiness $0.0003, spec $0.02, tests
  $0.06, implementation $0.04); LIST-4, with questions, a redraft and a
  failed first implementation, $0.22.
- Prompts and code go to LangFuse Cloud. For a client's code that's a
  decision of its own: self-hosting LangFuse, or masking.
