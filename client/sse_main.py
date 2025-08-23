#!/usr/bin/env python3
"""Multi-LLM MCP SSE Streaming Chatbot

A clean, modern chatbot that supports multiple LLM providers:
- OpenAI (ChatGPT)
- Anthropic (Claude) 
- Google (Gemini)
- Ollama (Llama)
- DeepSeek

Connects to InsightFinder MCP Server via Server-Sent Events (SSE) for real-time tool execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode

# Load environment variables from .env file
load_dotenv()


def parse_vllm_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse vLLM tool calls from response content."""
    if not content:
        return []
    
    tool_calls = []
    
    # Try to parse as direct JSON tool call
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict) and "name" in data and "parameters" in data:
            tool_calls.append({
                "name": data["name"],
                "args": data["parameters"],
                "id": f"call_{len(tool_calls)}"
            })
            return tool_calls
    except:
        pass
    
    # Try to find JSON tool calls in the text
    json_pattern = r'\{"name":\s*"[^"]+",\s*"parameters":\s*\{[^}]*\}\}'
    matches = re.findall(json_pattern, content)
    
    for match in matches:
        try:
            data = json.loads(match)
            if "name" in data and "parameters" in data:
                tool_calls.append({
                    "name": data["name"],
                    "args": data["parameters"],
                    "id": f"call_{len(tool_calls)}"
                })
        except:
            continue
    
    return tool_calls


# =============================================================================
# LLM Providers
# =============================================================================

def get_available_llms() -> Dict[str, Any]:
    """Get available LLM providers based on API keys."""
    llms = {}
    
    # OpenAI (ChatGPT)
    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        llms["chatgpt"] = {
            "class": ChatOpenAI,
            "models": ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
            "default_model": "gpt-4o"
        }
    
    # Anthropic (Claude)
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            from langchain_anthropic import ChatAnthropic
            llms["claude"] = {
                "class": ChatAnthropic,
                "models": ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-haiku-20240307"],
                "default_model": "claude-3-5-sonnet-20241022"
            }
        except ImportError:
            print("⚠️  langchain-anthropic not installed. Install with: pip install langchain-anthropic")
    
    # Google (Gemini)
    if os.getenv("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llms["gemini"] = {
                "class": ChatGoogleGenerativeAI,
                "models": ["gemini-2.0-flash-exp", "gemini-1.5-pro", "gemini-1.5-flash"],
                "default_model": "gemini-2.0-flash-exp"
            }
        except ImportError:
            print("⚠️  langchain-google-genai not installed. Install with: pip install langchain-google-genai")
    
    # Ollama (Llama)
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    if ollama_url:
        try:
            # Test if Ollama is running
            response = requests.get(f"{ollama_url}/api/tags", timeout=2)
            if response.status_code == 200:
                try:
                    from langchain_ollama import ChatOllama
                    llms["llama"] = {
                        "class": ChatOllama,
                        "models": [os.getenv("OLLAMA_MODEL", "llama3.1")],
                        "default_model": os.getenv("OLLAMA_MODEL", "llama3.1"),
                        "base_url": ollama_url
                    }
                except ImportError:
                    print("⚠️  langchain-ollama not installed. Install with: pip install langchain-ollama")
        except:
            pass
    
    # vLLM (OpenAI-compatible API)
    vllm_url = os.getenv("VLLM_BASE_URL", "http://localhost:7000")
    if vllm_url:
        try:
            # Test if vLLM is running
            response = requests.get(f"{vllm_url}/v1/models", timeout=2)
            if response.status_code == 200:
                models_data = response.json()
                available_models = []
                
                # Handle different response formats
                if "data" in models_data:
                    # Standard OpenAI format: {"data": [{"id": "model_name"}]}
                    available_models = [model["id"] for model in models_data["data"]]
                elif "models" in models_data:
                    # Some vLLM versions: {"models": ["model_name"]}
                    available_models = models_data["models"]
                elif isinstance(models_data, list):
                    # Direct list format: ["model_name"]
                    available_models = models_data
                
                if available_models:
                    print(f"🔍 vLLM detected models: {', '.join(available_models)}")
                    from langchain_openai import ChatOpenAI  # vLLM uses OpenAI-compatible API
                    llms["vllm"] = {
                        "class": ChatOpenAI,
                        "models": available_models,
                        "default_model": available_models[0],
                        "base_url": f"{vllm_url}/v1",
                        # vLLM often runs without auth; provide a default API key for OpenAI-compatible client
                        "api_key": os.getenv("VLLM_API_KEY", os.getenv("OPENAI_API_KEY", "EMPTY")),
                        # Add model info for debugging
                        "server_url": vllm_url
                    }
        except:
            pass
    
    # DeepSeek
    if os.getenv("DEEPSEEK_API_KEY"):
        from langchain_openai import ChatOpenAI  # DeepSeek uses OpenAI-compatible API
        llms["deepseek"] = {
            "class": ChatOpenAI,
            "models": ["deepseek-chat", "deepseek-coder"],
            "default_model": "deepseek-chat",
            "api_key": os.getenv("DEEPSEEK_API_KEY"),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        }
    
    return llms


