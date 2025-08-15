#!/usr/bin/env python3
"""InsightFinder MCP HTTP LangChain Chatbot

This version connects to the InsightFinder MCP Server via HTTP instead of stdio,
allowing you to test the HTTP server in real-time with OpenAI LLM.
"""

from __future__ import annotations

import asyncio
import os
import json
import httpx
from typing import Any, Dict, List, Optional

from opentelemetry import trace

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 0.  HTTP MCP Tool Implementation
# ---------------------------------------------------------------------------

class HTTPMCPTool(BaseTool):
    """A LangChain tool that calls the MCP server via HTTP."""
    
    name: str
    description: str
    base_url: str = Field(description="Base URL for the MCP server")
    tool_name: str = Field(description="Name of the tool in MCP")
    tool_schema: Dict[str, Any] = Field(description="Schema for tool arguments")
    
    def __init__(self, name: str, description: str, base_url: str, tool_name: str, tool_schema: Dict[str, Any], **kwargs):
        super().__init__(
            name=name,
            description=description,
            base_url=base_url,
            tool_name=tool_name,
            tool_schema=tool_schema,
            **kwargs
        )
    
    @property
    def args(self) -> Dict[str, Any]:
        """Get argument schema from tool schema."""
        return self.tool_schema.get("properties", {})
    
    async def _arun(self, **kwargs) -> str:
        """Execute the tool via HTTP request."""
        config = get_server_config()
        headers = get_auth_headers(config)
        
        async with httpx.AsyncClient() as client:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": self.tool_name,
                    "arguments": {k: v for k, v in kwargs.items() if v is not None}
                }
            }
            
            try:
                response = await client.post(
                    f"{self.base_url}/mcp",
                    json=request,
                    headers=headers,
                    timeout=30.0
                )
                response.raise_for_status()
                
                result = response.json()
                
                if "error" in result:
                    return f"Error: {result['error']['message']}"
                
                # Extract the content from the MCP response
                content = result.get("result", {}).get("content", [])
                if content and len(content) > 0:
                    return content[0].get("text", str(result))
                
                return str(result)
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    return f"Authentication failed: {e.response.text}"
                elif e.response.status_code == 403:
                    return f"Authorization failed: {e.response.text}"
                elif e.response.status_code == 429:
                    return f"Rate limit exceeded: {e.response.text}"
                else:
                    return f"HTTP error {e.response.status_code}: {e.response.text}"
            except Exception as e:
                return f"HTTP request failed: {str(e)}"
    
    def _run(self, **kwargs) -> str:
        """Synchronous version (calls async version)."""
        return asyncio.run(self._arun(**kwargs))


