# ticket-to-pr — working notes for Claude

A durable Jira-ticket-to-pull-request pipeline: Temporal orchestrates, Claude
Code writes tests and code in a throwaway sandbox, deterministic gates and the
target repository's CI decide, LangFuse traces cost. The target repository is
[listings-api](https://github.com/yovannygomez/listings-api). Design and
decisions: `README.md` and `docs/adr/`; production design: `infra/` (not applied).

## Running it

- `make up` (Temporal + Postgres + UI on :8233, builds the sandbox image),
  `make worker`, `make poller` (local stand-in for the Jira webhook).
- `.env` holds every secret and is git-ignored. Never print, commit or paste
  its values; check which keys are set by name only.
- `ADAPTERS=fake` runs the workflow without Jira, GitHub or a model.
- Docker runs in Lima (`docker context lima-default`), not Docker Desktop.

## Rules for changing it

- `pipeline/workflow.py` must stay deterministic: no clock, randomness or I/O.
  Anything with side effects is an activity, and activities must be
  idempotent (they can run twice).
- A change to the workflow while a run is open must keep the same sequence of
  commands, or the run fails to replay. Stop the worker and finish or
  terminate open runs before reordering steps.
- Sandbox permissions live in `sandbox/settings/*.json`. Any change there is
  verified with a probe (ask Claude Code to edit both `app/` and `tests/` and
  check what was refused), not assumed.
- `make lint test` before every commit. The workflow tests use Temporal's
  time-skipping server.
- Commits use the GitHub noreply email configured in this repository.
- Ask before pushing, before making the repository public, and before any
  `terraform apply`.
