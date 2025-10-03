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
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from rich.console import Console
from rich.markdown import Markdown

import readline

from traceloop.sdk import Traceloop

# Import OpenTelemetry for LLM observability
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    #from opentelemetry.instrumentation.auto_instrumentation import sitecustomize
    from opentelemetry.instrumentation.langchain import LangchainInstrumentor

    OPENTELEMETRY_AVAILABLE = True

    # Setup Session Id to be viewed in the Trace
    session_id = str(uuid.uuid4())

except ImportError:
    print("⚠️  OpenTelemetry not installed. Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp")
    OPENTELEMETRY_AVAILABLE = False

    # Create dummy trace objects
    class DummyTracer:
        def start_span(self, name, **kwargs):
            class DummySpan:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def set_attribute(self, key, value):
                    pass
                def set_status(self, status):
                    pass
            return DummySpan()

    trace = type('trace', (), {'get_tracer': lambda name: DummyTracer()})()

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# OpenTelemetry Initialization
# =============================================================================

def initialize_tracing():
    """Initialize OpenTelemetry for LLM observability."""
    if not OPENTELEMETRY_AVAILABLE:
        print("⚠️  OpenTelemetry not available - skipping tracing initialization")
        return False

    try:
        # Get configuration from environment
        trace_server_url = os.getenv("TRACE_SERVER_URL", "http://127.0.0.1:4317")  # gRPC endpoint
        insightfinder_user = os.getenv("TRACE_INSIGHTFINDER_USER_NAME", "")
        insightfinder_license = os.getenv("TRACE_INSIGHTFINDER_LICENSE_KEY", "")
        insightfinder_project = os.getenv("TRACE_INSIGHTFINDER_PROJECT", "")

        if not insightfinder_user or not insightfinder_license:
            print("⚠️  Missing InsightFinder credentials - skipping tracing")
            return False

        # Test connectivity to trace server first (skip for localhost)
        if not trace_server_url.startswith("http://127.0.0.1") and not trace_server_url.startswith("http://localhost"):
            try:
                import socket
                # Extract host and port from URL
                url_parts = trace_server_url.replace("http://", "").replace("https://", "")
                if ":" in url_parts:
                    host, port = url_parts.split(":", 1)
                    port = int(port)
                else:
                    host = url_parts
                    port = 4317  # default gRPC port

                # Test socket connection
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((host, port))
                sock.close()

                if result != 0:
                    print(f"⚠️  Cannot connect to trace server at {trace_server_url} - tracing will be disabled")
                    print("💡 Check if the InsightFinder trace server is running and accessible")
                    return False
            except Exception as e:
                print(f"⚠️  Cannot connect to trace server at {trace_server_url} - tracing will be disabled")
                print("💡 Check if the InsightFinder trace server is running and accessible")
                return False

        # Convert HTTP URL to gRPC endpoint (remove http:// and use gRPC format)
        grpc_endpoint = trace_server_url.replace("http://", "").replace("https://", "")

        # Enable instrumentor
        LangchainInstrumentor().instrument()

        # Set headers that the InsightFinder trace server expects
        headers_dict = {
            "ifuser": insightfinder_user,
            "iflicensekey": insightfinder_license,
            "ifproject": insightfinder_project,
            "ifsystem": os.getenv("TRACE_INSIGHTFINDER_SYSTEM_NAME", "")
        }

        # Create resource with service information
        resource = Resource.create({
            "service.name": "Multi-LLM-MCP-Chatbot",
            "service.version": "1.0.0",
            "environment": os.getenv("ENVIRONMENT", "development"),
            "mcp.server.url": os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000"),
            "insightfinder.user": insightfinder_user,
            "insightfinder.project": insightfinder_project,
            "insightfinder.license_key": insightfinder_license
        })

        # Create OTLP exporter
        exporter = OTLPSpanExporter(
            endpoint=f"http://{grpc_endpoint}",
            headers=headers_dict,
            insecure=True
        )

        # Create span processor and tracer provider
        span_processor = BatchSpanProcessor(exporter)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(span_processor)

        # Set the tracer provider
        trace.set_tracer_provider(tracer_provider)

        print("✅ OpenTelemetry initialized - LLM tracing enabled")
        return True

    except Exception as e:
        print(f"⚠️  Failed to initialize OpenTelemetry: {e}")
        return False


