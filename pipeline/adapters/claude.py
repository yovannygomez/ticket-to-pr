"""The agent: Claude for the reasoning steps, Claude Code for the code.

Readiness and the spec are single API calls with a forced tool, so the
answer comes back as validated JSON. Tests and implementation are Claude
Code runs in the sandbox, each with its own permissions: the tests step
can't touch the app, the implement step can't touch the tests.
"""

from pathlib import Path

from anthropic import AsyncAnthropic

from pipeline import tracing
from pipeline.adapters.github import Workspace, git
from pipeline.adapters.sandbox import Sandbox
from pipeline.models import Implementation, ImplementInput, Readiness, Spec, Ticket, WriteTestsInput, WrittenTests

TESTS_COMMIT = "tests from acceptance criteria"

READINESS_TOOL = {
    "name": "readiness",
    "description": "Whether the ticket can be turned into a spec without guessing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ready": {"type": "boolean"},
            "questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ready", "questions"],
    },
}

SPEC_TOOL = {
    "name": "spec",
    "description": "The spec for the ticket.",
    "input_schema": {
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "Sections: Goal, Behavior, Edge cases, Out of scope, Acceptance tests.",
            },
            "acceptance_tests": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Each one a concrete, testable case: input and expected result.",
            },
        },
        "required": ["markdown", "acceptance_tests"],
    },
}


def describe(ticket: Ticket) -> str:
    comments = "\n".join(f"- {comment}" for comment in ticket.comments) or "(none)"
    return f"Ticket {ticket.key}: {ticket.title}\n\n{ticket.description}\n\nComments, oldest first:\n{comments}"


class ClaudeAgent:
    def __init__(self, api_key: str, workspace: Workspace, sandbox: Sandbox, fast_model: str, strong_model: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.workspace = workspace
        self.sandbox = sandbox
        self.fast_model = fast_model
        self.strong_model = strong_model

    async def _tool_call(self, ticket: str, name: str, model: str, tool: dict, prompt: str) -> dict:
        with tracing.generation(ticket, name, model, prompt) as observation:
            response = await self.client.messages.create(
                model=model,
                max_tokens=4000,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": prompt}],
            )
            answer = next(block.input for block in response.content if block.type == "tool_use")
            observation.update(
                output=answer,
                usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
            )
        return answer

    async def check_readiness(self, ticket: Ticket) -> Readiness:
        prompt = (
            "You review tickets before an engineer writes a spec. A ticket is ready when its acceptance "
            "criteria are specific enough to test without guessing. If it isn't, ask the fewest questions "
            f"that would make it ready.\n\n{describe(ticket)}"
        )
        answer = await self._tool_call(ticket.key, "check_readiness", self.fast_model, READINESS_TOOL, prompt)
        return Readiness(ready=answer["ready"], questions=answer["questions"])

    async def draft_spec(self, ticket: Ticket) -> Spec:
        prompt = (
            "Write the spec for this ticket in a small FastAPI service (listings with title, category, "
            "price_cents as an integer, city). Be concrete and short. If the comments request changes to an "
            "earlier spec, apply them. List what is out of scope.\n\n" + describe(ticket)
        )
        answer = await self._tool_call(ticket.key, "draft_spec", self.strong_model, SPEC_TOOL, prompt)
        return Spec(markdown=answer["markdown"], acceptance_tests=answer["acceptance_tests"])

    async def write_tests(self, request: WriteTestsInput) -> WrittenTests:
        path = await self.workspace.prepare(request.ticket.key, request.branch)
        # Idempotent: a retry after the tests were pushed reuses that commit.
        done = await git(path, "log", "--format=%H", "--grep", TESTS_COMMIT, "origin/main..HEAD")
        if not done:
            tests = "\n".join(f"- {test}" for test in request.spec.acceptance_tests)
            prompt = (
                f"Write pytest tests for ticket {request.ticket.key} in a new file under tests/, following the "
                "style of the existing tests and the `client` fixture in tests/conftest.py. One test per "
                f"acceptance test below. Do not change any existing file.\n\nAcceptance tests:\n{tests}\n\n"
                "Run `python -m pytest -q` and confirm the new tests fail because the feature is missing, "
                "not because of a mistake in the tests."
            )
            await self.sandbox.claude(request.ticket.key, "tests", path, prompt, "write-tests.json", self.strong_model)
            await self.workspace.commit_and_push(path, request.branch, f"{request.ticket.key}: {TESTS_COMMIT}", "tests")
            done = await git(path, "rev-parse", "HEAD")
        sha = done.splitlines()[0]
        files = await git(path, "diff", "--name-only", "--diff-filter=A", "origin/main", sha, "--", "tests/")
        return WrittenTests(branch=request.branch, commit_sha=sha, files=files.split())

    async def implement(self, request: ImplementInput) -> Implementation:
        path: Path = await self.workspace.prepare(request.ticket.key, request.tests.branch)
        feedback = (
            "\n\nThe previous attempt failed these gates; fix them:\n" + "\n".join(request.feedback)
            if request.feedback
            else ""
        )
        prompt = (
            f"Implement ticket {request.ticket.key} in app/ so that every test passes. You cannot edit tests.\n\n"
            f"Spec:\n{request.spec.markdown}{feedback}\n\n"
            "Run `python -m pytest -q` and `ruff check .` until both pass. End with a two-sentence summary."
        )
        summary = await self.sandbox.claude(
            request.ticket.key, "implement", path, prompt, "implement.json", self.strong_model
        )
        sha = await self.workspace.commit_and_push(
            path, request.tests.branch, f"{request.ticket.key}: {request.ticket.title}"
        )
        return Implementation(branch=request.tests.branch, commit_sha=sha, summary=summary.strip())
