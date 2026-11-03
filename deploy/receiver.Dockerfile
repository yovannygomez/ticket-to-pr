# The webhook receiver on Lambda: the same FastAPI app, run unchanged by the
# Lambda Web Adapter extension. Build from the repository root:
#   docker build -f deploy/receiver.Dockerfile -t <ecr>/ticket-to-pr/receiver .
FROM public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 AS adapter

FROM python:3.12-slim
COPY --from=adapter /lambda-adapter /opt/extensions/lambda-adapter
WORKDIR /app
COPY pyproject.toml ./
COPY pipeline/ pipeline/
RUN pip install --no-cache-dir .
CMD ["uvicorn", "pipeline.receiver:app", "--host", "0.0.0.0", "--port", "8000"]
