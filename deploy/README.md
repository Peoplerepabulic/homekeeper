# AWS Deploy I — DynamoDB table + Lambda MCP server (2026-10-04)

Region: `us-east-1`. Profile: `alexa-ai-user` (IAM user `alexa-ai-tools`,
account 955857822289). All commands below need
`AWS_PROFILE=alexa-ai-user AWS_REGION=us-east-1`.

## What was created

| Resource | Name / value |
|---|---|
| DynamoDB table | `homekeeper` — pk `pk` (S), sk `sk` (S), `PAY_PER_REQUEST`, no GSIs |
| Lambda function | `homekeeper-mcp` — python3.12, 512 MB, 30 s timeout |
| Lambda version | `2` (v1 = first broken build, v2 = working) |
| Function URL | `https://ikwwry36o6eftmfibyzymv5thy0kplnb.lambda-url.us-east-1.on.aws/` (auth `NONE`) |
| IAM role | `homekeeper-lambda-role` — `AWSLambdaBasicExecutionRole` + inline DynamoDB policy scoped to the `homekeeper` table only |
| Env vars | `HOMEKEEPER_TABLE=homekeeper`, `DEMO_CLOCK=2026-10-04` (`AWS_REGION` is reserved by Lambda — do NOT set it; the runtime provides it) |
| Seed | 36 items written via `python src/seed.py` (8 appliances, 24 tasks, 1 recall alert, 1 briefing, 1 profile, 1 event) |

## Packaging (zip route — no docker on the build VM)

```
pip install --target package/ "fastmcp>=4" mangum
cp src/*.py src/catalog.json lambda_handler.py package/
cd package && zip -r ../homekeeper-lambda.zip .   # DO NOT exclude *.dist-info
```

`lambda_handler.py` wraps `mcp.http_app(path="/mcp", stateless_http=True,
host_origin_protection=False)` with `Mangum(app, lifespan="auto")`.
Handler: `lambda_handler.handler`. Zip is ~17 MB.

## Gotchas hit (also in FRICTION_LOG.md)

1. **`*.dist-info` must stay in the zip.** Excluding it breaks the import:
   `Runtime.ImportModuleError: No package metadata was found for fastmcp`.
2. **FastMCP 4.x DNS-rebinding guard 403s the Function URL hostname.**
   Fix: `host_origin_protection=False` in `http_app()` (safe here — public
   HTTPS endpoint called server-to-server, not a localhost server).
3. **Mangum needs `lifespan="auto"`, not `"off"`.** FastMCP 4.x creates its
   StreamableHTTP session manager inside the ASGI lifespan; with `"off"`
   every request fails with `RuntimeError: Task group is not initialized`.
4. **Transient `aws` CLI timeouts** on this VM (proxy). Table/role creation
   calls timed out client-side but succeeded server-side — always
   re-check with describe/get before retrying blindly.
5. **The VM proxy 403s `*.lambda-url.*` hosts.** `curl` against the Function
   URL from here returns proxy 403s — a red herring, not an AWS problem.
   Verify via `aws lambda invoke` with synthetic Function URL (payload 2.0)
   events instead: see `e2e_lambda.py`. (Direct egress fails SSL — do not
   unset the proxy.)

## Not done / known limitations

- **Provisioned concurrency = 1 is BLOCKED**: new-account Lambda concurrency
  limit is 10, and allocating any provisioned concurrency would push
  unreserved below the 10 minimum. Raising it needs an AWS Support case
  (the Service Quotas API refuses: default quota is 1000). Mitigation for
  the demo: warm the function with one call ~1 min before recording; cold
  start is ~2.3 s, warm calls are 13–76 ms.
- **Warm latency** (from CloudWatch REPORT): 13–76 ms per tool call —
  comfortably under the 500 ms Alexa+ budget. Cold start ~2.3 s.

## Re-verify after any code change

```
python3 deploy/e2e_lambda.py   # initialize -> tools/list -> tools/call (via lambda:Invoke)
```