class HTTPMCPClient:
    """HTTP client for MCP server that provides LangChain-compatible tools."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.tools: List[HTTPMCPTool] = []
    
    async def initialize(self) -> bool:
        """Initialize the MCP session and load tools."""
        config = get_server_config()
        headers = get_auth_headers(config)
        
        async with httpx.AsyncClient() as client:
            # Initialize the session
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "LangChain HTTP MCP Client",
                        "version": "1.0.0"
                    }
                }
            }
            
            try:
                response = await client.post(
                    f"{self.base_url}/mcp",
                    json=init_request,
                    headers=headers,
                    timeout=10.0
                )
                response.raise_for_status()
                init_result = response.json()
                
                if "error" in init_result:
                    print(f"Failed to initialize MCP session: {init_result['error']}")
                    return False
                
                # Get available tools
                tools_request = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {}
                }
                
                response = await client.post(
                    f"{self.base_url}/mcp",
                    json=tools_request,
                    headers=headers,
                    timeout=10.0
                )
                response.raise_for_status()
                tools_result = response.json()
                
                if "error" in tools_result:
                    print(f"Failed to get tools: {tools_result['error']}")
                    return False
                
                # Create LangChain tools
                self.tools = []
                for tool_info in tools_result["result"]["tools"]:
                    tool = HTTPMCPTool(
                        name=tool_info["name"],
                        description=tool_info["description"],
                        base_url=self.base_url,
                        tool_name=tool_info["name"],
                        tool_schema=tool_info.get("inputSchema", {"type": "object", "properties": {}})
                    )
                    self.tools.append(tool)
                
                print(f"✓ Loaded {len(self.tools)} tools from HTTP MCP server")
                return True
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    print(f"❌ Authentication failed: Check your credentials")
                elif e.response.status_code == 403:
                    print(f"❌ Authorization failed: Access denied")
                elif e.response.status_code == 429:
                    print(f"❌ Rate limit exceeded: Too many requests")
                else:
                    print(f"❌ HTTP error {e.response.status_code}: {e.response.text}")
                return False
            except Exception as e:
                print(f"Failed to connect to MCP server: {e}")
                return False
    
    def get_tools(self) -> List[HTTPMCPTool]:
        """Get the loaded tools."""
        return self.tools


# ---------------------------------------------------------------------------
# 1.  Connection configuration
# ---------------------------------------------------------------------------

def get_server_config() -> Dict[str, str]:
    """Get server configuration from environment variables."""
    return {
        "base_url": os.getenv("MCP_SERVER_URL", "http://localhost:8000"),
        "api_url": os.getenv("INSIGHTFINDER_API_URL", "https://stg.insightfinder.com"),
        "system_name": os.getenv("INSIGHTFINDER_SYSTEM_NAME", "IF Prod System"),
        "user_name": os.getenv("INSIGHTFINDER_USER_NAME", "mustafa"),
        "license_key": os.getenv("INSIGHTFINDER_LICENSE_KEY", "47b73a737d8a806ef37e1c6d7245b0671261faea"),
        "enable_debug": os.getenv("ENABLE_DEBUG_MESSAGES", "false"),
        # Authentication settings
        "auth_method": os.getenv("HTTP_AUTH_METHOD", "api_key"),
        "api_key": os.getenv("HTTP_API_KEY", ""),
        "bearer_token": os.getenv("HTTP_BEARER_TOKEN", ""),
        "basic_username": os.getenv("HTTP_BASIC_USERNAME", "admin"),
        "basic_password": os.getenv("HTTP_BASIC_PASSWORD", ""),
    }


def get_auth_headers(config: Dict[str, str]) -> Dict[str, str]:
    """Get authentication headers based on configuration."""
    headers = {"Content-Type": "application/json"}
    
    auth_method = config["auth_method"].lower()
    
    if auth_method == "api_key" and config["api_key"]:
        headers["X-API-Key"] = config["api_key"]
    elif auth_method == "bearer" and config["bearer_token"]:
        headers["Authorization"] = f"Bearer {config['bearer_token']}"
    elif auth_method == "basic" and config["basic_username"] and config["basic_password"]:
        import base64
        credentials = f"{config['basic_username']}:{config['basic_password']}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded_credentials}"
    
    return headers


# ---------------------------------------------------------------------------
# 2.  Agent bootstrap
# ---------------------------------------------------------------------------

async def bootstrap_agent():
    """Bootstrap the LangChain agent with HTTP MCP tools."""
    config = get_server_config()
    
    # Create HTTP MCP client
    client = HTTPMCPClient(config["base_url"])
    
    # Initialize and get tools
    if not await client.initialize():
        raise Exception("Failed to initialize MCP client")
    
    tools = client.get_tools()
    
    if not tools:
        raise Exception("No tools available from MCP server")
    
    # Create LLM
    # llm = ChatOpenAI(model="gpt-4", temperature=0)
    llm = ChatOpenAI(model="gpt-4-turbo", temperature=0)  # 128k context window
    # llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
    
    # Create ReAct agent
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


def save_chat_data(prompt: str, response: str):
    """Save chat data for tracing."""
    span = trace.get_current_span()
    span.set_attribute("chat.prompt", prompt)
    span.set_attribute("chat.response", response)


async def test_server_connection():
    """Test the HTTP server connection before starting chat."""
    config = get_server_config()
    headers = get_auth_headers(config)
    
    print(f"🔗 Testing connection to MCP server at {config['base_url']}...")
    
    async with httpx.AsyncClient() as client:
        try:
            # Test health endpoint (no auth required)
            health_response = await client.get(f"{config['base_url']}/health", timeout=5.0)
            health_response.raise_for_status()
            print("✓ Health check passed")
            
            # Test server info (no auth required)
            info_response = await client.get(f"{config['base_url']}/", timeout=5.0)
            info_response.raise_for_status()
            info = info_response.json()
            print(f"✓ Server: {info.get('name', 'Unknown')} v{info.get('version', 'Unknown')}")
            
            # Check authentication status
            auth_info = info.get('authentication', {})
            if auth_info.get('enabled'):
                print(f"🔐 Authentication: {auth_info.get('method', 'unknown').upper()}")
                
                # Test authenticated endpoint
                test_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {}
                }
                
                auth_response = await client.post(
                    f"{config['base_url']}/mcp",
                    json=test_request,
                    headers=headers,
                    timeout=5.0
                )
                auth_response.raise_for_status()
                print("✓ Authentication successful")
            else:
                print("⚠️  Authentication: DISABLED")
            
            return True
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                print("❌ Authentication failed: Check your credentials")
                print("Set the appropriate environment variables:")
                print("  HTTP_API_KEY=your_api_key (for API key auth)")
                print("  HTTP_BEARER_TOKEN=your_token (for bearer auth)")
                print("  HTTP_BASIC_USERNAME and HTTP_BASIC_PASSWORD (for basic auth)")
            elif e.response.status_code == 403:
                print("❌ Authorization failed: Access denied")
            else:
                print(f"❌ Server connection failed: HTTP {e.response.status_code}")
            return False
        except Exception as e:
            print(f"❌ Server connection failed: {e}")
            print(f"Make sure the MCP server is running on {config['base_url']}")
            print("Start it with: TRANSPORT_TYPE=http python -m insightfinder_mcp_server.main")
            return False


async def chat_loop():
    """Main chat loop with HTTP MCP integration."""
    config = get_server_config()
    
    print("🚀 InsightFinder MCP HTTP Chatbot")
    print(f"Server: {config['base_url']}")
    print(f"System: {config['system_name']}")
    print("=" * 50)
    
    # Test server connection first
    if not await test_server_connection():
        return
    
    # Bootstrap agent
    print("\n🔧 Initializing agent...")
    try:
        agent = await bootstrap_agent()
        print("✓ Agent ready!")
    except Exception as e:
        print(f"❌ Failed to bootstrap agent: {e}")
        return
    
    # Chat loop
    history: List[BaseMessage] = []
    print("\n💬 Chat ready — type 'exit' to quit, 'tools' to list available tools.\n")
    
    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        
        if user_input.lower() in {"exit", "quit"}:
            break
        
        if user_input.lower() == "tools":
            config = get_server_config()
            client = HTTPMCPClient(config["base_url"])
            if await client.initialize():
                tools = client.get_tools()
                print(f"\n📋 Available tools ({len(tools)}):")
                for tool in tools:
                    print(f"  • {tool.name}: {tool.description[:80]}...")
                print()
            continue
        
        if user_input.lower() == "clear":
            history = []
            print("🗑️  Chat history cleared.\n")
            continue
        
        # Add user message to history
        history.append(HumanMessage(content=user_input))
        
        try:
            # Call agent with full history
            # print("🤔 Thinking...")
            result = await agent.ainvoke({"messages": history})
            
            save_chat_data(user_input, result["messages"][-1].content)
            
            # Update history from agent output
            history = list(result["messages"])
            history = trim_history(history)
            
            # Print assistant's latest response
            ai_msg = next(msg for msg in reversed(history) if isinstance(msg, AIMessage))
            print(f"Bot > {ai_msg.content}\n")
            
        except Exception as err:
            print(f"❌ Error: {err}\n")


def print_usage():
    """Print usage instructions."""
    config = get_server_config()
    print(f"""
