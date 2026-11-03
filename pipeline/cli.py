"""Drive a run by hand, without Jira: what the webhook would do.

python -m pipeline.cli start LIST-7     # = ticket moved to "Ready for agent"
python -m pipeline.cli approve LIST-7   # = ticket moved to "Spec approved"
python -m pipeline.cli stage LIST-7
"""

import asyncio
import sys

from temporalio.client import Client

from pipeline.config import Settings, workflow_id
from pipeline.workflow import TicketWorkflow


async def main(command: str, key: str) -> None:
    settings = Settings.from_env()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = client.get_workflow_handle(workflow_id(key))
    if command == "start":
        await client.start_workflow(
            TicketWorkflow.run, key, id=workflow_id(key), task_queue=settings.task_queue, start_signal="ready_for_agent"
        )
    elif command == "approve":
        await handle.signal(TicketWorkflow.spec_approved)
    elif command == "stage":
        print(await handle.query(TicketWorkflow.stage))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
