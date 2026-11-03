"""Data passed between the workflow and its activities.

Plain dataclasses, so Temporal's default converter stores them in the
history as JSON and the replay gets the same values back.
"""

from dataclasses import dataclass, field


@dataclass
class Ticket:
    key: str
    title: str
    description: str
    acceptance_criteria: list[str]
    comments: list[str] = field(default_factory=list)


@dataclass
class Readiness:
    ready: bool
    questions: list[str] = field(default_factory=list)


@dataclass
class Spec:
    markdown: str
    acceptance_tests: list[str]


@dataclass
class WrittenTests:
    """The approved tests, frozen at `commit_sha` before implementation starts."""

    branch: str
    commit_sha: str
    files: list[str]


@dataclass
class Implementation:
    branch: str
    commit_sha: str
    summary: str


@dataclass
class GateReport:
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class PullRequest:
    number: int
    url: str


@dataclass
class Comment:
    key: str
    body: str


@dataclass
class Move:
    key: str
    status: str


@dataclass
class WriteTestsInput:
    ticket: Ticket
    spec: Spec
    branch: str


@dataclass
class ImplementInput:
    ticket: Ticket
    spec: Spec
    tests: WrittenTests
    feedback: list[str] = field(default_factory=list)


@dataclass
class GatesInput:
    implementation: Implementation
    tests: WrittenTests


@dataclass
class PullRequestInput:
    ticket: Ticket
    spec: Spec
    implementation: Implementation
    report: GateReport