# Global tracer for the application
tracer = trace.get_tracer("multi-llm-mcp-chatbot")


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
            import requests
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
        """Execute the tool via SSE streaming with OpenTelemetry tracing."""
        with tracer.start_span(f"mcp_tool_{self.tool_name}") as span:
            span.set_attribute("tool.name", self.tool_name)
            span.set_attribute("tool.args", json.dumps(kwargs, default=str))

            results = []
            progress_info = None

            if self.enable_progress:
                print(f"🔧 Streaming {self.tool_name}...")

            try:
                async for event_data in self._stream_tool_execution(**kwargs):
                    event = event_data.get("event")
                    data = event_data.get("data", {})

                    if event == "tool_started":
                        span.set_attribute("tool.status", "started")
                        if self.enable_progress:
                            print(f"  ▶️  Started: {data.get('tool', 'unknown')}")

                    elif event == "partial_result":
                        batch = data.get("batch", [])
                        progress = data.get("progress", {})
                        results.extend(batch)

                        if progress:
                            span.set_attribute("tool.progress.current", progress.get("current", 0))
                            span.set_attribute("tool.progress.total", progress.get("total", 0))
                            span.set_attribute("tool.progress.percentage", progress.get("percentage", 0))

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
                        span.set_attribute("tool.status", "completed")
                        span.set_attribute("tool.result_count", len(results))
                        if self.enable_progress:
                            print(f"  🏁 Completed: {data.get('tool', 'unknown')}")
                        break

                    elif event in ["error", "tool_error"]:
                        error_msg = data.get("error", "Unknown error")
                        span.set_attribute("tool.status", "error")
                        span.set_attribute("tool.error", error_msg)
                        return f"Tool execution failed: {error_msg}"

                # Format the final result
                if results:
                    span.set_attribute("tool.result_count", len(results))
                    if len(results) == 1:
                        result = json.dumps(results[0], indent=2, default=str)
                        span.set_attribute("tool.result_size", len(result))
                        return result
                    else:
                        summary = f"Retrieved {len(results)} items"
                        if progress_info:
                            summary += f" (streamed in batches)"
                        result = f"{summary}\n\n" + json.dumps(results, indent=2, default=str)
                        span.set_attribute("tool.result_size", len(result))
                        return result
                else:
                    span.set_attribute("tool.result_count", 0)
                    return "No results returned from tool execution"

            except Exception as e:
                span.set_attribute("tool.status", "error")
                span.set_attribute("tool.error", str(e))
                raise
    
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
# InsightFinder Environment & Account Selection
# =============================================================================

class IFAccount(BaseModel):
    user_name: str
    license_key: str
    system_name: Optional[str] = None


class IFEnvironment(BaseModel):
    name: str
    api_url: str
    accounts: List[IFAccount] = Field(default_factory=list)