🔧 Setup Instructions:

1. Start the MCP HTTP server:
   export TRANSPORT_TYPE=http
   export INSIGHTFINDER_LICENSE_KEY=your_license_key
   export INSIGHTFINDER_SYSTEM_NAME=your_system_name
   export INSIGHTFINDER_USER_NAME=your_username
   
   # Authentication (recommended)
   export HTTP_AUTH_ENABLED=true
   export HTTP_AUTH_METHOD=api_key  # or bearer, basic
   export HTTP_API_KEY=your_secure_api_key
   
   python -m insightfinder_mcp_server.main

2. Set your OpenAI API key:
   export OPENAI_API_KEY=your_openai_api_key

3. Set client authentication (if server has auth enabled):
   export HTTP_AUTH_METHOD=api_key  # must match server
   export HTTP_API_KEY=same_as_server_key

4. Run this client:
   python client/http_main.py

Environment Variables:
- MCP_SERVER_URL: MCP server URL (default: http://localhost:8000)
- OPENAI_API_KEY: Your OpenAI API key (required)
- TRIM_HISTORY: Max chat history length (default: 0 = unlimited)

Authentication (must match server settings):
- HTTP_AUTH_METHOD: api_key, bearer, or basic
- HTTP_API_KEY: API key for api_key method
- HTTP_BEARER_TOKEN: Token for bearer method  
- HTTP_BASIC_USERNAME/HTTP_BASIC_PASSWORD: Credentials for basic method

Current config:
- MCP Server: {config['base_url']}
- System: {config['system_name']}
- Auth Method: {config['auth_method']}
""")


if __name__ == "__main__":
    # Check for required environment variables
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY environment variable is required")
        print_usage()
        exit(1)
    
    try:
        asyncio.run(chat_loop())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print_usage()
