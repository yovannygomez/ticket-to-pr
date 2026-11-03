"""In-memory adapters: the whole workflow runs without Jira, GitHub or a model.

Used by the tests and by `ADAPTERS=fake` for a local dry run. Each fake
records what it was asked to do, so a test can assert on it.
"""

from dataclasses import dataclass, field

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


@dataclass
class FakeTracker:
    tickets: dict[str, Ticket] = field(default_factory=dict)
    comments: list[tuple[str, str]] = field(default_factory=list)
    moves: list[tuple[str, str]] = field(default_factory=list)

    async def get_ticket(self, key: str) -> Ticket:
        if key not in self.tickets:
            self.tickets[key] = Ticket(
                key=key,
                title="Filter listings by price",
                description="Buyers want to narrow listings to a price range.",
                acceptance_criteria=[
                    "GET /listings accepts min_price and max_price",
                    "min_price greater than max_price returns 422",
                ],
            )
        return self.tickets[key]

    async def comment(self, key: str, body: str) -> None:
        self.comments.append((key, body))

    async def move(self, key: str, status: str) -> None:
        self.moves.append((key, status))


@dataclass
class FakeAgent:
    """Ready when the ticket has acceptance criteria; the first
    `failing_attempts` implementations fail the gates."""

    failing_attempts: int = 0
    implementations: list[ImplementInput] = field(default_factory=list)

    async def check_readiness(self, ticket: Ticket) -> Readiness:
        if ticket.acceptance_criteria:
            return Readiness(ready=True)
        return Readiness(ready=False, questions=["What are the acceptance criteria?"])

    async def draft_spec(self, ticket: Ticket) -> Spec:
        notes = f"\n\nReviewer notes: {ticket.comments[-1]}" if ticket.comments else ""
        return Spec(
            markdown=f"# {ticket.key}: {ticket.title}\n\n{ticket.description}{notes}",
            acceptance_tests=list(ticket.acceptance_criteria),
        )

    async def write_tests(self, request: WriteTestsInput) -> WrittenTests:
        return WrittenTests(branch=request.branch, commit_sha="tests000", files=["tests/test_filters.py"])

    async def implement(self, request: ImplementInput) -> Implementation:
        self.implementations.append(request)
        attempt = len(self.implementations)
        return Implementation(
            branch=request.tests.branch,
            commit_sha=f"impl{attempt:03}",
            summary="broken" if attempt <= self.failing_attempts else "ok",
        )


@dataclass
class FakeGates:
    async def run(self, request: GatesInput) -> GateReport:
        if request.implementation.summary == "broken":
            return GateReport(passed=False, failures=["pytest: test_min_greater_than_max failed"])
        return GateReport(passed=True)


@dataclass
class FakeRepository:
    """CI fails on the first `failing_ci_runs` commits it sees."""

    failing_ci_runs: int = 0
    opened: list[PullRequestInput] = field(default_factory=list)
    ci_runs: list[str] = field(default_factory=list)
    handed: list[int] = field(default_factory=list)

    async def wait_for_checks(self, implementation: Implementation) -> CheckReport:
        self.ci_runs.append(implementation.commit_sha)
        if len(self.ci_runs) <= self.failing_ci_runs:
            return CheckReport(passed=False, failures=["ci / test: ruff format --check would reformat app/main.py"])
        return CheckReport(passed=True)

    async def hand_to_reviewer(self, pull_request: PullRequest) -> None:
        self.handed.append(pull_request.number)

    async def open_pull_request(self, request: PullRequestInput) -> PullRequest:
        self.opened.append(request)
        number = len(self.opened)
        return PullRequest(number=number, url=f"https://github.com/example/listings-api/pull/{number}")
