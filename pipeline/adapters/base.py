"""What the activities need from the outside world.

Each provider (Jira, GitHub, Claude Code...) implements one of these, so
switching Jira for Linear or GitHub for Bitbucket is a new adapter, not a
change to the workflow.
"""

from typing import Protocol

from pipeline.models import (
    CheckReport,
    GateReport,
    GatesInput,
    Implementation,
    ImplementInput,
    PullRequest,
    PullRequestInput,
    Readiness,
    Spec,
    Ticket,
    WriteTestsInput,
    WrittenTests,
)


class Tracker(Protocol):
    async def get_ticket(self, key: str) -> Ticket: ...
    async def comment(self, key: str, body: str) -> None: ...
    async def move(self, key: str, status: str) -> None: ...


class Agent(Protocol):
    async def check_readiness(self, ticket: Ticket) -> Readiness: ...
    async def draft_spec(self, ticket: Ticket) -> Spec: ...
    async def write_tests(self, request: WriteTestsInput) -> WrittenTests: ...
    async def implement(self, request: ImplementInput) -> Implementation: ...


class Gates(Protocol):
    async def run(self, request: GatesInput) -> GateReport: ...


class Repository(Protocol):
    async def open_pull_request(self, request: PullRequestInput) -> PullRequest: ...
    async def wait_for_checks(self, implementation: Implementation) -> CheckReport: ...
    async def hand_to_reviewer(self, pull_request: PullRequest) -> None: ...
