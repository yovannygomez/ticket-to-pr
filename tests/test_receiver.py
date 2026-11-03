"""The receiver maps a Jira status change to exactly one Temporal call."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from pipeline import receiver
from pipeline.config import Settings

SECRET = "test-secret"


class FakeHandle:
    def __init__(self, calls: list):
        self.calls = calls

    async def signal(self, signal):
        self.calls.append(("signal", signal.__name__))


class FakeTemporal:
    def __init__(self):
        self.calls: list = []

    async def start_workflow(self, run, key, *, id, task_queue, start_signal):
        self.calls.append(("signal-with-start", id, start_signal))

    def get_workflow_handle(self, workflow_id):
        self.calls.append(("handle", workflow_id))
        return FakeHandle(self.calls)


@pytest.fixture
def client():
    app = receiver.FastAPI()
    app.router.routes.extend(receiver.app.router.routes)
    app.state.settings = Settings("unused", "default", "tickets", "fake", SECRET)
    app.state.temporal = FakeTemporal()
    return TestClient(app)


def post(client: TestClient, event: dict, secret: str = SECRET):
    body = json.dumps(event).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/jira", content=body, headers={"x-hub-signature": signature})


def status_change(to: str) -> dict:
    return {"issue": {"key": "LIST-7"}, "changelog": {"items": [{"field": "status", "toString": to}]}}


def test_ready_for_agent_signals_with_start(client):
    assert post(client, status_change("Ready for agent")).status_code == 200
    assert client.app.state.temporal.calls == [("signal-with-start", "ticket-LIST-7", "ready_for_agent")]


def test_spec_approved_signals_the_running_workflow(client):
    assert post(client, status_change("Spec approved")).status_code == 200
    assert client.app.state.temporal.calls == [("handle", "ticket-LIST-7"), ("signal", "spec_approved")]


def test_other_changes_do_nothing(client):
    edit = {"issue": {"key": "LIST-7"}, "changelog": {"items": [{"field": "description", "toString": "x"}]}}
    assert post(client, edit).status_code == 200
    assert post(client, status_change("Done")).status_code == 200
    assert client.app.state.temporal.calls == []


def test_bad_signature_is_rejected(client):
    assert post(client, status_change("Ready for agent"), secret="wrong").status_code == 401
    assert client.app.state.temporal.calls == []
