"""TicketWorkflow: one durable run per ticket, from "Ready for agent" to an open PR.

Deterministic by design: no clock, no randomness, no I/O here. Everything
with side effects is an activity, whose result is recorded in the history,
so a replay after a crash takes exactly the same path.

Two human checkpoints, both driven by moving the ticket in the tracker:
  - "Ready for agent" -> signal `ready_for_agent` (start, answered questions,
    or changes requested on the spec)
  - "Spec approved"   -> signal `spec_approved`
The second checkpoint, the PR review, happens in the repository: the PR
opens as a draft, the repository's CI must pass (failures go back to the
agent), and only then is it marked ready and handed to the reviewer.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

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
MAX_CI_FIXES = 2

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
CI_WAIT = dict(
    start_to_close_timeout=timedelta(minutes=30),
    heartbeat_timeout=timedelta(minutes=2),
    retry_policy=RetryPolicy(maximum_attempts=3),
)


PROGRESS_MARKER = "ticket-to-pr progress"
STEPS = [
    ("checking readiness", "Ticket has what a spec needs"),
    ("drafting spec", "Spec drafted"),
    ("waiting for spec approval", "Spec approved by a person"),
    ("writing tests", "Tests written from the acceptance criteria"),
    ("implementing", "Code implemented"),
    ("running gates", "Local gates passed"),
    ("waiting for CI", "Pull request CI passed"),
    ("done", "Handed to the reviewer"),
]
STEP_OF = {stage: index for index, (stage, _) in enumerate(STEPS)} | {"waiting for info": 0}


def progress_comment(reached: int, stage: str, attempts: int) -> str:
    """A checklist: done, current, still to do. One comment, edited in place."""
    finished = stage == "done"
    lines = [f"## {PROGRESS_MARKER}"]
    for index, (_, label) in enumerate(STEPS):
        if index < reached or finished:
            mark = "✅"
        elif index == reached:
            mark = {"stopped": "⛔", "failed": "❌"}.get(stage, "⏳")
            if stage == "waiting for info":
                label += " (waiting for answers)"
            elif index in (STEP_OF["implementing"], STEP_OF["running gates"]) and attempts > 1:
                label += f" (attempt {attempts})"
        else:
            mark = "⬜"
        lines.append(f"- {mark} {label}")
    return "\n".join(lines)


def spec_comment(spec: Spec) -> str:
    tests = "\n".join(f"- {test}" for test in spec.acceptance_tests)
    return (
        "## Spec ready for review\n"
        "**To approve:** move the ticket to **Spec approved**. "
        "**To change it:** comment what to change, then move it back to **Ready for agent**.\n"
        f"{spec.markdown}\n## Acceptance tests\n{tests}"
    )


class Stopped(Exception):
    """The workflow ends early and says why on the ticket."""


@workflow.defn
class TicketWorkflow:
    def __init__(self) -> None:
        self._ready_signals = 0
        self._approved = False
        self._stage = "starting"
        self._reached = 0
        self._attempts = 0

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
        self._key = key
        try:
            ticket = await self._ready_ticket(key)
            spec = await self._approved_spec(ticket)
            await self._move(key, AGENT_WORKING)
            tests = await self._write_tests(ticket, spec)
            implementation, report = await self._implement_until_green(ticket, spec, tests, [])
            pull_request = await workflow.execute_activity_method(
                Activities.open_pull_request, PullRequestInput(ticket, spec, implementation, report), **QUICK
            )
            await self._comment(key, f"Draft pull request opened, waiting for CI: {pull_request.url}")
            await self._ci_until_green(ticket, spec, tests, implementation)
            await workflow.execute_activity_method(Activities.hand_to_reviewer, pull_request, **QUICK)
            await self._comment(key, f"CI passed. Pull request ready for review: {pull_request.url}")
            await self._move(key, IN_REVIEW)
            await self._report("done")
            return pull_request.url
        except Stopped as stop:
            await self._comment(key, f"Agent stopped: {stop}")
            await self._report("stopped")
            return f"stopped: {stop}"
        except ActivityError as error:
            # A step failed after all its retries: say so on the ticket, then
            # fail the run so it shows up as failed in Temporal.
            await self._comment(key, f"Agent failed at {self._stage}: {error.cause or error}")
            await self._report("failed")
            raise

    async def _ready_ticket(self, key: str) -> Ticket:
        """Definition of ready: no spec until the ticket has what it needs."""
        await self._report("checking readiness")
        for _ in range(MAX_QUESTION_ROUNDS):
            ticket = await workflow.execute_activity_method(Activities.read_ticket, key, **QUICK)
            readiness = await workflow.execute_activity_method(Activities.check_readiness, ticket, **MODEL)
            if readiness.ready:
                return ticket
            questions = "\n".join(f"- {question}" for question in readiness.questions)
            await self._comment(
                key,
                f"## Questions before the spec\n{questions}\n"
                "**Next step:** answer in a comment, then move the ticket back to **Ready for agent**.",
            )
            await self._move(key, NEEDS_INFO)
            await self._report("waiting for info")
            await self._wait_for_ready("no answer to the questions")
        raise Stopped(f"still not ready after {MAX_QUESTION_ROUNDS} rounds of questions")

    async def _approved_spec(self, ticket: Ticket) -> Spec:
        """Drafts the spec on the ticket; a move back to "Ready for agent"
        means changes were requested in the comments, so it redrafts."""
        for _ in range(MAX_SPEC_DRAFTS):
            await self._report("drafting spec")
            spec = await workflow.execute_activity_method(Activities.draft_spec, ticket, **MODEL)
            await self._comment(ticket.key, spec_comment(spec))
            await self._move(ticket.key, SPEC_REVIEW)
            await self._report("waiting for spec approval")
            seen = self._ready_signals
            reviewed = lambda seen=seen: self._approved or self._ready_signals > seen  # noqa: E731
            await self._wait(reviewed, "the spec was not reviewed")
            if self._approved:
                return spec
            ticket = await workflow.execute_activity_method(Activities.read_ticket, ticket.key, **QUICK)
        raise Stopped(f"spec not approved after {MAX_SPEC_DRAFTS} drafts")

    async def _write_tests(self, ticket: Ticket, spec: Spec) -> WrittenTests:
        await self._report("writing tests")
        branch = f"feature/{ticket.key}"
        return await workflow.execute_activity_method(
            Activities.write_tests, WriteTestsInput(ticket, spec, branch), **AGENT_RUN
        )

    async def _ci_until_green(self, ticket: Ticket, spec: Spec, tests: WrittenTests, implementation) -> None:
        """The repository's CI is the source of truth; its failures go back to
        the agent, which pushes a fix to the same PR, at most MAX_CI_FIXES times."""
        for attempt in range(1 + MAX_CI_FIXES):
            await self._report("waiting for CI")
            checks = await workflow.execute_activity_method(Activities.wait_for_checks, implementation, **CI_WAIT)
            if checks.passed:
                return
            if attempt == MAX_CI_FIXES:
                failures = "\n".join(f"- {failure}" for failure in checks.failures)
                raise Stopped(f"CI still failing after {MAX_CI_FIXES} fixes:\n{failures}")
            await self._comment(ticket.key, "CI failed; the agent is fixing it on the same pull request.")
            implementation, _ = await self._implement_until_green(ticket, spec, tests, checks.failures)

    async def _implement_until_green(
        self, ticket: Ticket, spec: Spec, tests: WrittenTests, feedback: list[str]
    ) -> tuple[Implementation, GateReport]:
        """Local gates first, cheap and deterministic; their failures go back
        to the agent, at most MAX_FIX_ATTEMPTS times."""
        for _ in range(1 + MAX_FIX_ATTEMPTS):
            await self._report("implementing")
            implementation = await workflow.execute_activity_method(
                Activities.implement, ImplementInput(ticket, spec, tests, feedback), **AGENT_RUN
            )
            await self._report("running gates")
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

    async def _report(self, stage: str) -> None:
        """Sets the stage and updates the progress comment on the ticket, so
        the person watching Jira sees which step runs without opening Temporal."""
        if stage == "implementing":
            self._attempts += 1
        self._stage = stage
        if stage in STEP_OF:
            self._reached = STEP_OF[stage]
        body = progress_comment(self._reached, stage, self._attempts)
        await workflow.execute_activity_method(Activities.update_progress, Comment(self._key, body), **QUICK)

    async def _comment(self, key: str, body: str) -> None:
        signed = f"**ticket-to-pr agent** · {self._stage}\n{body}"
        await workflow.execute_activity_method(Activities.comment, Comment(key, signed), **QUICK)

    async def _move(self, key: str, status: str) -> None:
        await workflow.execute_activity_method(Activities.move_ticket, Move(key, status), **QUICK)