def create_llm(provider: str, model: Optional[str] = None, temperature: float = 0) -> Any:
    """Create an LLM instance for the specified provider."""
    available_llms = get_available_llms()
    
    if provider not in available_llms:
        raise ValueError(f"LLM provider '{provider}' not available. Available: {list(available_llms.keys())}")
    
    llm_config = available_llms[provider]
    model = model or llm_config["default_model"]
    
    # Create LLM with provider-specific parameters
    if provider == "chatgpt":
        return llm_config["class"](model=model, temperature=temperature)
    elif provider == "claude":
        return llm_config["class"](model=model, temperature=temperature)
    elif provider == "gemini":
        return llm_config["class"](model=model, temperature=temperature)
    elif provider == "llama":
        return llm_config["class"](
            model=model, 
            temperature=temperature,
            base_url=llm_config["base_url"]
        )
    elif provider == "vllm":
        # Configure vLLM for function calling support
        return llm_config["class"](
            model=model,
            temperature=temperature,
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
            # Enable function calling for vLLM
            model_kwargs={
                "tools": None,  # Will be set when binding tools
                "tool_choice": "auto"  # Let the model decide when to use tools
            }
        )
    elif provider == "deepseek":
        return llm_config["class"](
            model=model,
            temperature=temperature,
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"]
        )


# =============================================================================
# SSE Event Handling
# =============================================================================

@dataclass
class SSEEvent:
    """Represents an SSE event"""
    event: str
    data: str
    id: Optional[str] = None


# =============================================================================
# MCP Tool Implementation  
# =============================================================================

class MCPTool(BaseTool):
    """A LangChain tool that calls the MCP server via SSE streaming."""
    
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
                            current_event = None
                            
            except Exception as e:
                yield {"event": "error", "data": {"error": str(e)}}
    
    async def _arun(self, **kwargs) -> str:
        """Execute the tool via SSE streaming."""
        results = []
        progress_info = None
        
        if self.enable_progress:
            print(f"🔧 Streaming {self.tool_name}...")
        
        async for event_data in self._stream_tool_execution(**kwargs):
            event = event_data.get("event")
            data = event_data.get("data", {})
            
            if event == "tool_started":
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
                
            elif event in ["error", "tool_error"]:
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


# =============================================================================
# MCP Client
# =============================================================================

