# The worker on ECS. Build from the repository root:
#   docker build -f deploy/worker.Dockerfile -t <ecr>/ticket-to-pr/worker .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 worker
WORKDIR /app
COPY pyproject.toml ./
COPY pipeline/ pipeline/
RUN pip install --no-cache-dir .
USER worker
CMD ["python", "-m", "pipeline.worker"]
