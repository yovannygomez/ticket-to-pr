"""The workflow end to end against fakes, on Temporal's time-skipping test
server: a seven-day wait for a human finishes in milliseconds."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from pipeline.activities import Activities
from pipeline.adapters.fake import FakeAgent, FakeGates, FakeRepository, FakeTracker
from pipeline.models import Ticket
from pipeline.workflow import AGENT_WORKING, IN_REVIEW, NEEDS_INFO, SPEC_REVIEW, TicketWorkflow

KEY = "LIST-7"


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


class Pipeline:
    def __init__(
        self,
        client: Client,
        agent: FakeAgent | None = None,
        ticket: Ticket | None = None,
        repository: FakeRepository | None = None,
    ):
        self.client = client
        self.queue = f"tickets-{uuid.uuid4()}"
        self.tracker = FakeTracker(tickets={KEY: ticket} if ticket else {})
        self.agent = agent or FakeAgent()
        self.repository = repository or FakeRepository()
        self.activities = Activities(self.tracker, self.agent, FakeGates(), self.repository)

    def worker(self) -> Worker:
        return Worker(self.client, task_queue=self.queue, workflows=[TicketWorkflow], activities=self.activities.all())

    async def start(self) -> WorkflowHandle:
        return await self.client.start_workflow(
            TicketWorkflow.run,
            KEY,
            id=f"ticket-{KEY}-{uuid.uuid4()}",
            task_queue=self.queue,
            start_signal="ready_for_agent",
        )

    async def wait_for_stage(self, handle: WorkflowHandle, stage: str) -> None:
        for _ in range(200):
            if await handle.query(TicketWorkflow.stage) == stage:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"never reached stage {stage!r}")

    def statuses(self) -> list[str]:
        return [status for _, status in self.tracker.moves]


async def test_happy_path_opens_a_pull_request(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client)
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        url = await handle.result()

    progress = pipeline.tracker.progress[KEY]
    assert progress.count("✅") == 8 and "⏳" not in progress
    assert url.endswith("/pull/1")
    assert pipeline.statuses() == [SPEC_REVIEW, AGENT_WORKING, IN_REVIEW]
    assert pipeline.repository.handed == [1]
    assert pipeline.repository.opened[0].report.passed


async def test_ticket_without_criteria_asks_before_writing_a_spec(env: WorkflowEnvironment):
    ticket = Ticket(key=KEY, title="Make search better", description="It feels slow.", acceptance_criteria=[])
    pipeline = Pipeline(env.client, ticket=ticket)
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for info")
        assert "acceptance criteria" in pipeline.tracker.comments[-1][1]
        assert "move the ticket back to **Ready for agent**" in pipeline.tracker.comments[-1][1]

        ticket.acceptance_criteria.append("Search answers in under 300 ms at p95")
        await handle.signal(TicketWorkflow.ready_for_agent)
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        await handle.result()

    assert pipeline.statuses()[:2] == [NEEDS_INFO, SPEC_REVIEW]


async def test_changes_requested_redrafts_the_spec(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client)
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")

        pipeline.tracker.tickets[KEY].comments.append("Prices are in cents, not dollars.")
        await handle.signal(TicketWorkflow.ready_for_agent)
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        await handle.result()

    specs = [body for _, body in pipeline.tracker.comments if "## Spec ready for review" in body]
    assert len(specs) == 2
    assert "Prices are in cents" in specs[1]


async def test_gate_failures_go_back_to_the_agent(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client, agent=FakeAgent(failing_attempts=1))
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        await handle.result()

    first, second = pipeline.agent.implementations
    assert first.feedback == []
    assert second.feedback == ["pytest: test_min_greater_than_max failed"]


async def test_gives_up_when_the_gates_keep_failing(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client, agent=FakeAgent(failing_attempts=99))
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        result = await handle.result()

    assert result.startswith("stopped: gates still failing")
    assert "⛔ Local gates passed" in pipeline.tracker.progress[KEY]
    assert len(pipeline.agent.implementations) == 3
    assert pipeline.repository.opened == []


async def test_unreviewed_spec_stops_after_seven_days(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client)
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await env.sleep(timedelta(days=8))
        result = await handle.result()

    assert result == "stopped: the spec was not reviewed within 7 days"
    assert "Agent stopped: the spec was not reviewed" in pipeline.tracker.comments[-1][1]


async def test_ci_failure_goes_back_to_the_agent_on_the_same_pull_request(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client, repository=FakeRepository(failing_ci_runs=1))
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        await handle.result()

    assert len(pipeline.repository.opened) == 1
    assert pipeline.repository.ci_runs == ["impl001", "impl002"]
    assert pipeline.agent.implementations[1].feedback == ["ci / test: ruff format --check would reformat app/main.py"]
    assert pipeline.repository.handed == [1]


async def test_never_hands_over_a_pull_request_with_red_ci(env: WorkflowEnvironment):
    pipeline = Pipeline(env.client, repository=FakeRepository(failing_ci_runs=99))
    async with pipeline.worker():
        handle = await pipeline.start()
        await pipeline.wait_for_stage(handle, "waiting for spec approval")
        await handle.signal(TicketWorkflow.spec_approved)
        result = await handle.result()

    assert result.startswith("stopped: CI still failing")
    assert pipeline.repository.handed == []
    assert IN_REVIEW not in pipeline.statuses()
