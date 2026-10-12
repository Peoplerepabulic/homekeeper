"""HomeKeeper MCP server (hot path).

Exposes 7 coarse-grained, intent-based tools to Alexa+. Every tool is a
database read/write plus static-catalog lookup -- no LLM calls on this path,
keeping latency far under the Alexa+ 500 ms round-trip limit.

Tool implementations live in tools.py (shared with the chat Lambda); business
logic lives in store.py. This module only registers them with FastMCP.
"""

from fastmcp import FastMCP

import tools

mcp = FastMCP("homekeeper")

for _fn in tools.ALL:
    mcp.tool()(_fn)


if __name__ == "__main__":
    # Streamable HTTP is required by the Alexa+ MCP spec (2025-11-25).
    # stateless_http keeps every request independent (no session affinity),
    # which is what the Lambda deployment needs.
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
    )
