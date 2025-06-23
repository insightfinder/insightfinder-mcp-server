#!/usr/bin/env python3
"""GitHub MCP LangChain Chatbot (v3 – with conversation context)

This release threads **full chat history** through the ReAct agent so
answers stay contextual.  You can optionally cap history length via the
`TRIM_HISTORY` env var to avoid hitting model-token limits.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List

from iftracer.sdk import Iftracer
from iftracer.sdk.decorators import workflow
from opentelemetry import trace

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


# ---------------------------------------------------------------------------
# 0.  Observability (Traceloop)
# ---------------------------------------------------------------------------

def init_traceloop() -> None:
    """
    Initialise Traceloop instrumentation if the SDK is available
    and the mandatory API key is present in the environment.
    """

    # You can tweak these values to match your own naming conventions.
    # Traceloop.init(
    #     app_name="github-mcp-chatbot",
    #     api_endpoint="http://localhost:4318"
    # )

    # Traceloop.init(
    #     api_key="tl_5350c1287de5471bbfc08187b1fbe5cb"
    # )
    # Optionally, uncomment for more verbose local debugging:
    # Traceloop.enable_console_exporter()
    # -----------------------------------------------------------------------
    # From this point forward all LangChain / LLM activity is traced.
    # -----------------------------------------------------------------------

    Iftracer.init(
        app_name="llm-agent-chatbot",
        api_endpoint="http://52.90.56.233:4499",
        iftracer_user="maoyuwang",
        iftracer_license_key="595bf1a9253e982b0e3951a1d8ba634fdae19cb3",
        iftracer_project="LLM-AI-Agent-0-0-1-LLM-Agent-Trace",
    )

# ---------------------------------------------------------------------------
# 1.  Connection mapping
# ---------------------------------------------------------------------------

def make_connections() -> Dict[str, Any]:
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_PERSONAL_ACCESS_TOKEN in env.")

    return {
        "github": {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-e",
                f"GITHUB_PERSONAL_ACCESS_TOKEN={token}",
                "ghcr.io/github/github-mcp-server"
            ],
            "transport": "stdio",
        }
    }


# ---------------------------------------------------------------------------
# 2.  Agent bootstrap
# ---------------------------------------------------------------------------

async def bootstrap_agent():
    client = MultiServerMCPClient(make_connections())
    tools = await client.get_tools()
    llm = ChatOpenAI(model="gpt-4.1", temperature=0)
    return create_react_agent(llm, tools)


# ---------------------------------------------------------------------------
# 3.  CLI with conversation memory
# ---------------------------------------------------------------------------

def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Optionally clip history to the most recent N messages (even number)."""
    limit = int(os.getenv("TRIM_HISTORY", "0"))
    if limit and len(messages) > limit:
        return messages[-limit:]
    return messages


@workflow(name="ChatData")
def save_chat_data(prompt: str, response: str):
    span = trace.get_current_span()
    span.set_attribute("chat.prompt", prompt)
    span.set_attribute("chat.response", response)


@workflow(name="github-mcp-chatbot")
async def chat_loop():
    agent = await bootstrap_agent()
    history: List[BaseMessage] = []
    print("✓ GitHub Chatbot ready — type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in {"exit", "quit"}:
            break

        # 1️⃣ Add user message to history
        history.append(HumanMessage(content=user_input))

        try:
            # 2️⃣ Call agent with **full** history
            result = await agent.ainvoke({"messages": history})

            save_chat_data(user_input, result["messages"][-1].content)

            # 3️⃣ Update history from agent output (includes its reply/tool calls)
            history = list(result["messages"])  # copy
            history = trim_history(history)

            # 4️⃣ Print assistant's latest response
            ai_msg = next(msg for msg in reversed(history) if isinstance(msg, AIMessage))
            print(f"Bot > {ai_msg.content}\n")
        except Exception as err:
            print(f"[error] {err}\n")


if __name__ == "__main__":
    # Initialise tracing before the rest of the app boots.
    init_traceloop()

    asyncio.run(chat_loop())