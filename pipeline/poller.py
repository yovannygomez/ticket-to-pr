"""Local stand-in for the Jira webhook: Jira can't reach a laptop, so this
polls the project's statuses and sends the same Temporal calls the receiver
would. On AWS the webhook replaces it.

Statuses seen at startup are only recorded, never acted on, so restarting
the poller doesn't replay old moves.
"""

import asyncio
import os

import httpx
from temporalio.client import Client

from pipeline.config import Settings, workflow_id
from pipeline.receiver import READY_FOR_AGENT, SPEC_APPROVED
from pipeline.workflow import TicketWorkflow

POLL_SECONDS = 10


async def statuses(http: httpx.AsyncClient, project: str) -> dict[str, str]:
    response = await http.get(
        "/search/jql", params={"jql": f"project = {project}", "fields": "status", "maxResults": 100}
    )
    return {issue["key"]: issue["fields"]["status"]["name"] for issue in response.raise_for_status().json()["issues"]}


async def main() -> None:
    settings = Settings.from_env()
    temporal = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    env = os.environ
    http = httpx.AsyncClient(
        base_url=f"{env['JIRA_BASE_URL']}/rest/api/3", auth=(env["JIRA_EMAIL"], env["JIRA_API_TOKEN"]), timeout=20
    )
    project = env.get("JIRA_PROJECT", "LIST")
    seen = await statuses(http, project)
    print(f"watching {project}: {len(seen)} tickets, every {POLL_SECONDS}s", flush=True)
    while True:
        await asyncio.sleep(POLL_SECONDS)
        current = await statuses(http, project)
        for key, status in current.items():
            if seen.get(key) == status:
                continue
            if status == READY_FOR_AGENT:
                await temporal.start_workflow(
                    TicketWorkflow.run, key, id=workflow_id(key), task_queue=settings.task_queue,
                    start_signal="ready_for_agent",
                )  # fmt: skip
                print(f"{key} -> {status}: signal-with-start ready_for_agent", flush=True)
            elif status == SPEC_APPROVED:
                await temporal.get_workflow_handle(workflow_id(key)).signal(TicketWorkflow.spec_approved)
                print(f"{key} -> {status}: signal spec_approved", flush=True)
        seen = current


if __name__ == "__main__":
    asyncio.run(main())
