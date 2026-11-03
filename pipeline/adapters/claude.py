"""The agent: Claude for the reasoning steps, Claude Code for the code.

Readiness and the spec are single API calls with structured output, so the
answer comes back as JSON that matches a schema. Tests and implementation are Claude
Code runs in the sandbox, each with its own permissions: the tests step
can't touch the app, the implement step can't touch the tests.
"""

import json
from pathlib import Path

from anthropic import AsyncAnthropic

from pipeline import tracing
from pipeline.adapters.github import Workspace, git
from pipeline.adapters.sandbox import Sandbox
from pipeline.models import Implementation, ImplementInput, Readiness, Spec, Ticket, WriteTestsInput, WrittenTests

TESTS_COMMIT = "tests from acceptance criteria"

READINESS_SCHEMA = {
    "type": "object",
    "properties": {
        "ready": {"type": "boolean"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ready", "questions"],
    "additionalProperties": False,
}

SPEC_SCHEMA = {
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
    "additionalProperties": False,
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

    async def _structured(self, ticket: str, name: str, model: str, schema: dict, prompt: str) -> dict:
        with tracing.generation(ticket, name, model, prompt) as observation:
            response = await self.client.messages.create(
                model=model,
                max_tokens=8000,
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}],
            )
            answer = json.loads(next(block.text for block in response.content if block.type == "text"))
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
        answer = await self._structured(ticket.key, "check_readiness", self.fast_model, READINESS_SCHEMA, prompt)
        return Readiness(ready=answer["ready"], questions=answer["questions"])

    async def _code_context(self, key: str) -> str:
        """The repository's current code, so the spec refers to real names and data."""
        path = await self.workspace.prepare(key, f"feature/{key}")
        files = sorted(path.glob("app/*.py")) + [path / "tests/conftest.py"]
        return "\n\n".join(f"--- {file.relative_to(path)}\n{file.read_text()}" for file in files if file.exists())

    async def draft_spec(self, ticket: Ticket) -> Spec:
        prompt = (
            "Write the spec for this ticket. Be concrete and short, use the names and seed data in the code "
            "below, and never invent records that aren't there. If the comments answer questions or request "
            "changes to an earlier spec, apply them. List what is out of scope.\n\n"
            f"{describe(ticket)}\n\nCurrent code on main:\n{await self._code_context(ticket.key)}"
        )
        answer = await self._structured(ticket.key, "draft_spec", self.strong_model, SPEC_SCHEMA, prompt)
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