class MCPClient:
    """MCP client that provides streaming LangChain tools."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.tools: List[MCPTool] = []
    
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
        
        # Check SSE support
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
                        "name": "Multi-LLM MCP Client",
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
                enable_progress = os.getenv("SSE_SHOW_PROGRESS", "false").lower() == "true"
                
                for tool_info in tools_result["result"]["tools"]:
                    tool = MCPTool(
                        name=tool_info["name"],
                        description=tool_info["description"],
                        base_url=self.base_url,
                        tool_name=tool_info["name"],
                        tool_schema=tool_info.get("inputSchema", {"type": "object", "properties": {}}),
                        enable_progress=enable_progress
                    )
                    self.tools.append(tool)
                
                print(f"✓ Loaded {len(self.tools)} MCP tools")
                return True
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    print(f"❌ Authentication failed: Check your credentials")
                elif e.response.status_code == 403:
                    print(f"❌ Authorization failed: Access denied")
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
                        
                        if time.time() - start_time > 5:
                            break
                    
                    print(f"✅ SSE connection successful ({event_count} events received)")
                    return True
                    
            except Exception as e:
                print(f"❌ SSE connection failed: {e}")
                return False
    
    def get_tools(self) -> List[MCPTool]:
        """Get the loaded tools."""
        return self.tools


# =============================================================================
# Configuration & Utilities
# =============================================================================

def get_server_config() -> Dict[str, Any]:
    """Get server configuration from environment variables."""
    return {
        "base_url": os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000"),
        "api_url": os.getenv("INSIGHTFINDER_API_URL", "https://stg.insightfinder.com"),
        "system_name": os.getenv("INSIGHTFINDER_SYSTEM_NAME", ""),
        "user_name": os.getenv("INSIGHTFINDER_USER_NAME", ""),
        "license_key": os.getenv("INSIGHTFINDER_LICENSE_KEY", ""),
        "verify_ssl": os.getenv("VERIFY_SSL", "true").lower() == "true",
        "ssl_cert_path": os.getenv("SSL_CERT_PATH", ""),
        "auth_method": os.getenv("HTTP_AUTH_METHOD", "api_key"),
        "api_key": os.getenv("HTTP_API_KEY", ""),
    }


def get_auth_headers(config: Dict[str, Any]) -> Dict[str, str]:
    """Get authentication headers based on configuration."""
    headers = {"Content-Type": "application/json"}
    
    # Add InsightFinder credentials headers
    if config.get("license_key"):
        headers["X-IF-License-Key"] = config["license_key"]
    if config.get("user_name"):
        headers["X-IF-User-Name"] = config["user_name"]
    if config.get("api_url"):
        headers["X-IF-API-URL"] = config["api_url"]
    
    # Add server authentication headers
    if config["api_key"]:
        headers["X-API-Key"] = config["api_key"]
    
    return headers


def create_http_client(config: Dict[str, Any]) -> httpx.AsyncClient:
    """Create HTTP client with SSL configuration."""
    verify_ssl = config.get("verify_ssl", True)
    ssl_cert_path = config.get("ssl_cert_path", "")
    
    if ssl_cert_path and os.path.exists(ssl_cert_path):
        return httpx.AsyncClient(verify=ssl_cert_path)
    elif not verify_ssl:
        return httpx.AsyncClient(verify=False)
    else:
        return httpx.AsyncClient()


def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Optionally clip history to the most recent N messages."""
    limit = int(os.getenv("TRIM_HISTORY", "0"))
    if limit and len(messages) > limit:
        return messages[-limit:]
    return messages


# =============================================================================
# Agent Bootstrap
# =============================================================================

def create_function_calling_agent(llm, tools):
    """Create a function calling agent for vLLM models that support tool calls."""
    
    # Bind tools to the LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # Define the agent workflow
    def call_model(state: MessagesState):
        """Call the LLM with tools bound."""
        messages = state["messages"]
        response = llm_with_tools.invoke(messages)
        
        # Parse vLLM tool calls from content if no tool_calls detected
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Native tool calls already present
            pass
        else:
            # Try to parse tool calls from content for vLLM
            parsed_tool_calls = parse_vllm_tool_calls(response.content)
            if parsed_tool_calls:
                # Create a new AIMessage with proper tool_calls
                response = AIMessage(
                    content="",  # Clear content since it's now a tool call
                    tool_calls=parsed_tool_calls
                )
        
        return {"messages": [response]}
    
    def should_continue(state: MessagesState) -> str:
        """Determine if the agent should continue or finish."""
        messages = state["messages"]
        last_message = messages[-1]
        
        # If the last message has tool calls, continue to execute tools
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        # Otherwise, we're done
        return "end"
    
    # Create the graph
    workflow = StateGraph(MessagesState)
    
    # Add nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))
    
    # Set entry point
    workflow.set_entry_point("agent")
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools", 
            "end": "__end__"
        }
    )
    
    # Tools should always go back to the agent
    workflow.add_edge("tools", "agent")
    
    # Compile the graph
    return workflow.compile()


