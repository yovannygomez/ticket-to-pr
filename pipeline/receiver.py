"""The webhook receiver: Jira status change -> one Temporal call -> 200.

No state and no business logic. It talks to the Temporal server, never to a
worker, and never waits for the work. If Temporal is unreachable the call
raises, FastAPI answers 500, and Jira retries the webhook.
"""

import hashlib
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from temporalio.client import Client

from pipeline.config import Settings, workflow_id
from pipeline.workflow import TicketWorkflow

READY_FOR_AGENT = "Ready for agent"
SPEC_APPROVED = "Spec approved"


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Jira signs the body with the webhook's secret: `sha256=<hex HMAC>`."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def new_status(event: dict) -> str | None:
    """The status the ticket moved to, from the changelog; None when the
    update changed something else (a comment, the description...)."""
    for item in (event.get("changelog") or {}).get("items", []):
        if item.get("field") == "status":
            return item.get("toString")
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    app.state.temporal = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/webhooks/jira")
async def jira_webhook(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    body = await request.body()
    if not valid_signature(settings.jira_webhook_secret, body, request.headers.get("x-hub-signature")):
        raise HTTPException(status_code=401)

    event = await request.json()
    key = event["issue"]["key"]
    status = new_status(event)
    temporal: Client = request.app.state.temporal

    if status == READY_FOR_AGENT:
        # Signal-with-start: starts the run the first time; afterwards it
        # signals the running one (questions answered, spec changes asked).
        await temporal.start_workflow(
            TicketWorkflow.run,
            key,
            id=workflow_id(key),
            task_queue=settings.task_queue,
            start_signal="ready_for_agent",
        )
    elif status == SPEC_APPROVED:
        await temporal.get_workflow_handle(workflow_id(key)).signal(TicketWorkflow.spec_approved)
    # Any other change: nothing to do, still 200 so Jira doesn't retry.

    return {"ok": True}
