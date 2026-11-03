# 0003. Deterministic gates decide; the repository's CI is the source of truth

**Status:** accepted

## Context

The agent's own report ("all tests pass") is not evidence, and an LLM
reviewer is probabilistic: rerun it and the verdict can change. What blocks
a change has to give the same answer every time for the same code.

## Decision

Two layers, both plain code.

**Local gates**, in the sandbox, on exactly what was pushed (the working
copy is reset to the branch first):

1. `ruff check`
2. `pytest`
3. existing tests unchanged: nothing under `tests/` modified or deleted
   against main
4. the approved tests unchanged since the SHA of their commit
5. no new `skip` or `xfail`
6. main's version of the existing tests, run against this branch's code:
   `git checkout origin/main -- tests/` restores files that exist on main
   and keeps the branch's new ones, so one run covers both

Failures go back to the agent, at most twice. They are cheap and catch most
problems before a PR exists.

**The repository's CI** on the draft PR decides. A failed job's log goes
back to the agent, which pushes a fix to the same PR, at most twice. Only a
green PR leaves draft and reaches the reviewer.

The agent can't change the judge: its GitHub token has no Workflows
permission and its sandbox denies edits to `.github/`.

## Consequences

- In the first real run the agent's implement step couldn't write
  (a permissions mistake, since fixed); it reported the failure, the gates
  caught 15 failing tests, and the second attempt passed. Nothing reached
  the PR on the agent's word alone.
- A test that legitimately has to change can't today; that would be a
  human-approved exception, not something the agent decides.