async def bootstrap_agent(llm_provider: str, model: Optional[str] = None):
    """Bootstrap the LangChain agent with MCP tools."""
    config = get_server_config()
    
    # Create MCP client
    client = MCPClient(config["base_url"])
    
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
    llm = create_llm(llm_provider, model)
    
    # Create agent based on provider
    if llm_provider == "vllm":
        # Use function calling agent for vLLM
        print("🔧 Creating function calling agent for vLLM...")
        agent = create_function_calling_agent(llm, tools)
    else:
        # Use ReAct agent for other providers
        print(f"🔧 Creating ReAct agent for {llm_provider}...")
        agent = create_react_agent(llm, tools)
    
    return agent, client


# =============================================================================
# Interactive Chat Interface
# =============================================================================

async def interactive_chat():
    """Interactive chat with LLM selection."""
    print("🚀 Multi-LLM MCP Streaming Chatbot")
    print("=" * 50)
    
    # Show available LLMs
    available_llms = get_available_llms()
    if not available_llms:
        print("❌ No LLM providers available. Please check your .env configuration.")
        print("💡 Copy client/.env.example to client/.env and add your API keys")
        return
    
    print("🤖 Available LLM Providers:")
    for i, (name, config) in enumerate(available_llms.items(), 1):
        models = ", ".join(config["models"][:2])  # Show first 2 models
        if len(config["models"]) > 2:
            models += f" (+{len(config["models"]) - 2} more)"
        print(f"  {i}. {name.title()}: {models}")
    print()
    
    # Select LLM provider
    while True:
        try:
            choice = input("Select LLM provider (1-5 or name): ").strip().lower()
            
            if choice.isdigit():
                choice = int(choice) - 1
                if 0 <= choice < len(available_llms):
                    llm_provider = list(available_llms.keys())[choice]
                    break
            elif choice in available_llms:
                llm_provider = choice
                break
            else:
                print("❌ Invalid selection. Try again.")
        except (ValueError, KeyboardInterrupt):
            print("👋 Goodbye!")
            return
    
    # Select model (optional)
    llm_config = available_llms[llm_provider]
    if len(llm_config["models"]) > 1:
        print(f"\n📋 Available {llm_provider.title()} models:")
        for i, model in enumerate(llm_config["models"], 1):
            print(f"  {i}. {model}")
        
        try:
            model_choice = input(f"Select model (1-{len(llm_config['models'])} or press Enter for default): ").strip()
            if model_choice.isdigit():
                model_idx = int(model_choice) - 1
                if 0 <= model_idx < len(llm_config["models"]):
                    selected_model = llm_config["models"][model_idx]
                else:
                    selected_model = llm_config["default_model"]
            else:
                selected_model = llm_config["default_model"]
        except:
            selected_model = llm_config["default_model"]
    else:
        selected_model = llm_config["default_model"]
    
    print(f"\n✓ Selected: {llm_provider.title()} ({selected_model})")
    
    # Initialize agent
    try:
        print("\n🔧 Initializing agent...")
        agent, client = await bootstrap_agent(llm_provider, selected_model)
        print("✓ Agent ready!")
    except Exception as e:
        print(f"❌ Failed to initialize agent: {e}")
        return
    
    # Chat loop
    history: List[BaseMessage] = []
    progress_enabled = os.getenv("SSE_SHOW_PROGRESS", "false").lower() == "true"
    
    print(f"\n💬 Chat ready! Type 'help' for commands, 'exit' to quit.")
    print(f"🔧 Tools: {len(client.get_tools())} available")
    print(f"📊 Progress updates: {'ON' if progress_enabled else 'OFF'}")
    if llm_provider == "vllm":
        print(f"⚡ Using function calling agent for vLLM")
    else:
        print(f"🤖 Using ReAct agent for {llm_provider}")
    print()
    
    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        
        if user_input.lower() in {"exit", "quit", "bye"}:
            break
        
        if user_input.lower() == "help":
            print("\n📋 Available commands:")
            print("  • help - Show this help")
            print("  • tools - List available MCP tools")
            print("  • clear - Clear chat history")
            print("  • progress - Toggle progress updates")
            print("  • test-sse - Test SSE connection")
            print("  • exit/quit/bye - Exit the chat")
            print()
            continue
        
        if user_input.lower() == "tools":
            tools = client.get_tools()
            print(f"\n🔧 Available MCP tools ({len(tools)}):")
            for i, tool in enumerate(tools, 1):
                print(f"  {i}. {tool.name}: {tool.description[:60]}...")
            print()
            continue
        
        if user_input.lower() == "clear":
            history = []
            print("🗑️  Chat history cleared.\n")
            continue
        
        if user_input.lower() == "progress":
            current = os.getenv("SSE_SHOW_PROGRESS", "false").lower() == "true"
            new_state = not current
            os.environ["SSE_SHOW_PROGRESS"] = "true" if new_state else "false"
            
            # Re-initialize tools with new progress setting
            await client.initialize()
            print(f"📊 Progress updates: {'ON' if new_state else 'OFF'}")
            continue
        
        if user_input.lower() == "test-sse":
            await client.test_sse_connection()
            continue
        
        if not user_input:
            continue
        
        # Process message
        history.append(HumanMessage(content=user_input))
        
        try:
            result = await agent.ainvoke({"messages": history})
            
            # Update history
            history = list(result["messages"])
            history = trim_history(history)
            
            # Get assistant's final response (the last AIMessage that doesn't have tool calls)
            ai_msg = None
            for msg in reversed(history):
                if isinstance(msg, AIMessage):
                    # For function calling agents, get the final response after tool execution
                    if llm_provider == "vllm":
                        # Skip messages that only contain tool calls
                        if hasattr(msg, 'tool_calls') and msg.tool_calls and not msg.content:
                            continue
                    ai_msg = msg
                    break
            
            if ai_msg and ai_msg.content:
                print(f"Bot > {ai_msg.content}\n")
            else:
                print("Bot > [No response generated]\n")
            
        except Exception as err:
            print(f"❌ Error: {err}\n")
    
    print("👋 Goodbye!")


