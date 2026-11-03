"""The worker: polls the task queue and runs the workflow and activity code.

It only calls out (to the Temporal server, the tracker, the repository and
the model), so it needs no inbound ports.
"""

import asyncio
import os
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from pipeline.activities import Activities
from pipeline.config import Settings
from pipeline.workflow import TicketWorkflow


def build_activities(settings: Settings) -> Activities:
    if settings.adapters == "fake":
        from pipeline.adapters.fake import FakeAgent, FakeGates, FakeRepository, FakeTracker

        return Activities(FakeTracker(), FakeAgent(), FakeGates(), FakeRepository())
    if settings.adapters == "real":
        from pipeline.adapters.claude import ClaudeAgent
        from pipeline.adapters.github import GitHubRepository, Workspace
        from pipeline.adapters.jira import JiraTracker
        from pipeline.adapters.sandbox import Sandbox, SandboxGates

        env = os.environ
        workspace = Workspace(Path(env["WORKSPACES_DIR"]).expanduser(), env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"])
        sandbox = Sandbox(env.get("SANDBOX_IMAGE", "ticket-to-pr-sandbox:dev"), env["ANTHROPIC_API_KEY"])
        return Activities(
            JiraTracker(env["JIRA_BASE_URL"], env["JIRA_EMAIL"], env["JIRA_API_TOKEN"]),
            ClaudeAgent(
                env["ANTHROPIC_API_KEY"],
                workspace,
                sandbox,
                fast_model=env.get("FAST_MODEL", "claude-haiku-5-5"),
                strong_model=env.get("STRONG_MODEL", "claude-sonnet-5-5"),
            ),
            SandboxGates(workspace, sandbox),
            GitHubRepository(env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"], env["REVIEWER"]),
        )
    raise ValueError(f"unknown ADAPTERS={settings.adapters!r}")


async def main() -> None:
    settings = Settings.from_env()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[TicketWorkflow],
        activities=build_activities(settings).all(),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
