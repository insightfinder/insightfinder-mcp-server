#!/usr/bin/env python3
"""InsightFinder MCP SSE Streaming LangChain Chatbot

This version connects to the InsightFinder MCP Server via Server-Sent Events (SSE),
allowing real-time streaming of tool execution results with progress updates.
"""

from __future__ import annotations

import asyncio
import os
import json
import httpx
import time
from typing import Any, Dict, List, Optional, AsyncGenerator
from dataclasses import dataclass

from opentelemetry import trace

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 0. SSE Event Handling
# ---------------------------------------------------------------------------

@dataclass
class SSEEvent:
    """Represents an SSE event"""
    event: str
    data: str
    id: Optional[str] = None


class SSEEventHandler:
    """Handles SSE event processing"""
    
    def __init__(self):
        self.on_connected: Optional[callable] = None
        self.on_heartbeat: Optional[callable] = None
        self.on_tool_started: Optional[callable] = None
        self.on_partial_result: Optional[callable] = None
        self.on_tool_result: Optional[callable] = None
        self.on_tool_completed: Optional[callable] = None
        self.on_error: Optional[callable] = None
    
    async def handle_event(self, event: str, data: str):
        """Handle incoming SSE events"""
        try:
            if data:
                event_data = json.loads(data)
            else:
                event_data = {}
            
            if event == 'connected' and self.on_connected:
                await self.on_connected(event_data)
            elif event == 'heartbeat' and self.on_heartbeat:
                await self.on_heartbeat(event_data)
            elif event == 'tool_started' and self.on_tool_started:
                await self.on_tool_started(event_data)
            elif event == 'partial_result' and self.on_partial_result:
                await self.on_partial_result(event_data)
            elif event == 'tool_result' and self.on_tool_result:
                await self.on_tool_result(event_data)
            elif event == 'tool_completed' and self.on_tool_completed:
                await self.on_tool_completed(event_data)
            elif event in ['error', 'tool_error'] and self.on_error:
                await self.on_error(event_data)
                
        except json.JSONDecodeError as e:
            print(f"Failed to parse event data: {e}")
        except Exception as e:
            print(f"Error handling SSE event: {e}")


# ---------------------------------------------------------------------------
# 1. SSE MCP Tool Implementation
# ---------------------------------------------------------------------------