# =============================================================================
# Main Entry Point
# =============================================================================

def print_setup_help():
    """Print setup instructions."""
    print("""
🔧 Multi-LLM MCP Client Setup:

1. Copy the example environment file:
   cp client/.env.example client/.env

2. Edit client/.env and add your API keys for the LLMs you want to use:
   - OPENAI_API_KEY=your_openai_key (for ChatGPT)
   - ANTHROPIC_API_KEY=your_anthropic_key (for Claude)
   - GOOGLE_API_KEY=your_google_key (for Gemini)
   - VLLM_BASE_URL=http://localhost:8000 (for vLLM)
   - DEEPSEEK_API_KEY=your_deepseek_key (for DeepSeek)

3. For Ollama (Llama), make sure Ollama is running:
   ollama serve

4. Start the MCP server:
   TRANSPORT_TYPE=http SSE_ENABLED=true python -m insightfinder_mcp_server.main

5. Run this client:
   python client/sse_main.py

🎯 Supported LLM Providers:
- OpenAI (ChatGPT): GPT-4o, GPT-4-turbo, GPT-3.5-turbo (ReAct agent)
- Anthropic (Claude): Claude-3.5-Sonnet, Claude-3-Opus, Claude-3-Haiku (ReAct agent)
- Google (Gemini): Gemini-2.0-Flash, Gemini-1.5-Pro, Gemini-1.5-Flash (ReAct agent)
- Ollama (Llama): Local Llama models (ReAct agent)
- vLLM: OpenAI-compatible API for local models (Function calling agent)
- DeepSeek: DeepSeek-Chat, DeepSeek-Coder (ReAct agent)

🔧 Agent Types:
- ReAct Agent: Uses text-based reasoning and action format
- Function Calling Agent: Uses native function calls (vLLM only)
""")


if __name__ == "__main__":
    try:
        asyncio.run(interactive_chat())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print_setup_help()
