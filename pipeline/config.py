import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    temporal_address: str
    temporal_namespace: str
    task_queue: str
    adapters: str
    jira_webhook_secret: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            temporal_address=os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
            temporal_namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.environ.get("TASK_QUEUE", "tickets"),
            adapters=os.environ.get("ADAPTERS", "fake"),
            jira_webhook_secret=os.environ.get("JIRA_WEBHOOK_SECRET", ""),
        )


def workflow_id(key: str) -> str:
    """One workflow per ticket: a duplicate webhook can't start a second run."""
    return f"ticket-{key}"
