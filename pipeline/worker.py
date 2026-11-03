"""The worker: polls the task queue and runs the workflow and activity code.

It only calls out (to the Temporal server, the tracker, the repository and
the model), so it needs no inbound ports.
"""

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from pipeline.activities import Activities
from pipeline.config import Settings
from pipeline.workflow import TicketWorkflow


def build_activities(settings: Settings) -> Activities:
    if settings.adapters == "fake":
        from pipeline.adapters.fake import FakeAgent, FakeGates, FakeRepository, FakeTracker

        return Activities(FakeTracker(), FakeAgent(), FakeGates(), FakeRepository())
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
