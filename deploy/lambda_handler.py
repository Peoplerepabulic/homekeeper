"""AWS Lambda entry point for the HomeKeeper MCP server (hot path).

Wraps the FastMCP streamable-HTTP Starlette app with Mangum so it runs
behind a Lambda Function URL. Stateless mode keeps every request
independent (no session affinity), which is what Lambda needs.

Packaging layout (zip root):
    lambda_handler.py   <- this file (Lambda handler: lambda_handler.handler)
    server.py, store.py, db.py, scheduling.py, seed.py
    catalog.json
    <fastmcp, mangum and their deps>
"""

from mangum import Mangum

from server import mcp

# Starlette ASGI app serving MCP streamable HTTP at /mcp.
# host_origin_protection=False: the DNS-rebinding guard is meant for
# localhost-bound servers; this is a public HTTPS endpoint called
# server-to-server by Alexa+, so the Host allowlist would only 403
# legitimate callers (e.g. the Lambda Function URL hostname).
_asgi_app = mcp.http_app(
    path="/mcp", stateless_http=True, host_origin_protection=False
)

# lifespan="auto": run the ASGI lifespan once per cold start. FastMCP 4.x
# requires it -- the StreamableHTTP session manager (task group) is only
# created inside the lifespan; with lifespan="off" every request fails with
# "Task group is not initialized".
handler = Mangum(_asgi_app, lifespan="auto")
