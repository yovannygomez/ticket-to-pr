# 0002. A throwaway sandbox per step, with different permissions per step

**Status:** accepted

## Context

An agent told to make the tests pass will sometimes change the tests. A
rule in a prompt or a CLAUDE.md lowers the odds; it doesn't enforce
anything. And an agent that runs where the credentials live can do
anything those credentials allow.

## Decision

Claude Code runs headless in a container created for one step and removed
when it ends. The container gets the ticket's working copy and a model key,
nothing else: no repository token, no cloud credentials. The worker clones,
commits and pushes, with the token passed per command, never written into
the clone.

The steps are split so that writing the tests and writing the code are
different runs with different permissions:

| Step | Can edit | Can't edit |
|---|---|---|
| write tests | `tests/` | `app/`, `.github/`, `.claude/` |
| implement | `app/` | `tests/`, `.github/`, `.claude/` |

Both run in accept-edits mode with deny rules, which win over any allow;
Bash is limited to `pytest` and `ruff check`; web access is denied.

## Consequences

- Verified with a probe: asked to edit both, the implement step changed
  `app/main.py` and was refused on `tests/test_listings.py`.
- The tests are written before the code and pushed as their own commit, so
  the PR shows them separately and the gates can check they didn't change.
- Each step pays for a cold start (installing the repo's dependencies, a
  few seconds); acceptable next to a model run of a minute or two.