class SSEMCPTool(BaseTool):
    """A LangChain tool that calls the MCP server via SSE streaming."""
    
    name: str
    description: str
    base_url: str = Field(description="Base URL for the MCP server")
    tool_name: str = Field(description="Name of the tool in MCP")
    tool_schema: Dict[str, Any] = Field(description="Schema for tool arguments")
    enable_progress: bool = Field(default=False, description="Enable verbose progress updates")
    
    def __init__(self, name: str, description: str, base_url: str, tool_name: str, 
                 tool_schema: Dict[str, Any], enable_progress: bool = False, **kwargs):
        super().__init__(
            name=name,
            description=description,
            base_url=base_url,
            tool_name=tool_name,
            tool_schema=tool_schema,
            enable_progress=enable_progress,
            **kwargs
        )
    
    @property
    def args(self) -> Dict[str, Any]:
        """Get argument schema from tool schema."""
        return self.tool_schema.get("properties", {})
    
    async def _stream_tool_execution(self, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream tool execution via SSE."""
        config = get_server_config()
        headers = get_auth_headers(config)
        headers["Accept"] = "text/event-stream"
        
        # Filter out None values
        tool_args = {k: v for k, v in kwargs.items() if v is not None}
        
        async with create_http_client(config) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/tools/{self.tool_name}/stream",
                    json=tool_args,
                    headers=headers,
                    timeout=60.0
                ) as response:
                    response.raise_for_status()
                    
                    current_event = None
                    async for line in response.aiter_lines():
                        line = line.strip()
                        
                        if line.startswith('event:'):
                            current_event = line[6:].strip()
                        elif line.startswith('data:'):
                            data = line[5:].strip()
                            
                            if current_event and data:
                                try:
                                    event_data = json.loads(data)
                                    yield {"event": current_event, "data": event_data}
                                except json.JSONDecodeError:
                                    yield {"event": current_event, "data": {"raw": data}}
                            
                            current_event = None
                        elif not line:
                            # Empty line indicates end of event
                            current_event = None
                            
            except Exception as e:
                yield {"event": "error", "data": {"error": str(e)}}
    
    async def _arun(self, **kwargs) -> str:
        """Execute the tool via SSE streaming."""
        results = []
        progress_info = None
        tool_info = None
        
        # Only show initial message if progress is enabled
        if self.enable_progress:
            print(f"🔧 Streaming {self.tool_name}...")
        
        async for event_data in self._stream_tool_execution(**kwargs):
            event = event_data.get("event")
            data = event_data.get("data", {})
            
            if event == "tool_started":
                tool_info = data
                if self.enable_progress:
                    print(f"  ▶️  Started: {data.get('tool', 'unknown')}")
            
            elif event == "partial_result":
                batch = data.get("batch", [])
                progress = data.get("progress", {})
                results.extend(batch)
                
                if self.enable_progress and progress:
                    current = progress.get("current", 0)
                    total = progress.get("total", 0)
                    percentage = progress.get("percentage", 0)
                    print(f"  📊 Progress: {current}/{total} ({percentage:.1f}%)")
                    progress_info = progress
            
            elif event == "tool_result":
                result = data.get("result")
                if isinstance(result, list):
                    results.extend(result)
                else:
                    results.append(result)
                    
                if self.enable_progress:
                    print(f"  ✅ Received result")
            
            elif event == "tool_completed":
                if self.enable_progress:
                    print(f"  🏁 Completed: {data.get('tool', 'unknown')}")
                break
                
            elif event == "error" or event == "tool_error":
                error_msg = data.get("error", "Unknown error")
                print(f"  ❌ Error: {error_msg}")
                return f"Tool execution failed: {error_msg}"
        
        # Format the final result
        if results:
            if len(results) == 1:
                return json.dumps(results[0], indent=2, default=str)
            else:
                summary = f"Retrieved {len(results)} items"
                if progress_info:
                    summary += f" (streamed in batches)"
                return f"{summary}\n\n" + json.dumps(results, indent=2, default=str)
        else:
            return "No results returned from tool execution"
    
    def _run(self, **kwargs) -> str:
        """Synchronous version (calls async version)."""
        return asyncio.run(self._arun(**kwargs))


# ---------------------------------------------------------------------------
# 2. SSE MCP Client
# ---------------------------------------------------------------------------

class SSEMCPClient:
    """SSE-enabled MCP client that provides streaming LangChain tools."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.tools: List[SSEMCPTool] = []
        self.connection_id: Optional[str] = None
        self.event_handler = SSEEventHandler()
    
    async def check_sse_support(self) -> bool:
        """Check if the server supports SSE streaming."""
        config = get_server_config()
        headers = get_auth_headers(config)
        
        async with create_http_client(config) as client:
            try:
                response = await client.get(f"{self.base_url}/", headers=headers, timeout=5.0)
                response.raise_for_status()
                
                server_info = response.json()
                capabilities = server_info.get("capabilities", {})
                streaming = capabilities.get("streaming", {})
                
                return streaming.get("supported", False)
                
            except Exception:
                return False
    
    async def initialize(self) -> bool:
        """Initialize the MCP session and load tools."""
        config = get_server_config()
        headers = get_auth_headers(config)
        
        # Check SSE support first
        if not await self.check_sse_support():
            print("⚠️  Server does not support SSE streaming, falling back to standard HTTP")
        else:
            print("✅ Server supports SSE streaming")
        
        async with create_http_client(config) as client:
            # Initialize the session
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"streaming": True},
                    "clientInfo": {
                        "name": "LangChain SSE MCP Client",
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
                
                # Create SSE-enabled LangChain tools
                self.tools = []
                enable_progress = os.getenv("SSE_SHOW_PROGRESS", "false").lower() == "true"
                
                for tool_info in tools_result["result"]["tools"]:
                    tool = SSEMCPTool(
                        name=tool_info["name"],
                        description=tool_info["description"] + " (with SSE streaming)",
                        base_url=self.base_url,
                        tool_name=tool_info["name"],
                        tool_schema=tool_info.get("inputSchema", {"type": "object", "properties": {}}),
                        enable_progress=enable_progress  # Control verbose progress output via SSE_SHOW_PROGRESS
                    )
                    self.tools.append(tool)
                
                print(f"✓ Loaded {len(self.tools)} SSE-enabled tools")
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
    
    async def test_sse_connection(self) -> bool:
        """Test SSE connection."""
        config = get_server_config()
        headers = get_auth_headers(config)
        headers["Accept"] = "text/event-stream"
        
        print("🔌 Testing SSE connection...")
        
        async with create_http_client(config) as client:
            try:
                async with client.stream(
                    "GET",
                    f"{self.base_url}/mcp/events",
                    headers=headers,
                    timeout=10.0
                ) as response:
                    response.raise_for_status()
                    
                    event_count = 0
                    start_time = time.time()
                    
                    async for line in response.aiter_lines():
                        line = line.strip()
                        
                        if line.startswith('data:'):
                            event_count += 1
                            if event_count >= 2:  # Connected + heartbeat
                                break
                        
                        # Timeout after 5 seconds
                        if time.time() - start_time > 5:
                            break
                    
                    print(f"✅ SSE connection successful ({event_count} events received)")
                    return True
                    
            except Exception as e:
                print(f"❌ SSE connection failed: {e}")
                return False
    
    def get_tools(self) -> List[SSEMCPTool]:
        """Get the loaded tools."""
        return self.tools


# ---------------------------------------------------------------------------
# 3. Shared utilities (same as http_main.py)
# ---------------------------------------------------------------------------

def get_server_config() -> Dict[str, str]:
    """Get server configuration from environment variables."""
    return {
        "base_url": os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000"),
        "api_url": os.getenv("INSIGHTFINDER_API_URL", "https://stg.insightfinder.com"),
        "system_name": os.getenv("INSIGHTFINDER_SYSTEM_NAME", "system_name"),
        "user_name": os.getenv("INSIGHTFINDER_USER_NAME", "username"),
        "license_key": os.getenv("INSIGHTFINDER_LICENSE_KEY", "license_key"),
        "enable_debug": os.getenv("ENABLE_DEBUG_MESSAGES", "false"),
        # SSL settings
        "verify_ssl": os.getenv("VERIFY_SSL", "true").lower() == "true",
        "ssl_cert_path": os.getenv("SSL_CERT_PATH", ""),
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


def create_http_client(config: Dict[str, str]) -> httpx.AsyncClient:
    """Create HTTP client with SSL configuration."""
    verify_ssl = config.get("verify_ssl", True)
    ssl_cert_path = config.get("ssl_cert_path", "")
    
    if ssl_cert_path and os.path.exists(ssl_cert_path):
        # Use custom certificate
        return httpx.AsyncClient(verify=ssl_cert_path)
    elif not verify_ssl:
        # Disable SSL verification for self-signed certificates
        return httpx.AsyncClient(verify=False)
    else:
        # Default SSL verification
        return httpx.AsyncClient()


# ---------------------------------------------------------------------------
# 4. Agent bootstrap
# ---------------------------------------------------------------------------

async def bootstrap_agent():
    """Bootstrap the LangChain agent with SSE MCP tools."""
    config = get_server_config()
    
    # Create SSE MCP client
    client = SSEMCPClient(config["base_url"])
    
    # Test SSE connection
    if not await client.test_sse_connection():
        print("⚠️  SSE connection test failed, but continuing...")
    
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
# 5. CLI with conversation memory
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
    
    # Show SSL configuration
    if not config.get("verify_ssl", True):
        print("⚠️  SSL verification: DISABLED (using self-signed certificates)")
    else:
        print("🔒 SSL verification: ENABLED")
    
    async with create_http_client(config) as client:
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
            
            # Check SSE support
            capabilities = info.get('capabilities', {})
            streaming = capabilities.get('streaming', {})
            if streaming.get('supported'):
                print(f"✓ SSE Streaming: ENABLED")
                print(f"  Transport: {streaming.get('transport', 'unknown')}")
                endpoints = streaming.get('endpoints', {})
                for name, path in endpoints.items():
                    print(f"  {name}: {path}")
            else:
                print("⚠️  SSE Streaming: NOT SUPPORTED")
            
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
            error_msg = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in error_msg and "self-signed certificate" in error_msg:
                print("❌ SSL Certificate verification failed (self-signed certificate)")
                print("💡 To connect to a server with self-signed certificate:")
                print("   export VERIFY_SSL=false")
                print("   python3 client/sse_main.py")
                print("")
                print("   OR provide the certificate path:")
                print("   export SSL_CERT_PATH=/path/to/server.crt")
            else:
                print(f"❌ Server connection failed: {e}")
            print(f"Make sure the MCP server is running on {config['base_url']}")
            print("Start it with: TRANSPORT_TYPE=http SSE_ENABLED=true python -m insightfinder_mcp_server.main")
            return False


async def chat_loop():
    """Main chat loop with SSE MCP integration."""
    config = get_server_config()
    
    print("🚀 InsightFinder MCP SSE Streaming Chatbot")
    print(f"Server: {config['base_url']}")
    print(f"System: {config['system_name']}")
    print("=" * 60)
    
    # Test server connection first
    if not await test_server_connection():
        return
    
    # Bootstrap agent
    print("\n🔧 Initializing SSE streaming agent...")
    try:
        agent = await bootstrap_agent()
        print("✓ SSE streaming agent ready!")
    except Exception as e:
        print(f"❌ Failed to bootstrap agent: {e}")
        return
    
    # Chat loop
    history: List[BaseMessage] = []
    progress_enabled = os.getenv("SSE_SHOW_PROGRESS", "false").lower() == "true"
    print(f"\n💬 SSE Chat ready — type 'exit' to quit, 'tools' to list available tools.")
    print(f"📊 Progress updates: {'ENABLED' if progress_enabled else 'DISABLED'} (type 'progress' to toggle)")
    print("📡 Real-time tool execution streaming is active!\n")
    
    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        
        if user_input.lower() in {"exit", "quit"}:
            break
        
        if user_input.lower() == "tools":
            config = get_server_config()
            client = SSEMCPClient(config["base_url"])
            if await client.initialize():
                tools = client.get_tools()
                print(f"\n📋 Available SSE-enabled tools ({len(tools)}):")
                for tool in tools:
                    print(f"  • {tool.name}: {tool.description[:80]}...")
                print()
            continue
        
        if user_input.lower() == "clear":
            history = []
            print("🗑️  Chat history cleared.\n")
            continue
            
        if user_input.lower() == "test-sse":
            config = get_server_config()
            client = SSEMCPClient(config["base_url"])
            await client.test_sse_connection()
            continue
            
        if user_input.lower() in ["progress", "toggle-progress"]:
            # Toggle progress output
            current_state = os.getenv("SSE_SHOW_PROGRESS", "false").lower() == "true"
            new_state = not current_state
            
            # Update environment variable and re-initialize client
            os.environ["SSE_SHOW_PROGRESS"] = "true" if new_state else "false"
            try:
                agent = await bootstrap_agent()  # Re-bootstrap with new settings
                print(f"📊 Progress updates: {'ENABLED' if new_state else 'DISABLED'}")
            except Exception as e:
                print(f"❌ Failed to update progress settings: {e}")
            continue
        
        # Add user message to history
        history.append(HumanMessage(content=user_input))
        
        try:
            # Call agent with full history
            print("🤔 Processing with SSE streaming...")
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
🔧 SSE Streaming Setup Instructions:

1. Start the MCP HTTP server with SSE enabled:
   export TRANSPORT_TYPE=http
   export SSE_ENABLED=true
   export SSE_HEARTBEAT_ENABLED=true
   export SSE_PING_INTERVAL=30
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

4. Run this SSE streaming client:
   python client/sse_main.py

Environment Variables:
- MCP_SERVER_URL: MCP server URL (default: http://127.0.0.1:8000)
- OPENAI_API_KEY: Your OpenAI API key (required)
- TRIM_HISTORY: Max chat history length (default: 0 = unlimited)
- VERIFY_SSL: Enable/disable SSL verification (default: true)
- SSL_CERT_PATH: Path to custom SSL certificate (optional)
- SSE_SHOW_PROGRESS: Show verbose progress updates (default: false)

Authentication (must match server settings):
- HTTP_AUTH_METHOD: api_key, bearer, or basic
- HTTP_API_KEY: API key for api_key method
- HTTP_BEARER_TOKEN: Token for bearer method  
- HTTP_BASIC_USERNAME/HTTP_BASIC_PASSWORD: Credentials for basic method

SSE Features:
- Real-time tool execution streaming (silent by default)
- Streaming results for large datasets
- Connection status monitoring
- Automatic retry and reconnection

To enable verbose progress updates:
   export SSE_SHOW_PROGRESS=true

Chat Commands:
- 'tools' - List available SSE-enabled tools
- 'test-sse' - Test SSE connection
- 'progress' - Toggle verbose progress updates on/off
- 'clear' - Clear chat history
- 'exit' or 'quit' - Exit the chat

Current config:
- MCP Server: {config['base_url']}
- System: {config['system_name']}
- Auth Method: {config['auth_method']}
- SSL Verification: {'enabled' if config.get('verify_ssl', True) else 'disabled'}
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
