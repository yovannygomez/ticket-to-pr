"""Activities: every step with side effects.

Thin on purpose: each one delegates to an adapter, so the same workflow runs
against fakes in the tests and against Jira, Claude Code and GitHub for real.
They must be idempotent, because Temporal retries them after a timeout or a
dead worker.
"""

import asyncio
from collections.abc import Awaitable

from temporalio import activity

from pipeline.adapters.base import Agent, Gates, Repository, Tracker
from pipeline.models import (
    CheckReport,
    Comment,
    GateReport,
    GatesInput,
    Implementation,
    ImplementInput,
    Move,
    PullRequest,
    PullRequestInput,
    Readiness,
    Spec,
    Ticket,
    WriteTestsInput,
    WrittenTests,
)

HEARTBEAT_SECONDS = 20


async def _with_heartbeat[T](work: Awaitable[T]) -> T:
    """Runs `work` while telling the server the worker is alive, so a dead
    worker is noticed within the heartbeat timeout, not after hours."""
    task = asyncio.ensure_future(work)
    while True:
        done, _ = await asyncio.wait({task}, timeout=HEARTBEAT_SECONDS)
        if done:
            return task.result()
        activity.heartbeat()


class Activities:
    def __init__(self, tracker: Tracker, agent: Agent, gates: Gates, repository: Repository):
        self.tracker = tracker
        self.agent = agent
        self.gates = gates
        self.repository = repository

    def all(self) -> list:
        return [
            self.read_ticket,
            self.comment,
            self.move_ticket,
            self.check_readiness,
            self.draft_spec,
            self.write_tests,
            self.implement,
            self.run_gates,
            self.open_pull_request,
            self.wait_for_checks,
            self.hand_to_reviewer,
        ]

    @activity.defn
    async def read_ticket(self, key: str) -> Ticket:
        return await self.tracker.get_ticket(key)

    @activity.defn
    async def comment(self, request: Comment) -> None:
        await self.tracker.comment(request.key, request.body)

    @activity.defn
    async def move_ticket(self, request: Move) -> None:
        await self.tracker.move(request.key, request.status)

    @activity.defn
    async def check_readiness(self, ticket: Ticket) -> Readiness:
        return await self.agent.check_readiness(ticket)

    @activity.defn
    async def draft_spec(self, ticket: Ticket) -> Spec:
        return await self.agent.draft_spec(ticket)

    @activity.defn
    async def write_tests(self, request: WriteTestsInput) -> WrittenTests:
        return await _with_heartbeat(self.agent.write_tests(request))

    @activity.defn
    async def implement(self, request: ImplementInput) -> Implementation:
        return await _with_heartbeat(self.agent.implement(request))

    @activity.defn
    async def run_gates(self, request: GatesInput) -> GateReport:
        return await _with_heartbeat(self.gates.run(request))

    @activity.defn
    async def open_pull_request(self, request: PullRequestInput) -> PullRequest:
        return await self.repository.open_pull_request(request)

    @activity.defn
    async def wait_for_checks(self, implementation: Implementation) -> CheckReport:
        return await _with_heartbeat(self.repository.wait_for_checks(implementation))

    @activity.defn
    async def hand_to_reviewer(self, pull_request: PullRequest) -> None:
        await self.repository.hand_to_reviewer(pull_request)
