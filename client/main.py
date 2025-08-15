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

from opentelemetry import trace

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent


# ---------------------------------------------------------------------------
# 1.  Connection mapping
# ---------------------------------------------------------------------------

def make_connections() -> Dict[str, Any]:
    api_url = os.getenv("INSIGHTFINDER_API_URL", "https://stg.insightfinder.com")
    system_name = os.getenv("INSIGHTFINDER_SYSTEM_NAME", "IF Prod System")
    user_name = os.getenv("INSIGHTFINDER_USER_NAME", "mustafa")
    license_key = os.getenv("INSIGHTFINDER_LICENSE_KEY", "47b73a737d8a806ef37e1c6d7245b0671261faea")
    enable_debug = os.getenv("ENABLE_DEBUG_MESSAGES", "false")

    return {
        "insightfinder": {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-e", "INSIGHTFINDER_API_URL=" + api_url,
                "-e", "INSIGHTFINDER_SYSTEM_NAME=" + system_name,
                "-e", "INSIGHTFINDER_USER_NAME=" + user_name,
                "-e", "INSIGHTFINDER_LICENSE_KEY=" + license_key,
                "-e", "ENABLE_DEBUG_MESSAGES=" + enable_debug,
                "-e", "TZ=America/New_York",
                "docker.io/insightfinder/insightfinder-mcp-server:latest"
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
    # llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
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


# @workflow(name="ChatData")
def save_chat_data(prompt: str, response: str):
    span = trace.get_current_span()
    span.set_attribute("chat.prompt", prompt)
    span.set_attribute("chat.response", response)


# @workflow(name="github-mcp-chatbot")
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

    asyncio.run(chat_loop())