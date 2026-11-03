"""TicketWorkflow: one durable run per ticket, from "Ready for agent" to an open PR.

Deterministic by design: no clock, no randomness, no I/O here. Everything
with side effects is an activity, whose result is recorded in the history,
so a replay after a crash takes exactly the same path.

Two human checkpoints, both driven by moving the ticket in the tracker:
  - "Ready for agent" -> signal `ready_for_agent` (start, answered questions,
    or changes requested on the spec)
  - "Spec approved"   -> signal `spec_approved`
The second checkpoint, the PR review, happens in the repository.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pipeline.activities import Activities
    from pipeline.models import (
        Comment,
        GateReport,
        GatesInput,
        Implementation,
        ImplementInput,
        Move,
        PullRequestInput,
        Spec,
        Ticket,
        WriteTestsInput,
        WrittenTests,
    )

NEEDS_INFO = "Needs info"
SPEC_REVIEW = "Spec review"
AGENT_WORKING = "Agent working"
IN_REVIEW = "In review"

HUMAN_WAIT = timedelta(days=7)
MAX_QUESTION_ROUNDS = 3
MAX_SPEC_DRAFTS = 3
MAX_FIX_ATTEMPTS = 2

QUICK = dict(
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(maximum_attempts=5),
)
MODEL = dict(
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy=RetryPolicy(maximum_attempts=3),
)
AGENT_RUN = dict(
    start_to_close_timeout=timedelta(hours=1),
    heartbeat_timeout=timedelta(minutes=2),
    retry_policy=RetryPolicy(maximum_attempts=3),
)


class Stopped(Exception):
    """The workflow ends early and says why on the ticket."""


@workflow.defn
class TicketWorkflow:
    def __init__(self) -> None:
        self._ready_signals = 0
        self._approved = False
        self._stage = "starting"

    @workflow.signal
    def ready_for_agent(self) -> None:
        self._ready_signals += 1

    @workflow.signal
    def spec_approved(self) -> None:
        self._approved = True

    @workflow.query
    def stage(self) -> str:
        return self._stage

    @workflow.run
    async def run(self, key: str) -> str:
        try:
            ticket = await self._ready_ticket(key)
            spec = await self._approved_spec(ticket)
            await self._move(key, AGENT_WORKING)
            tests = await self._write_tests(ticket, spec)
            implementation, report = await self._implement_until_green(ticket, spec, tests)
            pull_request = await workflow.execute_activity_method(
                Activities.open_pull_request, PullRequestInput(ticket, spec, implementation, report), **QUICK
            )
            await self._comment(key, f"Pull request ready for review: {pull_request.url}")
            await self._move(key, IN_REVIEW)
            self._stage = "done"
            return pull_request.url
        except Stopped as stop:
            await self._comment(key, f"Agent stopped: {stop}")
            self._stage = "stopped"
            return f"stopped: {stop}"

    async def _ready_ticket(self, key: str) -> Ticket:
        """Definition of ready: no spec until the ticket has what it needs."""
        self._stage = "checking readiness"
        for _ in range(MAX_QUESTION_ROUNDS):
            ticket = await workflow.execute_activity_method(Activities.read_ticket, key, **QUICK)
            readiness = await workflow.execute_activity_method(Activities.check_readiness, ticket, **MODEL)
            if readiness.ready:
                return ticket
            questions = "\n".join(f"- {question}" for question in readiness.questions)
            await self._comment(key, f"Before I can write a spec, I need:\n{questions}")
            await self._move(key, NEEDS_INFO)
            self._stage = "waiting for info"
            await self._wait_for_ready("no answer to the questions")
        raise Stopped(f"still not ready after {MAX_QUESTION_ROUNDS} rounds of questions")

    async def _approved_spec(self, ticket: Ticket) -> Spec:
        """Drafts the spec on the ticket; a move back to "Ready for agent"
        means changes were requested in the comments, so it redrafts."""
        for _ in range(MAX_SPEC_DRAFTS):
            self._stage = "drafting spec"
            spec = await workflow.execute_activity_method(Activities.draft_spec, ticket, **MODEL)
            await self._comment(ticket.key, spec.markdown)
            await self._move(ticket.key, SPEC_REVIEW)
            self._stage = "waiting for spec approval"
            seen = self._ready_signals
            reviewed = lambda seen=seen: self._approved or self._ready_signals > seen  # noqa: E731
            await self._wait(reviewed, "the spec was not reviewed")
            if self._approved:
                return spec
            ticket = await workflow.execute_activity_method(Activities.read_ticket, ticket.key, **QUICK)
        raise Stopped(f"spec not approved after {MAX_SPEC_DRAFTS} drafts")

    async def _write_tests(self, ticket: Ticket, spec: Spec) -> WrittenTests:
        self._stage = "writing tests"
        branch = f"feature/{ticket.key}"
        return await workflow.execute_activity_method(
            Activities.write_tests, WriteTestsInput(ticket, spec, branch), **AGENT_RUN
        )

    async def _implement_until_green(
        self, ticket: Ticket, spec: Spec, tests: WrittenTests
    ) -> tuple[Implementation, GateReport]:
        """The agent gets the gate failures back, at most MAX_FIX_ATTEMPTS times."""
        feedback: list[str] = []
        for _ in range(1 + MAX_FIX_ATTEMPTS):
            self._stage = "implementing"
            implementation = await workflow.execute_activity_method(
                Activities.implement, ImplementInput(ticket, spec, tests, feedback), **AGENT_RUN
            )
            self._stage = "running gates"
            report = await workflow.execute_activity_method(
                Activities.run_gates, GatesInput(implementation, tests), **AGENT_RUN
            )
            if report.passed:
                return implementation, report
            feedback = report.failures
        failures = "\n".join(f"- {failure}" for failure in feedback)
        raise Stopped(f"gates still failing after {MAX_FIX_ATTEMPTS} fixes:\n{failures}")

    async def _wait_for_ready(self, reason: str) -> None:
        seen = self._ready_signals
        await self._wait(lambda: self._ready_signals > seen, reason)

    async def _wait(self, condition, reason: str) -> None:
        try:
            await workflow.wait_condition(condition, timeout=HUMAN_WAIT)
        except TimeoutError:
            raise Stopped(f"{reason} within {HUMAN_WAIT.days} days") from None

    async def _comment(self, key: str, body: str) -> None:
        await workflow.execute_activity_method(Activities.comment, Comment(key, body), **QUICK)

    async def _move(self, key: str, status: str) -> None:
        await workflow.execute_activity_method(Activities.move_ticket, Move(key, status), **QUICK)
