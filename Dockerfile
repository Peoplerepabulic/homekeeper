# HomeKeeper MCP server — Lambda Web Adapter image (for Phase 1 Lambda deploy).
#
# The AWS Lambda Web Adapter lets a regular HTTP server (FastMCP over
# streamable HTTP) run behind a Lambda Function URL with zero code changes:
# the adapter extension forwards Lambda events to the local web server.
FROM python:3.12-slim

# Lambda Web Adapter extension binary.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 \
    /lambda-adapter /opt/extensions/lambda-adapter

ENV PORT=8000 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/

# src/server.py serves streamable HTTP on $PORT; the adapter forwards
# Function URL traffic to it. Provisioned concurrency = 1 in Phase 1
# keeps p95 under 300 ms for the demo (single concurrent user).
CMD ["python", "src/server.py"]