def load_if_environments() -> List[IFEnvironment]:
    """Load InsightFinder environments from env vars.

    Precedence (first found wins):
      1) INSIGHTFINDER_ENVS_FILE -> YAML file path
      2) INSIGHTFINDER_ENVS_YAML -> YAML string
      3) INSIGHTFINDER_ENVS      -> JSON string
      4) Simple env vars fallback

    YAML example (file or inline):
    environments:
      - name: stg
        api_url: https://stg.insightfinder.com
        accounts:
          - user_name: u1
            license_key: k1
      - name: prod
        api_url: https://app.insightfinder.com
        accounts:
          - user_name: u2
            license_key: k2

    JSON example (INSIGHTFINDER_ENVS):
    [
      {"name":"stg","api_url":"https://stg.insightfinder.com","accounts":[{"user_name":"u1","license_key":"k1"}]},
      {"name":"prod","api_url":"https://app.insightfinder.com","accounts":[{"user_name":"u2","license_key":"k2"}]}
    ]

    Falls back to single env variables if JSON not provided.
    """
    envs: List[IFEnvironment] = []

    # 1) YAML file path
    yaml_path = os.getenv("INSIGHTFINDER_ENVS_FILE", "if_envs.yaml").strip()
    if yaml_path and os.path.exists(yaml_path):
        try:
            try:
                import yaml  # type: ignore
            except Exception:
                yaml = None  # Fallback to skip if not installed
            if yaml is not None:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                candidates = data.get("environments", data) if isinstance(data, dict) else data
                if isinstance(candidates, list):
                    for item in candidates:
                        try:
                            envs.append(IFEnvironment(**item))
                        except Exception:
                            pass
                if envs:
                    return envs
        except Exception:
            # Ignore and try next source
            pass

    # 2) YAML inline string
    yaml_inline = os.getenv("INSIGHTFINDER_ENVS_YAML", "").strip()
    if yaml_inline:
        try:
            try:
                import yaml  # type: ignore
            except Exception:
                yaml = None
            if yaml is not None:
                data = yaml.safe_load(yaml_inline)
                candidates = data.get("environments", data) if isinstance(data, dict) else data
                if isinstance(candidates, list):
                    for item in candidates:
                        try:
                            envs.append(IFEnvironment(**item))
                        except Exception:
                            pass
                if envs:
                    return envs
        except Exception:
            pass

    # 3) JSON string
    raw = os.getenv("INSIGHTFINDER_ENVS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    try:
                        envs.append(IFEnvironment(**item))
                    except Exception:
                        # Skip invalid entries but continue
                        pass
        except Exception:
            # Ignore parse errors and fall back
            pass

    if not envs:
        # Fallback: derive from singular env vars, defaulting to STG if URL matches
        api_url = os.getenv("INSIGHTFINDER_API_URL", "https://stg.insightfinder.com")
        user = os.getenv("INSIGHTFINDER_USER_NAME", "")
        key = os.getenv("INSIGHTFINDER_LICENSE_KEY", "")
        name = "prod" if "app.insightfinder.com" in api_url else "stg"
        accounts = [IFAccount(user_name=user, license_key=key)] if user and key else []
        envs = [IFEnvironment(name=name, api_url=api_url, accounts=accounts)]

    return envs


def select_if_environment_and_account(console: Optional[Console] = None) -> Optional[Dict[str, str]]:
    """Interactively select IF environment and account; returns selected fields or None.

    Returns a dict with api_url, user_name, license_key, and optionally system_name.
    """
    envs = load_if_environments()
    if not envs:
        return None

    # Determine defaults if provided
    default_env_name = os.getenv("DEFAULT_IF_ENV", "").strip().lower()
    default_account_hint = os.getenv("DEFAULT_IF_ACCOUNT", "").strip()

    # Print environments
    print("\n🌐 InsightFinder environments:")
    for i, e in enumerate(envs, 1):
        acct_count = len(e.accounts)
        print(f"  {i}. {e.name} ({e.api_url}) - {acct_count} account{'s' if acct_count != 1 else ''}")

    # Resolve default index if any
    env_idx = None
    if default_env_name:
        for i, e in enumerate(envs):
            if e.name.lower() == default_env_name:
                env_idx = i
                break

    while True:
        prompt = f"Select IF environment (1-{len(envs)}{', Enter='+str(env_idx+1) if env_idx is not None else ''}): "
        choice = input(prompt).strip().lower()
        if not choice and env_idx is not None:
            selected_env = envs[env_idx]
            break
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(envs):
                selected_env = envs[idx]
                break
        # Also allow by name
        for e in envs:
            if e.name.lower() == choice:
                selected_env = e
                break
        else:
            print("❌ Invalid selection. Try again.")
            continue
        break

    # Accounts selection
    accounts = selected_env.accounts
    if not accounts:
        print("⚠️  No InsightFinder accounts configured for this environment.")
        return {"api_url": selected_env.api_url}

    print(f"\n👤 Accounts for {selected_env.name}:")
    for i, a in enumerate(accounts, 1):
        masked = a.license_key[:4] + "..." if a.license_key else ""
        print(f"  {i}. {a.user_name} ({masked})")

    # Resolve default account index if any
    acct_idx = None
    if default_account_hint:
        if default_account_hint.isdigit():
            j = int(default_account_hint) - 1
            if 0 <= j < len(accounts):
                acct_idx = j
        else:
            for j, a in enumerate(accounts):
                if a.user_name == default_account_hint:
                    acct_idx = j
                    break

    while True:
        prompt = f"Select account (1-{len(accounts)}{', Enter='+str(acct_idx+1) if acct_idx is not None else ''}): "
        choice = input(prompt).strip()
        if not choice and acct_idx is not None:
            account = accounts[acct_idx]
            break
        if choice.isdigit():
            j = int(choice) - 1
            if 0 <= j < len(accounts):
                account = accounts[j]
                break
        print("❌ Invalid selection. Try again.")

    return {
        "api_url": selected_env.api_url,
        "user_name": account.user_name,
        "license_key": account.license_key,
        "system_name": account.system_name or "",
    }


# =============================================================================
# Agent Bootstrap
# =============================================================================

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
    
    # Create ReAct agent
    return create_react_agent(llm, tools), client


# =============================================================================
# Chat Processing Functions
# =============================================================================

async def process_chat_message(agent, history: List[BaseMessage], user_input: str, llm_provider: str, model: str):
    """Process a single chat message with OpenTelemetry tracing."""
    with tracer.start_span("process_chat_message") as span:
        span.set_attribute("chat.model", model)
        span.set_attribute("chat.llm_provider", llm_provider)
        span.set_attribute("uuid", session_id)

        try:
            result = await agent.ainvoke({"messages": history})
            span.set_attribute("chat.status", "success")

            with tracer.start_as_current_span("chat-prompt-response") as pr_span:
                history = trim_history(list(result["messages"]))
                # Extract final AI message content
                model_response = next(msg for msg in reversed(history) if isinstance(msg, AIMessage))

                chat_result = {"prompt":user_input,"response":str(model_response.content)}

                pr_span.set_attribute("chat.prompt", user_input)
                pr_span.set_attribute("chat.response", str(model_response.content))
                pr_span.set_attribute("chat.result", json.dumps(chat_result))
                pr_span.set_attribute("username", os.getenv("TRACE_INSIGHTFINDER_USER_NAME", ""))
                pr_span.set_attribute("trace.entity", "chat-prompt-response")


            return result
        except Exception as e:
            span.set_attribute("chat.status", "error")
            span.set_attribute("chat.error", str(e))
            raise


# =============================================================================
# Interactive Chat Interface
# =============================================================================
async def interactive_chat():
    """Interactive chat with LLM selection and OpenTelemetry tracing."""
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

    # Select InsightFinder environment/account
    try:
        sel = select_if_environment_and_account()
        if sel:
            # Apply selection to environment variables used by headers
            if sel.get("api_url"):
                os.environ["INSIGHTFINDER_API_URL"] = sel["api_url"]
            if sel.get("user_name"):
                os.environ["INSIGHTFINDER_USER_NAME"] = sel["user_name"]
            if sel.get("license_key"):
                os.environ["INSIGHTFINDER_LICENSE_KEY"] = sel["license_key"]
            if sel.get("system_name"):
                os.environ["INSIGHTFINDER_SYSTEM_NAME"] = sel["system_name"]

            env_label = "prod" if "app.insightfinder.com" in sel.get("api_url", "") else "stg"
            print(f"✓ InsightFinder: {env_label} ({sel.get('user_name','')})")
    except Exception:
        # Non-fatal; continue with defaults
        pass
    
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
    print()

    console = Console()
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
        history = trim_history(history)  # Always trim before sending to LLM
        try:
            with tracer.start_as_current_span("agent-chat-loop") as agent_chat_loop_span:
                result = await process_chat_message(agent, history, user_input, llm_provider, selected_model)
                # Update history from agent result
                history = list(result["messages"])
                history = trim_history(history)
                # Extract final AI message content
                ai_msg = next(msg for msg in reversed(history) if isinstance(msg, AIMessage))
                md_content = str(ai_msg.content)

                md = Markdown(md_content)
                console.print(md)
            
        except Exception as err:
            # print(f"❌ Error: {err}\n")
            pass
    
    print("👋 Goodbye!")


# =============================================================================
# Main Entry Point
# =============================================================================

def print_setup_help():
    """Print setup instructions."""
    print("""
🔧 Multi-LLM MCP Client Setup with OpenTelemetry Observability:

1. Install dependencies:
   pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp

2. Copy the example environment file:
   cp llm-client/.env.example llm-client/.env

3. Edit llm-client/.env and configure:

   🤖 LLM API Keys (add the ones you want to use):
   - OPENAI_API_KEY=your_openai_key (for ChatGPT)
   - ANTHROPIC_API_KEY=your_anthropic_key (for Claude)
   - GOOGLE_API_KEY=your_google_key (for Gemini)
   - DEEPSEEK_API_KEY=your_deepseek_key (for DeepSeek)

   📊 InsightFinder Tracing:
   - INSIGHTFINDER_USER_NAME=your_username
   - INSIGHTFINDER_LICENSE_KEY=your_license_key
   - INSIGHTFINDER_PROJECT=llm-chatbot-traces
   - TRACE_SERVER_URL=http://127.0.0.1:4317

4. Start the InsightFinder trace server (see trace server documentation)

5. For Ollama (Llama), make sure Ollama is running:
   ollama serve

6. Start the MCP server:
   TRANSPORT_TYPE=http SSE_ENABLED=true python -m insightfinder_mcp_server.main

7. Run this client with OpenTelemetry tracing:
   python llm-client/main.py

🎯 Supported LLM Providers:
- OpenAI (ChatGPT): GPT-4o, GPT-4-turbo, GPT-3.5-turbo
- Anthropic (Claude): Claude-3.5-Sonnet, Claude-3-Opus, Claude-3-Haiku
- Google (Gemini): Gemini-2.0-Flash, Gemini-1.5-Pro, Gemini-1.5-Flash  
- Ollama (Llama): Local Llama models
- DeepSeek: DeepSeek-Chat, DeepSeek-Coder

📈 OpenTelemetry Tracing Features:
- Manual span creation for chat workflows
- MCP tool execution tracing with detailed attributes
- Prompt/response tracing for LLM interactions
- Direct integration with OpenTelemetry ecosystem
- Sends traces directly to InsightFinder trace server via OTLP
- Works with all LLM providers automatically
""")


if __name__ == "__main__":

    # Initialize tracing
    tracing_enabled = initialize_tracing()
    print(f"📈 LLM Tracing: {'ON' if tracing_enabled else 'OFF'}")

    try:
        asyncio.run(interactive_chat())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print_setup_help()
