"""LangFuse: what happened inside each model call, grouped per ticket.

Used only from activities, never from workflow code: a workflow is replayed
after a crash, and tracing there would log the same calls twice. Every
observation carries the ticket key as its session, so LangFuse shows one
session per ticket with every model call, its tokens and its cost.

Without LangFuse keys in the environment this is a no-op.
"""

import os
from contextlib import contextmanager

_client = None
if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
    from langfuse import get_client, propagate_attributes

    _client = get_client()


class _NoOp:
    def update(self, **_) -> None:
        pass


@contextmanager
def generation(ticket: str, name: str, model: str, prompt: str):
    """One model call. The caller reports output, usage and cost with
    `.update(output=..., usage_details={...}, cost_details={...})`."""
    if _client is None:
        yield _NoOp()
        return
    with propagate_attributes(session_id=ticket, tags=["ticket-to-pr"]):
        with _client.start_as_current_observation(as_type="generation", name=name, model=model, input=prompt) as obs:
            yield obs
    _client.flush()
