"""HomeKeeper chat agent: Strands + Bedrock, tools via MCP.

Runs on AgentCore Runtime (code-only deploy, no Docker). The agent connects
to HomeKeeper's own MCP server over Streamable HTTP, so every tool call goes
through the same MCP interface Alexa+ would use.

Entry point: agentcore.json / CreateAgentRuntime codeConfiguration.
"""

import json
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get(
    "HOMEKEEPER_MCP_URL",
    "https://7xxrr1lk9e.execute-api.us-east-1.amazonaws.com/mcp",
)
MODEL_ID = os.environ.get(
    "HOMEKEEPER_MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)
REGION = os.environ.get("AWS_REGION", "us-east-1")

SYSTEM_PROMPT = """You are HomeKeeper, a home-appliance assistant. \
You have tools to look up the household briefing, appliance records, \
maintenance tasks, reorder options, and to triage reported issues. \
Always use the tools for factual questions -- never invent appliance data. \
Keep replies short and speakable (1-3 sentences), like a voice assistant. \
If a tool returns candidates asking for clarification, relay the question."""

app = BedrockAgentCoreApp()


def _make_agent():
    mcp_client = MCPClient(
        lambda: streamable_http_client(MCP_URL),
        startup_timeout=30,
    )
    mcp_client.start()
    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=REGION,
        max_tokens=1024,
        temperature=0.3,
    )
    return Agent(
        model=model,
        tools=mcp_client.list_tools_sync(),
        system_prompt=SYSTEM_PROMPT,
    ), mcp_client


_AGENT = None
_MCP_CLIENT = None


def _agent():
    global _AGENT, _MCP_CLIENT
    if _AGENT is None:
        _AGENT, _MCP_CLIENT = _make_agent()
    return _AGENT


@app.entrypoint
def invoke(payload, context):
    """Payload: {"message": str, "history": [{"role","text"}...]}."""
    message = payload.get("message", "")
    history = payload.get("history", [])[-10:]

    agent = _agent()
    # Feed recent history as context, then the new message.
    transcript = ""
    for h in history:
        role = "User" if h.get("role") == "user" else "Assistant"
        transcript += f"{role}: {h.get('text','')}\n"
    prompt = (transcript + f"User: {message}") if transcript else message

    result = agent(prompt)
    text = str(result.message["content"][0]["text"]) if result.message.get("content") else ""

    # Collect tool calls for the frontend card renderer.
    tool_calls = []
    for item in result.metrics.get("tool_calls", []) if hasattr(result, "metrics") else []:
        tool_calls.append(item)

    return {"reply": text, "tool_calls": tool_calls}


if __name__ == "__main__":
    app.run()
