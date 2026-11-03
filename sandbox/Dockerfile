# The sandbox: one container per agent run, destroyed when it ends.
# It gets the ticket's working copy and a model key, nothing else: no
# repository token (the worker pushes), no cloud credentials.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.317 \
    && useradd --create-home --uid 1000 agent

COPY settings/ /etc/ticket-to-pr/
COPY gates.py /opt/ticket-to-pr/gates.py

USER agent
RUN git config --global --add safe.directory /work
ENV PATH=/home/agent/.local/bin:$PATH
WORKDIR /work
